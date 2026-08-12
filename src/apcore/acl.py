"""ACL (Access Control List) types and implementation for apcore.

This module defines the ACLRule dataclass and the ACL class that enforces
pattern-based access control between modules.
"""

from __future__ import annotations

import contextvars
import inspect
import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, ClassVar

import yaml

from apcore.acl_handlers import (
    ACLConditionHandler,
    _IdentityTypesHandler,
    _MaxCallDepthHandler,
    _NotHandler,
    _NotHandlerAsync,
    _OrHandler,
    _OrHandlerAsync,
    _RolesHandler,
)
from apcore.config import Config
from apcore.context import Context
from apcore.errors import ACLRuleError, ConfigNotFoundError
from apcore.utils.pattern import match_pattern

__all__ = ["ACLRule", "AuditEntry", "ACL"]

_logger = logging.getLogger(__name__)

# Surfaces condition-handler failures into the AuditEntry built for the current
# check() / async_check() invocation. Reset at the start of each public check so
# nested calls do not leak handler errors across audit entries.
_handler_error_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_apcore_acl_handler_error", default=None
)


@dataclass
class ACLRule:
    """A single access control rule.

    Rules are evaluated in order by the ACL system. Each rule specifies
    caller patterns, target patterns, and an effect (allow/deny).
    """

    callers: list[str]
    targets: list[str]
    effect: str
    description: str = ""
    conditions: dict[str, Any] | None = None


@dataclass(frozen=True)
class AuditEntry:
    """Structured record of an ACL check decision."""

    timestamp: str  # ISO 8601
    caller_id: str
    target_id: str
    decision: str  # "allow" or "deny"
    reason: str  # "rule_match", "default_effect", "no_rules"
    matched_rule: str | None = None  # Rule description (immutable snapshot)
    matched_rule_index: int | None = None
    identity_type: str | None = None
    roles: tuple[str, ...] = field(default_factory=tuple)
    call_depth: int | None = None
    trace_id: str | None = None
    handler_error: str | None = None  # set when a condition handler raised during evaluation


