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
from collections.abc import Iterator
from typing import Any, Callable, ClassVar

import yaml

from apcore.acl_handlers import (
    ACLConditionHandler,
    ConditionOutcome,
    _as_outcome,
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

__all__ = [
    "ACLRule",
    "AuditEntry",
    "ACL",
    "ConditionOutcome",
    "ConditionValidationFinding",
]

_logger = logging.getLogger(__name__)

# Surfaces unevaluable conditions (PROTOCOL_SPEC §6.1.1) into the AuditEntry
# built for the current check() / async_check() invocation, keyed by condition
# key so the audit message can be ordered lexicographically as §6.1.1 rule 2
# requires. Installed fresh at the start of each public check so nested calls do
# not leak diagnostics across audit entries.
#
# The dict is mutated in place rather than re-``set``: a ContextVar assignment
# made inside a coroutine does not necessarily propagate back to the caller's
# context, while a mutation of the shared mapping always does.
_handler_error_var: contextvars.ContextVar[dict[str, str] | None] = contextvars.ContextVar(
    "_apcore_acl_handler_error", default=None
)


# Synthetic ``handler_error`` key used when a rule's ``conditions`` value is not
# a mapping at all, so no real condition key exists to name. Mirrors
# apcore-typescript's ``MALFORMED_CONDITIONS_KEY``. The ``$`` prefix is reserved
# by PROTOCOL_SPEC §6.1 for compound operators, so it cannot collide with a key
# a deployment registered a handler for, and it sorts ahead of ordinary keys —
# a malformed block dominates any per-key diagnostic beside it.
_MALFORMED_CONDITIONS_KEY = "$conditions"


def _record_handler_error(key: str, reason: str) -> None:
    """Record an unevaluable condition for the in-flight check(), if any.

    The first reason recorded for a key wins: the same key can be reached by
    several rules in one check, and a stable diagnostic beats a last-writer one.
    A no-op when no check is in flight, so the evaluator stays usable directly.
    """
    errors = _handler_error_var.get()
    if errors is not None and key not in errors:
        errors[key] = f"{key}: {reason}"


def _handler_error_message() -> str | None:
    """Render the recorded diagnostics for the AuditEntry, or None.

    PROTOCOL_SPEC §6.1.1 rule 2: every unevaluable condition MUST be reported,
    ordered **lexicographically by condition key** and separated by ``"; "``.
    Lexicographic rather than evaluation order because the two differ across
    languages, and the same rule set must produce the same audit line in each.
    """
    errors = _handler_error_var.get()
    if not errors:
        return None
    return "; ".join(errors[key] for key in sorted(errors))


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
    # PROTOCOL_SPEC §6.3.1: non-null IF AND ONLY IF a condition was unevaluable
    # (§6.1.1) — no registered handler, the handler raised, or an async handler
    # could not be resolved on the sync check() path. It MUST stay null for an
    # ordinary UNSATISFIED condition: that distinction is what makes "the
    # handler said no" and "no answer was obtainable" tellable apart after the
    # fact. Several unevaluable conditions in one check are joined with "; " in
    # lexicographic order of condition key.
    handler_error: str | None = None


@dataclass(frozen=True)
class ConditionValidationFinding:
    """One rule/condition-key pair that does not resolve on the sync path.

    Returned by :meth:`ACL.validate_conditions` (PROTOCOL_SPEC §6.1.2 rule 3).

    Attributes:
        rule_index: Index of the offending rule in definition order.
        condition_key: The condition key that does not resolve.
        effect: The rule's effect. A finding on a ``deny`` rule is the
            consequential one — that rule now denies every call it matches.
        sync_registered: Whether the key resolves for :meth:`ACL.check`.
        async_registered: Whether the key resolves for :meth:`ACL.async_check`.

    ``sync_registered`` and ``async_registered`` are reported separately and
    MUST NOT be collapsed into one boolean (§6.1.3): a finding with
    ``sync_registered=False, async_registered=True`` is an async-only handler —
    a working condition under ``async_check()`` and an unevaluable one under
    ``check()``.
    """

    rule_index: int
    condition_key: str
    effect: str
    sync_registered: bool
    async_registered: bool


class ACL:
    """Access Control List with pattern-based rules and first-match-wins evaluation.

    Implements PROTOCOL_SPEC section 6 for module access control.

    Surface:
        :meth:`check` / :meth:`async_check` — the decision.
        :meth:`add_rule`, :meth:`remove_rule`, :meth:`reload` — mutation.
        :attr:`default_effect`, :attr:`rules` — read-only introspection (§6.8).
        :meth:`validate_conditions` — diagnostics for unregistered condition
        keys, to run once handler registration is complete (§6.1.2).

    Thread safety:
        Internally synchronized. All public methods (check, add_rule,
        remove_rule, reload) and the read-only accessors are safe to call
        concurrently. The accessors take the same snapshot the check path does.
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
    ) -> ConditionOutcome:
        """Evaluate a ``conditions`` object on the sync path (PROTOCOL_SPEC §6.1.1).

        Returns one of three outcomes, not a boolean. A key whose handler
        answered "no" is ``UNSATISFIED``; a key for which no answer could be
        obtained at all is ``UNEVALUABLE``. Collapsing the two is the defect
        §6.1.1 exists to prevent: it made a ``deny`` rule with a misspelled
        condition key fail **open**.

        A ``conditions`` object ANDs its keys, so §6.1.1's composition table
        applies: an outright ``UNSATISFIED`` wins even if a sibling was
        unevaluable, and ``UNEVALUABLE`` otherwise propagates. Short-circuiting
        on the decisive ``UNSATISFIED`` child is permitted (and a child skipped
        that way was never evaluated, so it records no diagnostic); short-
        circuiting on an ``UNEVALUABLE`` child is NOT, because a later sibling
        may still produce the decisive answer.

        When a handler returns an awaitable that completes synchronously (no
        actual ``await`` is hit before completion — e.g., an ``async def``
        whose body is a single ``return`` statement), the result is consumed
        and used. A handler that genuinely suspends is ``UNEVALUABLE``;
        callers needing true async handlers should use :meth:`async_check`.

        Cross-language parity with apcore-rust ``ACL::evaluate_conditions``
        which polls the future once with a noop waker (sync finding A-D-023).
        """
        if not isinstance(conditions, dict):
            return cls._malformed_conditions(conditions)

        saw_unevaluable = False
        for key, value in conditions.items():
            outcome = cls._evaluate_condition(key, value, context)
            if outcome is ConditionOutcome.UNSATISFIED:
                # Decisive: an outright "no" wins the AND. Remaining keys are
                # not evaluated, and therefore record nothing.
                return ConditionOutcome.UNSATISFIED
            if outcome is ConditionOutcome.UNEVALUABLE:
                saw_unevaluable = True
        return ConditionOutcome.UNEVALUABLE if saw_unevaluable else ConditionOutcome.SATISFIED

    @classmethod
    def _malformed_conditions(cls, conditions: Any) -> ConditionOutcome:
        """Classify a non-mapping ``conditions`` value as UNEVALUABLE.

        ``ACLRule.conditions`` is annotated ``dict[str, Any] | None``, but the
        annotation binds nobody: ``ACL(rules=[...])`` and ``add_rule()`` build
        rules programmatically and never reach the YAML parser, so a scalar or a
        list arrives here intact. Iterating it raised ``AttributeError`` straight
        out of ``check()``, which the ``ACL.check`` contract forbids — ``check``
        MUST NOT raise to indicate a deny, and a malformed rule supplied by the
        host is not the unrecoverable internal failure that raising is reserved
        for.

        It is UNEVALUABLE rather than UNSATISFIED, and that is the whole point:
        a malformed block is a misconfiguration, not a handler answering "no", so
        calling it UNSATISFIED would let a ``deny`` rule fall through to the next
        rule and then to ``default_effect`` — exactly the bypass §6.1.1 exists to
        close. Parity with apcore-typescript, which records the same synthetic
        key. (apcore-rust currently returns ``true`` here — an inert ``deny``
        rule — and is corrected in a later round.)
        """
        type_name = type(conditions).__name__
        _logger.warning(
            "ACL conditions must be a mapping, got %s — unevaluable (PROTOCOL_SPEC §6.1.1): "
            "a 'deny' rule takes effect, an 'allow' rule does not grant",
            type_name,
        )
        _record_handler_error(
            _MALFORMED_CONDITIONS_KEY,
            f"ACL conditions must be a mapping, got {type_name}",
        )
        return ConditionOutcome.UNEVALUABLE

    @classmethod
    def _evaluate_condition(
        cls,
        key: str,
        value: Any,
        context: Context,
    ) -> ConditionOutcome:
        """Evaluate one condition key on the sync path.

        The three ``UNEVALUABLE`` exits here are exactly PROTOCOL_SPEC §6.1.1's
        three situations: no registered handler, the handler raised, and an
        asynchronous handler that could not be resolved synchronously.
        """
        handler = cls._condition_handlers.get(key)
        if handler is None:
            # A typo'd key (`role:` for `roles:`) is not "condition not met" —
            # no answer was obtainable, so the rule resolves toward refusing
            # access and `AuditEntry.handler_error` says why.
            _logger.warning(
                "Unknown ACL condition %r — unevaluable (PROTOCOL_SPEC §6.1.1): "
                "a 'deny' rule takes effect, an 'allow' rule does not grant",
                key,
            )
            _record_handler_error(key, "unknown ACL condition")
            return ConditionOutcome.UNEVALUABLE

        try:
            result = handler.evaluate(value, context)
        except Exception as exc:
            _logger.exception("Handler for condition %r raised — unevaluable (PROTOCOL_SPEC §6.1.1)", key)
            _record_handler_error(key, f"{type(exc).__name__}: {exc}")
            return ConditionOutcome.UNEVALUABLE

        if inspect.isawaitable(result):
            # Try to advance the coroutine one synchronous step. If the
            # coroutine returns without hitting an ``await`` (sync-only
            # body wrapped in async fn), StopIteration carries the value.
            # Otherwise it suspends — unevaluable on this path.
            try:
                result.send(None)  # type: ignore[union-attr]
            except StopIteration as stop:
                return _as_outcome(stop.value)
            except Exception as exc:
                _logger.exception(
                    "Handler for condition %r raised during sync resolution — " "unevaluable (PROTOCOL_SPEC §6.1.1)",
                    key,
                )
                _record_handler_error(key, f"{type(exc).__name__}: {exc}")
                return ConditionOutcome.UNEVALUABLE
            else:
                # Coroutine suspended — genuinely async, can't run in sync path.
                # This is a *configuration* fault, not an unmet condition, so it
                # is one of §6.1.1's three unevaluable situations (parity with
                # apcore-typescript's "Async condition … in sync context" and
                # apcore-rust's ``Poll::Pending`` arm).
                result.close()  # type: ignore[union-attr]
                _logger.warning(
                    "Async condition %r suspended in sync context — unevaluable "
                    "(PROTOCOL_SPEC §6.1.1). Use async_check() for handlers needing await.",
                    key,
                )
                _record_handler_error(key, "async condition suspended in sync context — use async_check()")
                return ConditionOutcome.UNEVALUABLE

        return _as_outcome(result)

    @classmethod
    async def _evaluate_conditions_async(
        cls,
        conditions: dict[str, Any],
        context: Context,
    ) -> ConditionOutcome:
        """Async variant. Uses async handler if registered, falls back to sync.

        Same three-valued contract and same composition rules as
        :meth:`_evaluate_conditions`. Only two of §6.1.1's three unevaluable
        situations can arise here: an unregistered key and a raising handler.
        The third is specific to the sync path.
        """
        if not isinstance(conditions, dict):
            return cls._malformed_conditions(conditions)

        saw_unevaluable = False
        for key, value in conditions.items():
            outcome = await cls._evaluate_condition_async(key, value, context)
            if outcome is ConditionOutcome.UNSATISFIED:
                return ConditionOutcome.UNSATISFIED
            if outcome is ConditionOutcome.UNEVALUABLE:
                saw_unevaluable = True
        return ConditionOutcome.UNEVALUABLE if saw_unevaluable else ConditionOutcome.SATISFIED

    @classmethod
    async def _evaluate_condition_async(
        cls,
        key: str,
        value: Any,
        context: Context,
    ) -> ConditionOutcome:
        """Evaluate one condition key on the async path."""
        # Prefer async-specific handler (e.g., _OrHandlerAsync) so compound
        # operators recurse through the async path and properly await. Falls
        # back to the sync registry per PROTOCOL_SPEC §6.1.3.
        handler = cls._async_condition_handlers.get(key) or cls._condition_handlers.get(key)
        if handler is None:
            # Same diagnostic as the sync path — an unknown key must not deny
            # silently on either one.
            _logger.warning(
                "Unknown ACL condition %r — unevaluable (PROTOCOL_SPEC §6.1.1): "
                "a 'deny' rule takes effect, an 'allow' rule does not grant",
                key,
            )
            _record_handler_error(key, "unknown ACL condition")
            return ConditionOutcome.UNEVALUABLE
        try:
            result = handler.evaluate(value, context)
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:
            _logger.exception("Handler for condition %r raised — unevaluable (PROTOCOL_SPEC §6.1.1)", key)
            _record_handler_error(key, f"{type(exc).__name__}: {exc}")
            return ConditionOutcome.UNEVALUABLE
        return _as_outcome(result)

    # -- Load-time validation of condition keys (PROTOCOL_SPEC §6.1.2) ------

    @classmethod
    def _iter_condition_keys(cls, conditions: Any) -> Iterator[str]:
        """Yield every condition key a ``conditions`` object references.

        Keys nested inside ``$or`` / ``$not`` sub-objects count: a misspelling
        one nesting level down is exactly as invisible as one at the top level.
        The compound operators themselves are yielded too — they are registered
        handlers, so they never produce a finding.
        """
        if not isinstance(conditions, dict):
            return
        for key, value in conditions.items():
            yield key
            if key == "$or" and isinstance(value, list):
                for sub in value:
                    yield from cls._iter_condition_keys(sub)
            elif key == "$not":
                yield from cls._iter_condition_keys(value)

    @classmethod
    def _unresolved_condition_keys(cls, conditions: Any) -> list[str]:
        """Referenced keys with no handler on the sync path, first-seen order."""
        unresolved: list[str] = []
        seen: set[str] = set()
        for key in cls._iter_condition_keys(conditions):
            if key in seen:
                continue
            seen.add(key)
            if key not in cls._condition_handlers:
                unresolved.append(key)
        return unresolved

    @classmethod
    def _warn_unregistered_condition_keys(cls, rules: list[ACLRule], *, base_index: int = 0) -> None:
        """Warn — never fail — for rules referencing unregistered condition keys.

        PROTOCOL_SPEC §6.1.2: ``register_condition`` writes to a runtime,
        process-wide registry and ``acl.root`` discovery commonly runs during
        framework bootstrap, ahead of application code. Loading MUST NOT fail on
        an unregistered key, but MUST warn, naming the rule index, the key, and
        the rule's ``effect`` — the ``effect`` because a misconfigured ``deny``
        rule is the consequential case. :meth:`validate_conditions` is the
        deterministic check to run once registration is complete.
        """
        for offset, rule in enumerate(rules):
            if not rule.conditions:
                continue
            for key in cls._unresolved_condition_keys(rule.conditions):
                _logger.warning(
                    "ACL rule %d (effect=%s) references condition key %r with no registered "
                    "handler; on the sync check() path it is unevaluable (PROTOCOL_SPEC §6.1.1), "
                    "so a 'deny' rule takes effect and an 'allow' rule does not grant. Register a "
                    "handler with ACL.register_condition(), or call ACL.validate_conditions() "
                    "after bootstrap to assert on this.",
                    base_index + offset,
                    rule.effect,
                    key,
                )

    def validate_conditions(self) -> tuple[ConditionValidationFinding, ...]:
        """Report every rule referencing a condition key that does not resolve.

        The explicit validation entry point PROTOCOL_SPEC §6.1.2 rule 3
        requires. Loading an ACL only warns, because handler registration is a
        runtime, process-wide act that legitimately happens after discovery;
        this method is what a deployment calls once registration is complete, so
        it can turn a broken rule into a startup error of its own choosing.

        A finding is emitted whenever ``sync_registered`` is false — **including**
        when ``async_registered`` is true (§6.1.3 rule 2). An async-only handler
        is a working condition under :meth:`async_check` and an unevaluable one
        under :meth:`check`; an application that only ever calls ``async_check``
        may ignore such a finding, but that judgement belongs to the caller, not
        to the validator.

        Pure read: it does not mutate the ACL, register handlers, or emit an
        audit event.

        Returns:
            A possibly-empty tuple of :class:`ConditionValidationFinding`, in
            rule-definition order and then in the order keys appear in each
            rule's ``conditions`` (nested ``$or`` / ``$not`` keys included).
            Empty means every referenced key currently resolves on both paths —
            it is not a guarantee about the future, since a later
            :meth:`add_rule` can introduce a new one.
        """
        with self._lock:
            rules = list(self._rules)

        cls = type(self)
        findings: list[ConditionValidationFinding] = []
        for index, rule in enumerate(rules):
            if not rule.conditions:
                continue
            for key in cls._unresolved_condition_keys(rule.conditions):
                findings.append(
                    ConditionValidationFinding(
                        rule_index=index,
                        condition_key=key,
                        effect=rule.effect,
                        # _unresolved_condition_keys already filtered on the sync
                        # registry, so this is False by construction — spelled out
                        # rather than hard-coded so the two flags read as the
                        # independent facts §6.1.3 requires them to be.
                        sync_registered=key in cls._condition_handlers,
                        # async_check() consults the async registry and falls
                        # back to the sync one, so "resolves for async" is the
                        # union of the two.
                        async_registered=key in cls._async_condition_handlers or key in cls._condition_handlers,
                    )
                )
        return tuple(findings)

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
        # PROTOCOL_SPEC §6.5: warn the *first* time a conditional rule is
        # skipped for want of a context. Keyed by (rule index, effect) so a
        # hot path does not flood the log.
        self._warned_missing_context: set[tuple[int | None, str]] = set()

        # PROTOCOL_SPEC §6.1.2 rule 4: every entry point that accepts rules is
        # covered, direct construction included — ACL.load() and reload() both
        # funnel through here.
        type(self)._warn_unregistered_condition_keys(self._rules)

    @property
    def default_effect(self) -> str:
        """The effect applied when no rule matches — ``"allow"`` or ``"deny"``.

        Read-only accessor required by PROTOCOL_SPEC §6.8. A pure read: it
        emits no audit event and mutates nothing, and it reflects the reloaded
        file after :meth:`reload`.
        """
        with self._lock:
            return self._default_effect

    @property
    def rules(self) -> tuple[ACLRule, ...]:
        """The current rule list, in definition order.

        Read-only accessor required by PROTOCOL_SPEC §6.8. Returns an immutable
        tuple taken under the same snapshot discipline :meth:`check` uses, so a
        caller cannot reach through it to mutate the ACL's own list. Reflects
        the reloaded file after :meth:`reload`.
        """
        with self._lock:
            return tuple(self._rules)

    @classmethod
    def load(cls, yaml_path: str) -> ACL:
        """Load ACL configuration from a YAML file.

        Args:
            yaml_path: Path to the YAML configuration file.

        A rule referencing a condition key with no handler registered at load
        time is **not** an error: ``register_condition`` writes to a runtime,
        process-wide registry, and ``acl.root`` discovery commonly runs during
        framework bootstrap ahead of application code, so failing here would
        reject valid configurations on ordering alone (PROTOCOL_SPEC §6.1.2).
        A warning naming the rule index, the key and the rule's ``effect`` is
        emitted instead; :meth:`validate_conditions` is the deterministic check
        to run once registration is complete.

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

        A rule whose conditions cannot be **evaluated** at all — no registered
        handler, a handler that raised, or an async handler on this sync path —
        resolves toward refusing access per PROTOCOL_SPEC §6.1.1: a ``deny``
        rule takes effect and the call is denied, an ``allow`` rule does not
        match and does not grant. The emitted :class:`AuditEntry` carries a
        non-null ``handler_error`` naming the key and the reason. An unevaluable
        condition never raises out of this method.

        Args:
            caller_id: The calling module ID, or None for external calls.
            target_id: The target module ID being called.
            context: Optional execution context for conditional rules.

        Returns:
            True if the call is allowed, False if denied.
        """
        effective_caller, rules, default_effect, audit_logger = self._snapshot(caller_id)

        token = _handler_error_var.set({})
        try:
            matched: tuple[int, ACLRule] | None = None
            for idx, rule in enumerate(rules):
                outcome = self._matches_rule(rule, effective_caller, target_id, context, rule_index=idx)
                if outcome is ConditionOutcome.UNEVALUABLE:
                    if self._unevaluable_rule_takes_effect(rule):
                        matched = (idx, rule)
                        break
                    continue
                if outcome is ConditionOutcome.SATISFIED:
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

        Same §6.1.1 three-outcome contract as :meth:`check`. Only two of the
        three unevaluable situations can arise here — an unregistered key and a
        raising handler — since this path awaits genuine async handlers.

        Args:
            caller_id: The calling module ID, or None for external calls.
            target_id: The target module ID being called.
            context: Optional execution context for conditional rules.

        Returns:
            True if the call is allowed, False if denied.
        """
        effective_caller, rules, default_effect, audit_logger = self._snapshot(caller_id)

        token = _handler_error_var.set({})
        try:
            matched: tuple[int, ACLRule] | None = None
            for idx, rule in enumerate(rules):
                outcome = await self._matches_rule_async(rule, effective_caller, target_id, context, rule_index=idx)
                if outcome is ConditionOutcome.UNEVALUABLE:
                    if self._unevaluable_rule_takes_effect(rule):
                        matched = (idx, rule)
                        break
                    continue
                if outcome is ConditionOutcome.SATISFIED:
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
            handler_error=_handler_error_message(),
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
        *,
        rule_index: int | None = None,
    ) -> ConditionOutcome:
        """Resolve a single rule against the caller and target (three-valued).

        Returns ``SATISFIED`` when the rule matches, ``UNSATISFIED`` when it
        does not, and ``UNEVALUABLE`` when its conditions could not be
        evaluated at all (PROTOCOL_SPEC §6.1.1) — which the caller resolves
        toward refusing access.

        All of the following must hold for a ``SATISFIED``:
        1. Caller patterns match (supports compound operators $or, $not).
        2. Target patterns match (supports compound operators $or, $not).
        3. If conditions are present, they must all be satisfied.
        """
        if not self._match_patterns(rule.callers, caller, context):
            return ConditionOutcome.UNSATISFIED

        if not self._match_patterns(rule.targets, target, context):
            return ConditionOutcome.UNSATISFIED

        if rule.conditions is not None:
            return self._check_conditions(rule.conditions, context, rule, rule_index)

        return ConditionOutcome.SATISFIED

    async def _matches_rule_async(
        self,
        rule: ACLRule,
        caller: str,
        target: str,
        context: Context | None,
        *,
        rule_index: int | None = None,
    ) -> ConditionOutcome:
        """Async version of :meth:`_matches_rule`, using the async evaluator."""
        if not self._match_patterns(rule.callers, caller, context):
            return ConditionOutcome.UNSATISFIED

        if not self._match_patterns(rule.targets, target, context):
            return ConditionOutcome.UNSATISFIED

        if rule.conditions is not None:
            if context is None:
                self._warn_conditional_rule_without_context(rule_index, rule.effect)
                return ConditionOutcome.UNSATISFIED
            before = self._recorded_condition_keys()
            outcome = await self._evaluate_conditions_async(rule.conditions, context)
            if outcome is ConditionOutcome.UNEVALUABLE:
                self._warn_unevaluable_conditions(rule_index, rule.effect, before)
            return outcome

        return ConditionOutcome.SATISFIED

    def _check_conditions(
        self,
        conditions: dict[str, Any],
        context: Context | None,
        rule: ACLRule | None = None,
        rule_index: int | None = None,
    ) -> ConditionOutcome:
        """Evaluate a rule's conditions against the execution context.

        A missing context is deliberately NOT one of §6.1.1's unevaluable
        situations: calling with no context is a legitimate shape for external
        entry points, not a misconfiguration, and treating it as a failure would
        flip the decision for every ``@external`` call meeting a conditional
        ``deny`` rule (PROTOCOL_SPEC §6.5). It stays a plain non-match, with a
        warning so the consequence is at least visible.
        """
        effect = rule.effect if rule is not None else "unknown"
        if context is None:
            self._warn_conditional_rule_without_context(rule_index, effect)
            return ConditionOutcome.UNSATISFIED

        before = self._recorded_condition_keys()
        outcome = self._evaluate_conditions(conditions, context)
        if outcome is ConditionOutcome.UNEVALUABLE:
            self._warn_unevaluable_conditions(rule_index, effect, before)
        return outcome

    @staticmethod
    def _recorded_condition_keys() -> frozenset[str]:
        """Snapshot the condition keys already diagnosed in this check()."""
        errors = _handler_error_var.get()
        return frozenset(errors) if errors else frozenset()

    def _warn_unevaluable_conditions(self, rule_index: int | None, effect: str, before: frozenset[str]) -> None:
        """Warn that a rule's conditions were unevaluable (§6.1.1 rule 3).

        The message names the condition key(s), the rule's index and the rule's
        ``effect``; the ``effect`` is required because a misconfigured ``deny``
        rule is the consequential case. Only keys diagnosed *by this rule* are
        listed — ``before`` filters out ones an earlier rule already reported.
        """
        keys = sorted(self._recorded_condition_keys() - before)
        self._logger.warning(
            "ACL rule %s (effect=%s) has unevaluable condition(s) %s — PROTOCOL_SPEC §6.1.1: "
            "a 'deny' rule takes effect and the call is denied, an 'allow' rule does not grant.",
            "?" if rule_index is None else rule_index,
            effect,
            ", ".join(repr(k) for k in keys) if keys else "(unreported)",
        )

    def _warn_conditional_rule_without_context(self, rule_index: int | None, effect: str) -> None:
        """Warn once that a conditional rule was skipped for want of a context (§6.5)."""
        marker = (rule_index, effect)
        if marker in self._warned_missing_context:
            return
        self._warned_missing_context.add(marker)
        self._logger.warning(
            "ACL rule %s (effect=%s) has conditions but the check supplied no context, so the "
            "rule does not match (PROTOCOL_SPEC §6.5). A conditional 'deny' rule is therefore "
            "not a backstop for context-less callers — express a backstop as an unconditional "
            "'deny' rule or as default_effect: deny.",
            "?" if rule_index is None else rule_index,
            effect,
        )

    @staticmethod
    def _unevaluable_rule_takes_effect(rule: ACLRule) -> bool:
        """Apply §6.1.1's effect rule to a rule whose conditions were unevaluable.

        Returns True when the rule takes effect (a ``deny`` rule matches and the
        call is denied), False when evaluation continues to the next rule (an
        ``allow`` rule MUST NOT grant). Either way the audit entry already
        carries ``handler_error`` and a warning has been emitted.
        """
        return rule.effect == "deny"

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

        Note:
            A condition key with no registered handler warns and does not
            raise, exactly as on :meth:`load` (PROTOCOL_SPEC §6.1.2 rule 4).
            The warning names index ``0``, where the rule lands.
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

        # PROTOCOL_SPEC §6.1.2 rule 4: runtime insertion is an entry point that
        # MUST be covered, not just file loading. The rule lands at index 0, so
        # that is the index the warning names. Warn-never-fail, as on load.
        type(self)._warn_unregistered_condition_keys([rule])

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
            # The §6.5 once-per-rule warning is keyed by rule index, which the
            # reload may have repointed at a different rule. Start fresh.
            self._warned_missing_context.clear()


# ---------------------------------------------------------------------------
# Auto-register compound operators at module load time
# ---------------------------------------------------------------------------
ACL.register_condition("$or", _OrHandler(ACL._evaluate_conditions))
ACL.register_condition("$not", _NotHandler(ACL._evaluate_conditions))

# Async-aware variants: used by async_check() so $or/$not properly await
# async sub-condition handlers (mirrors TypeScript registerAsyncCondition).
ACL.register_async_condition("$or", _OrHandlerAsync(ACL._evaluate_conditions_async))
ACL.register_async_condition("$not", _NotHandlerAsync(ACL._evaluate_conditions_async))
