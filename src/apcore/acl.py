"""ACL (Access Control List) types and implementation for apcore.

This module defines the ACLRule dataclass and the ACL class that enforces
pattern-based access control between modules.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from typing import Any

import yaml

from apcore.context import Context
from apcore.errors import ACLRuleError, ConfigNotFoundError
from apcore.utils.pattern import match_pattern

__all__ = ["ACLRule", "ACL"]


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


class ACL:
    """Access Control List with pattern-based rules and first-match-wins evaluation.

    Implements PROTOCOL_SPEC section 6 for module access control.

    Thread safety:
        Internally synchronized. All public methods (check, add_rule,
        remove_rule, reload) are safe to call concurrently.
    """

    def __init__(self, rules: list[ACLRule], default_effect: str = "deny") -> None:
        """Initialize ACL with ordered rules and a default effect.

        Args:
            rules: Ordered list of ACL rules (first match wins).
            default_effect: Effect when no rule matches ('allow' or 'deny').
        """
        self._rules: list[ACLRule] = list(rules)
        self._default_effect: str = default_effect
        self._yaml_path: str | None = None
        self.debug: bool = False
        self._logger: logging.Logger = logging.getLogger("apcore.acl")
        self._lock = threading.Lock()

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

        with open(yaml_path) as f:
            try:
                data = yaml.safe_load(f)
            except yaml.YAMLError as e:
                raise ACLRuleError(f"Invalid YAML in {yaml_path}: {e}") from e

        if not isinstance(data, dict):
            raise ACLRuleError(
                f"ACL config must be a mapping, got {type(data).__name__}"
            )

        if "rules" not in data:
            raise ACLRuleError("ACL config missing required 'rules' key")

        raw_rules = data["rules"]
        if not isinstance(raw_rules, list):
            raise ACLRuleError(
                f"'rules' must be a list, got {type(raw_rules).__name__}"
            )

        default_effect: str = data.get("default_effect", "deny")
        rules: list[ACLRule] = []

        for i, raw_rule in enumerate(raw_rules):
            if not isinstance(raw_rule, dict):
                raise ACLRuleError(
                    f"Rule {i} must be a mapping, got {type(raw_rule).__name__}"
                )

            for key in ("callers", "targets", "effect"):
                if key not in raw_rule:
                    raise ACLRuleError(f"Rule {i} missing required key '{key}'")

            effect = raw_rule["effect"]
            if effect not in ("allow", "deny"):
                raise ACLRuleError(
                    f"Rule {i} has invalid effect '{effect}', must be 'allow' or 'deny'"
                )

            callers = raw_rule["callers"]
            if not isinstance(callers, list):
                raise ACLRuleError(
                    f"Rule {i} 'callers' must be a list, got {type(callers).__name__}"
                )

            targets = raw_rule["targets"]
            if not isinstance(targets, list):
                raise ACLRuleError(
                    f"Rule {i} 'targets' must be a list, got {type(targets).__name__}"
                )

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
        effective_caller = "@external" if caller_id is None else caller_id

        with self._lock:
            rules = list(self._rules)
            default_effect = self._default_effect

        for rule in rules:
            if self._matches_rule(rule, effective_caller, target_id, context):
                decision = rule.effect == "allow"
                self._logger.debug(
                    "ACL check: caller=%s target=%s decision=%s rule=%s",
                    caller_id,
                    target_id,
                    "allow" if decision else "deny",
                    rule.description or "(no description)",
                )
                return decision

        default_decision = default_effect == "allow"
        self._logger.debug(
            "ACL check: caller=%s target=%s decision=%s rule=default",
            caller_id,
            target_id,
            "allow" if default_decision else "deny",
        )
        return default_decision

    def _match_pattern(
        self, pattern: str, value: str, context: Context | None = None
    ) -> bool:
        """Match a single pattern against a value, with special pattern handling.

        Handles @external and @system patterns locally, delegates all
        other patterns to the foundation match_pattern() utility.
        """
        if pattern == "@external":
            return value == "@external"
        if pattern == "@system":
            return (
                context is not None
                and context.identity is not None
                and context.identity.type == "system"
            )
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
        1. At least one caller pattern matches the caller (OR logic).
        2. At least one target pattern matches the target (OR logic).
        3. If conditions are present, they must all be satisfied.
        """
        caller_match = any(
            self._match_pattern(p, caller, context) for p in rule.callers
        )
        if not caller_match:
            return False

        target_match = any(
            self._match_pattern(p, target, context) for p in rule.targets
        )
        if not target_match:
            return False

        if rule.conditions is not None:
            if not self._check_conditions(rule.conditions, context):
                return False

        return True

    def _check_conditions(
        self, conditions: dict[str, Any], context: Context | None
    ) -> bool:
        """Evaluate conditional rule parameters against the execution context.

        Returns False if any condition is not satisfied.
        """
        if context is None:
            return False

        if "identity_types" in conditions:
            if (
                context.identity is None
                or context.identity.type not in conditions["identity_types"]
            ):
                return False

        if "roles" in conditions:
            if context.identity is None:
                return False
            if not set(context.identity.roles) & set(conditions["roles"]):
                return False

        if "max_call_depth" in conditions:
            if len(context.call_chain) > conditions["max_call_depth"]:
                return False

        return True

    def add_rule(self, rule: ACLRule) -> None:
        """Add a rule at position 0 (highest priority).

        Args:
            rule: The ACLRule to add.
        """
        with self._lock:
            self._rules.insert(0, rule)

    def remove_rule(self, callers: list[str], targets: list[str]) -> bool:
        """Remove the first rule matching the given callers and targets.

        Args:
            callers: The caller patterns to match.
            targets: The target patterns to match.

        Returns:
            True if a rule was found and removed, False otherwise.
        """
        with self._lock:
            for i, rule in enumerate(self._rules):
                if rule.callers == callers and rule.targets == targets:
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