class ACL:
    """Access Control List with pattern-based rules and first-match-wins evaluation.

    Implements PROTOCOL_SPEC section 6 for module access control.

    Thread safety:
        Internally synchronized. All public methods (check, add_rule,
        remove_rule, reload) are safe to call concurrently.
    """

    _condition_handlers: ClassVar[dict[str, ACLConditionHandler]] = {
        "identity_types": _IdentityTypesHandler(),
        "roles": _RolesHandler(),
        "max_call_depth": _MaxCallDepthHandler(),
    }

    # Async condition handlers: used by _evaluate_conditions_async for compound
    # operators ($or, $not) that need to await sub-condition evaluations.
    # Falls back to _condition_handlers for keys not present here.
    _async_condition_handlers: ClassVar[dict[str, ACLConditionHandler]] = {}

    @classmethod
    def register_condition(cls, key: str, handler: ACLConditionHandler) -> None:
        """Register a condition handler. Replaces existing handler for same key."""
        cls._condition_handlers[key] = handler

    @classmethod
    def register_async_condition(cls, key: str, handler: ACLConditionHandler) -> None:
        """Register an async-aware condition handler used by async_check().

        Async handlers are used instead of the sync handler when the async
        evaluation path is active. This allows compound operators like $or/$not
        to properly await sub-condition handlers.

        Mirrors ``apcore-typescript.ACL.registerAsyncCondition``.
        """
        cls._async_condition_handlers[key] = handler

    @classmethod
    def _evaluate_conditions(
        cls,
        conditions: dict[str, Any],
        context: Context,
    ) -> bool:
        """Evaluate all conditions with AND logic. Fail-closed on unknown.

        When a handler returns an awaitable that completes synchronously (no
        actual ``await`` is hit before completion — e.g., an ``async def``
        whose body is a single ``return`` statement), the result is consumed
        and used. Genuine async handlers that need to suspend are treated as
        unsatisfied; callers should use :meth:`async_check` for those.

        Cross-language parity with apcore-rust ``ACL::evaluate_conditions``
        which polls the future once with a noop waker (sync finding A-D-023).
        """
        for key, value in conditions.items():
            handler = cls._condition_handlers.get(key)
            if handler is None:
                # Record the diagnostic, not just the log line: a typo'd key
                # (`role:` for `roles:`) denies exactly like a correctly-spelled
                # unmet condition, and `AuditEntry.handler_error` is the only
                # place the two are distinguishable. apcore-typescript records
                # on this path too (`acl.ts` "Unknown ACL condition").
                _logger.warning("Unknown ACL condition %r — treated as unsatisfied", key)
                _handler_error_var.set(f"{key}: unknown ACL condition")
                return False
            try:
                result = handler.evaluate(value, context)
            except Exception as exc:
                _logger.exception("Handler for condition %r raised — treated as unsatisfied", key)
                _handler_error_var.set(f"{key}: {type(exc).__name__}: {exc}")
                return False
            if inspect.isawaitable(result):
                # Try to advance the coroutine one synchronous step. If the
                # coroutine returns without hitting an ``await`` (sync-only
                # body wrapped in async fn), StopIteration carries the value.
                # Otherwise it suspends — fail closed.
                try:
                    result.send(None)  # type: ignore[union-attr]
                except StopIteration as stop:
                    result = stop.value
                except Exception as exc:
                    _logger.exception(
                        "Handler for condition %r raised during sync resolution — treated as unsatisfied",
                        key,
                    )
                    _handler_error_var.set(f"{key}: {type(exc).__name__}: {exc}")
                    return False
                else:
                    # Coroutine suspended — genuinely async, can't run in sync path.
                    # This is a *configuration* fault, not an unmet condition, so
                    # it belongs in the audit diagnostic (parity with
                    # apcore-typescript's "Async condition … in sync context").
                    result.close()  # type: ignore[union-attr]
                    _logger.warning(
                        "Async condition %r suspended in sync context — treated as "
                        "unsatisfied. Use async_check() for handlers needing await.",
                        key,
                    )
                    _handler_error_var.set(
                        f"{key}: async condition suspended in sync context — use async_check()"
                    )
                    return False
            if not result:
                return False
        return True

    @classmethod
    async def _evaluate_conditions_async(
        cls,
        conditions: dict[str, Any],
        context: Context,
    ) -> bool:
        """Async variant. Uses async handler if registered, falls back to sync."""
        for key, value in conditions.items():
            # Prefer async-specific handler (e.g., _OrHandlerAsync) so compound
            # operators recurse through the async path and properly await.
            handler = cls._async_condition_handlers.get(key) or cls._condition_handlers.get(key)
            if handler is None:
                # Same diagnostic as the sync path — an unknown key must not
                # deny silently on either one.
                _logger.warning("Unknown ACL condition %r — treated as unsatisfied", key)
                _handler_error_var.set(f"{key}: unknown ACL condition")
                return False
            try:
                result = handler.evaluate(value, context)
                if inspect.isawaitable(result):
                    result = await result
            except Exception as exc:
                _logger.exception("Handler for condition %r raised — treated as unsatisfied", key)
                _handler_error_var.set(f"{key}: {type(exc).__name__}: {exc}")
                return False
            if not result:
                return False
        return True

    def __init__(
        self,
        rules: list[ACLRule] | None = None,
        default_effect: str = "deny",
        *,
        audit_logger: Callable[[AuditEntry], None] | None = None,
    ) -> None:
        """Initialize ACL with ordered rules and a default effect.

        Args:
            rules: Ordered list of ACL rules (first match wins). Defaults to [].
            default_effect: Effect when no rule matches ('allow' or 'deny').
            audit_logger: Optional callback invoked with an AuditEntry for
                every check() call. Useful for structured audit trails.
        """
        if default_effect not in {"allow", "deny"}:
            raise ACLRuleError(
                f"Invalid default_effect '{default_effect}': must be 'allow' or 'deny'. "
                "Cross-language parity with apcore-typescript constructor validation (sync A-D-025)."
            )

        self._rules = list(rules) if rules is not None else []

        self._default_effect: str = default_effect
        self._yaml_path: str | None = None
        self._audit_logger: Callable[[AuditEntry], None] | None = audit_logger
        self._logger: logging.Logger = logging.getLogger(__name__)
        self._lock = threading.Lock()
        self.debug: bool = False

    @classmethod
    def load(cls, yaml_path: str) -> ACL:
        """Load ACL configuration from a YAML file.

        Args:
            yaml_path: Path to the YAML configuration file.

        Returns:
            A new ACL instance configured from the YAML file.

        Raises:
            ConfigNotFoundError: If the file does not exist.
            ACLRuleError: If the YAML is invalid or has structural errors.
        """
        if not os.path.isfile(yaml_path):
            raise ConfigNotFoundError(config_path=yaml_path)

        with open(yaml_path, encoding="utf-8") as f:
            try:
                data = yaml.safe_load(f)
            except yaml.YAMLError as e:
                raise ACLRuleError(f"Invalid YAML in {yaml_path}: {e}") from e

        if not isinstance(data, dict):
            raise ACLRuleError(f"ACL config must be a mapping, got {type(data).__name__}")

        if "rules" not in data:
            raise ACLRuleError("ACL config missing required 'rules' key")

        raw_rules = data["rules"]
        if not isinstance(raw_rules, list):
            raise ACLRuleError(f"'rules' must be a list, got {type(raw_rules).__name__}")

        default_effect: str = data.get("default_effect", "deny")
        rules: list[ACLRule] = []

        for i, raw_rule in enumerate(raw_rules):
            if not isinstance(raw_rule, dict):
                raise ACLRuleError(f"Rule {i} must be a mapping, got {type(raw_rule).__name__}")

            for key in ("callers", "targets", "effect"):
                if key not in raw_rule:
                    raise ACLRuleError(f"Rule {i} missing required key '{key}'")

            effect = raw_rule["effect"]
            if effect not in ("allow", "deny"):
                raise ACLRuleError(f"Rule {i} has invalid effect '{effect}', must be 'allow' or 'deny'")

            callers = raw_rule["callers"]
            if not isinstance(callers, list):
                raise ACLRuleError(f"Rule {i} 'callers' must be a list, got {type(callers).__name__}")

            targets = raw_rule["targets"]
            if not isinstance(targets, list):
                raise ACLRuleError(f"Rule {i} 'targets' must be a list, got {type(targets).__name__}")

            rules.append(
                ACLRule(
                    callers=callers,
                    targets=targets,
                    effect=effect,
                    description=raw_rule.get("description", ""),
                    conditions=raw_rule.get("conditions"),
                )
            )

        acl = cls(rules=rules, default_effect=default_effect)
        acl._yaml_path = yaml_path
        return acl

    @classmethod
    def discover(cls, config: Config) -> ACL | None:
        """Activate ``acl.root`` config-driven ACL discovery (D-64, Recommendation A).

        Resolves the ``acl.root`` config key (default ``"./acl"``) and loads an
        ACL from it when the path exists. The path is resolved relative to the
        directory of the config's source file when known (``config.source_path``),
        otherwise relative to the current working directory.

        ``acl.root`` is a directory by spec convention (``acl/{scope}_acl.yaml``,
        PROTOCOL_SPEC §3.1). When the resolved path is a directory, the
        conventional ``global_acl.yaml`` within it is loaded; if that file is
        absent the result is ``None`` (the missing-path no-op still holds). When
        the resolved path is itself a file, it is loaded directly. Either way the
        actual load goes through :meth:`load`.

        Critical invariant: a missing path returns ``None`` and attaches NOTHING.
        It MUST NOT synthesize an empty default-deny ACL — doing so would silently
        deny every inter-module call in every project that lacks an ``acl`` dir.
        Missing path means "no enforcement", identical to pre-D-64 behavior. The
        ``acl.default_effect`` config key only takes effect once a real ACL file
        is loaded (it is read by :meth:`load` from the ACL file itself); it never
        feeds a synthesized ACL here.

        Args:
            config: The loaded configuration to read ``acl.root`` from.

        Returns:
            A loaded :class:`ACL` when the resolved path exists, otherwise
            ``None``.

        Raises:
            ConfigNotFoundError: Never raised for a missing root (that path
                returns ``None``); only propagated from :meth:`load` if the
                path disappears between the existence check and the load.
            ACLRuleError: If the ACL file exists but is structurally invalid.
        """
        root = config.get("acl.root", Config.get_default("acl.root"))
        if root is None:
            return None

        root_path = Path(str(root))
        if not root_path.is_absolute():
            source_path = config.source_path
            if source_path is not None:
                base = Path(source_path).resolve().parent
            else:
                base = Path.cwd()
            root_path = base / root_path

        if not root_path.exists():
            # Missing path -> no enforcement. Do NOT synthesize an ACL.
            return None

        if root_path.is_dir():
            # Directory convention: acl/{scope}_acl.yaml (PROTOCOL_SPEC §3.1).
            acl_file = root_path / "global_acl.yaml"
            if not acl_file.is_file():
                # Directory present but no conventional ACL file -> no-op.
                return None
            return cls.load(str(acl_file))

        return cls.load(str(root_path))

    def check(
        self,
        caller_id: str | None,
        target_id: str,
        context: Context | None = None,
    ) -> bool:
        """Check if a call from caller_id to target_id is allowed.

        Args:
            caller_id: The calling module ID, or None for external calls.
            target_id: The target module ID being called.
            context: Optional execution context for conditional rules.

        Returns:
            True if the call is allowed, False if denied.
        """
        effective_caller, rules, default_effect, audit_logger = self._snapshot(caller_id)

        token = _handler_error_var.set(None)
        try:
            matched: tuple[int, ACLRule] | None = None
            for idx, rule in enumerate(rules):
                if self._matches_rule(rule, effective_caller, target_id, context):
                    matched = (idx, rule)
                    break

            return self._finalize_check(
                log_method="check",
                caller_id=caller_id,
                effective_caller=effective_caller,
                target_id=target_id,
                rules_present=bool(rules),
                default_effect=default_effect,
                matched=matched,
                audit_logger=audit_logger,
                context=context,
            )
        finally:
            _handler_error_var.reset(token)

    async def async_check(
        self,
        caller_id: str | None,
        target_id: str,
        context: Context | None = None,
    ) -> bool:
        """Async ACL check. Supports both sync and async condition handlers.

        Args:
            caller_id: The calling module ID, or None for external calls.
            target_id: The target module ID being called.
            context: Optional execution context for conditional rules.

        Returns:
            True if the call is allowed, False if denied.
        """
        effective_caller, rules, default_effect, audit_logger = self._snapshot(caller_id)

        token = _handler_error_var.set(None)
        try:
            matched: tuple[int, ACLRule] | None = None
            for idx, rule in enumerate(rules):
                if await self._matches_rule_async(rule, effective_caller, target_id, context):
                    matched = (idx, rule)
                    break

            return self._finalize_check(
                log_method="async_check",
                caller_id=caller_id,
                effective_caller=effective_caller,
                target_id=target_id,
                rules_present=bool(rules),
                default_effect=default_effect,
                matched=matched,
                audit_logger=audit_logger,
                context=context,
            )
        finally:
            _handler_error_var.reset(token)

    def _snapshot(self, caller_id: str | None) -> tuple[str, list[ACLRule], str, Callable[[AuditEntry], None] | None]:
        """Atomically snapshot mutable state for a check call."""
        effective_caller = "@external" if caller_id is None else caller_id
        with self._lock:
            return effective_caller, list(self._rules), self._default_effect, self._audit_logger

    def _finalize_check(
        self,
        *,
        log_method: str,
        caller_id: str | None,
        effective_caller: str,
        target_id: str,
        rules_present: bool,
        default_effect: str,
        matched: tuple[int, ACLRule] | None,
        audit_logger: Callable[[AuditEntry], None] | None,
        context: Context | None,
    ) -> bool:
        """Log the decision, emit an audit entry, and return the boolean result.

        Shared by both check() and async_check() so that audit + logging logic
        lives in exactly one place.
        """
        if matched is not None:
            matched_idx, matched_rule = matched
            decision = matched_rule.effect == "allow"
            rule_label: str = matched_rule.description or "(no description)"
            reason = "rule_match"
        else:
            matched_idx = None
            matched_rule = None
            decision = default_effect == "allow"
            rule_label = "default"
            reason = "default_effect" if rules_present else "no_rules"

        self._logger.debug(
            "ACL %s: caller_id=%s target_id=%s decision=%s rule=%s",
            log_method,
            caller_id,
            target_id,
            "allow" if decision else "deny",
            rule_label,
        )

        if audit_logger is not None:
            audit_logger(
                self._build_audit_entry(
                    caller_id=effective_caller,
                    target_id=target_id,
                    decision="allow" if decision else "deny",
                    reason=reason,
                    matched_rule=matched_rule,
                    matched_rule_index=matched_idx,
                    context=context,
                )
            )

        return decision

    def _match_patterns(self, patterns: list[str], value: str, context: Context | None = None) -> bool:
        """Match a list of patterns against a value.

        Implements compound operators ($or, $not) in pattern lists.
        """
        if not patterns:
            return False

        # Check for compound operators
        first = patterns[0]
        if first == "$or":
            return any(self._match_pattern(p, value, context) for p in patterns[1:])
        if first == "$not":
            # $not expects exactly one subsequent pattern
            if len(patterns) < 2:
                return False
            return not self._match_pattern(patterns[1], value, context)

        # Standard OR behavior for flat list
        return any(self._match_pattern(p, value, context) for p in patterns)

    def _build_audit_entry(
        self,
        *,
        caller_id: str,
        target_id: str,
        decision: str,
        reason: str,
        matched_rule: ACLRule | None,
        matched_rule_index: int | None,
        context: Context | None,
    ) -> AuditEntry:
        """Build an AuditEntry, extracting optional fields from context."""
        identity_type: str | None = None
        roles: tuple[str, ...] = ()
        call_depth: int | None = None
        trace_id: str | None = None

        if context is not None:
            trace_id = context.trace_id
            call_depth = len(context.call_chain)
            if context.identity is not None:
                identity_type = context.identity.type
                roles = tuple(context.identity.roles)

        return AuditEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            caller_id=caller_id,
            target_id=target_id,
            decision=decision,
            reason=reason,
            matched_rule=matched_rule.description if matched_rule is not None else None,
            matched_rule_index=matched_rule_index,
            identity_type=identity_type,
            roles=roles,
            call_depth=call_depth,
            trace_id=trace_id,
            handler_error=_handler_error_var.get(),
        )

    def _match_pattern(self, pattern: str, value: str, context: Context | None = None) -> bool:
        """Match a single pattern against a value, with special pattern handling.

        Handles @external and @system patterns locally, delegates all
        other patterns to the foundation match_pattern() utility.
        """
        if pattern == "@external":
            return value == "@external"
        if pattern == "@system":
            return context is not None and context.identity is not None and context.identity.type == "system"
        return match_pattern(pattern, value)

    def _matches_rule(
        self,
        rule: ACLRule,
        caller: str,
        target: str,
        context: Context | None,
    ) -> bool:
        """Check if a single rule matches the caller and target.

        All of the following must be true for a match:
        1. Caller patterns match (supports compound operators $or, $not).
        2. Target patterns match (supports compound operators $or, $not).
        3. If conditions are present, they must all be satisfied.
        """
        if not self._match_patterns(rule.callers, caller, context):
            return False

        if not self._match_patterns(rule.targets, target, context):
            return False

        if rule.conditions is not None:
            if not self._check_conditions(rule.conditions, context):
                return False

        return True

    async def _matches_rule_async(
        self,
        rule: ACLRule,
        caller: str,
        target: str,
        context: Context | None,
    ) -> bool:
        """Async version of _matches_rule. Uses _evaluate_conditions_async for conditions."""
        if not self._match_patterns(rule.callers, caller, context):
            return False

        if not self._match_patterns(rule.targets, target, context):
            return False

        if rule.conditions is not None:
            if context is None:
                return False
            if not await self._evaluate_conditions_async(rule.conditions, context):
                return False

        return True

    def _check_conditions(self, conditions: dict[str, Any], context: Context | None) -> bool:
        """Evaluate conditional rule parameters against the execution context.

        Returns False if any condition is not satisfied.
        """
        if context is None:
            return False
        return self._evaluate_conditions(conditions, context)

    def add_rule(
        self,
        rule: ACLRule | None = None,
        *,
        callers: list[str] | str | None = None,
        targets: list[str] | str | None = None,
        effect: str = "deny",
        description: str = "",
        conditions: dict[str, Any] | None = None,
    ) -> None:
        """Add a rule at position 0 (highest priority).

        Args:
            rule: Optional pre-built ACLRule.
            callers: Caller pattern(s) if *rule* is None.
            targets: Target pattern(s) if *rule* is None.
            effect: Rule effect if *rule* is None.
            description: Rule description if *rule* is None.
            conditions: Rule conditions if *rule* is None.
        """
        if rule is None:
            if callers is None or targets is None:
                raise ValueError("Must provide either 'rule' or both 'callers' and 'targets'")

            def _to_list(v: list[str] | str) -> list[str]:
                return [v] if isinstance(v, str) else list(v)

            rule = ACLRule(
                callers=_to_list(callers),
                targets=_to_list(targets),
                effect=effect,
                description=description,
                conditions=conditions,
            )

        with self._lock:
            self._rules.insert(0, rule)

    def remove_rule(
        self,
        callers: list[str],
        targets: list[str],
        conditions: dict | None = None,
    ) -> bool:
        """Remove the first rule matching the given callers, targets, and (optional) conditions.

        Args:
            callers: The caller patterns to match.
            targets: The target patterns to match.
            conditions: When provided, also disambiguate by ACLRule.conditions
                via deep equality. Two rules with identical callers+targets but
                different conditions can be selectively removed by passing the
                conditions to match. Cross-language parity with apcore-typescript
                removeRule (sync finding A-D-026).

        Returns:
            True if a rule was found and removed, False otherwise.
        """
        with self._lock:
            for i, rule in enumerate(self._rules):
                if rule.callers != callers or rule.targets != targets:
                    continue
                if conditions is not None and rule.conditions != conditions:
                    continue
                self._rules.pop(i)
                return True
            return False

    def reload(self) -> None:
        """Re-read the ACL from the original YAML file.

        Only works if the ACL was created via ACL.load().
        Raises ACLRuleError if no YAML path was stored.
        """
        with self._lock:
            yaml_path = self._yaml_path
        if yaml_path is None:
            raise ACLRuleError("Cannot reload: ACL was not loaded from a YAML file")
        reloaded = ACL.load(yaml_path)
        with self._lock:
            self._rules = reloaded._rules
            self._default_effect = reloaded._default_effect


# ---------------------------------------------------------------------------
# Auto-register compound operators at module load time
# ---------------------------------------------------------------------------
ACL.register_condition("$or", _OrHandler(ACL._evaluate_conditions))
ACL.register_condition("$not", _NotHandler(ACL._evaluate_conditions))

# Async-aware variants: used by async_check() so $or/$not properly await
# async sub-condition handlers (mirrors TypeScript registerAsyncCondition).
ACL.register_async_condition("$or", _OrHandlerAsync(ACL._evaluate_conditions_async))
ACL.register_async_condition("$not", _NotHandlerAsync(ACL._evaluate_conditions_async))
