"""Tests for the ACL (Access Control List) system."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from apcore.acl import ACL, ACLRule
from apcore.context import Context, Identity
from apcore.errors import ACLRuleError, ConfigNotFoundError


# === Pattern Matching (delegates to foundation) ===


class TestACLPatternMatching:
    """Tests for ACL-level pattern matching, including special @external/@system patterns."""

    # Test: @external pattern matches when caller_id is None
    def test_external_pattern_matches_none_caller(self) -> None:
        """@external pattern matches when the effective caller is None (external call)."""
        acl = ACL(rules=[ACLRule(callers=["@external"], targets=["*"], effect="allow")])
        assert acl.check(caller_id=None, target_id="some.module") is True

    # Test: @external pattern does NOT match when caller_id is a string
    def test_external_pattern_does_not_match_string_caller(self) -> None:
        """@external should not match when caller_id is a real module ID."""
        acl = ACL(
            rules=[ACLRule(callers=["@external"], targets=["*"], effect="allow")],
            default_effect="deny",
        )
        assert acl.check(caller_id="api.handler", target_id="some.module") is False

    # Test: @system pattern matches when identity.type == "system"
    def test_system_pattern_matches_system_identity(self) -> None:
        """@system matches when context has an identity with type='system'."""
        acl = ACL(rules=[ACLRule(callers=["@system"], targets=["*"], effect="allow")])
        ctx = Context.create(identity=Identity(id="sys_1", type="system"))
        assert acl.check(caller_id="internal.task", target_id="db.write", context=ctx) is True

    # Test: @system pattern does NOT match when identity is None
    def test_system_pattern_no_match_when_identity_none(self) -> None:
        """@system should not match when context has no identity."""
        acl = ACL(
            rules=[ACLRule(callers=["@system"], targets=["*"], effect="allow")],
            default_effect="deny",
        )
        ctx = Context.create()
        assert acl.check(caller_id="internal.task", target_id="db.write", context=ctx) is False

    # Test: @system pattern does NOT match when identity.type != "system"
    def test_system_pattern_no_match_when_identity_not_system(self) -> None:
        """@system should not match when identity type is 'user' or other non-system types."""
        acl = ACL(
            rules=[ACLRule(callers=["@system"], targets=["*"], effect="allow")],
            default_effect="deny",
        )
        ctx = Context.create(identity=Identity(id="u_123", type="user"))
        assert acl.check(caller_id="internal.task", target_id="db.write", context=ctx) is False

    # Test: exact pattern delegates to foundation match_pattern
    def test_exact_pattern_delegates_to_foundation(self) -> None:
        """Exact caller/target patterns use foundation match_pattern for matching."""
        acl = ACL(rules=[ACLRule(callers=["api.handler"], targets=["db.read"], effect="allow")])
        assert acl.check(caller_id="api.handler", target_id="db.read") is True

    # Test: wildcard "*" delegates to foundation match_pattern
    def test_wildcard_star_delegates_to_foundation(self) -> None:
        """Wildcard '*' matches any caller or target via foundation match_pattern."""
        acl = ACL(rules=[ACLRule(callers=["*"], targets=["*"], effect="allow")])
        assert acl.check(caller_id="anything", target_id="anything.else") is True

    # Test: prefix "executor.*" delegates to foundation match_pattern
    def test_prefix_wildcard_delegates_to_foundation(self) -> None:
        """Prefix wildcard patterns delegate to foundation match_pattern."""
        acl = ACL(rules=[ACLRule(callers=["executor.*"], targets=["*"], effect="allow")])
        assert acl.check(caller_id="executor.email", target_id="some.target") is True
        assert acl.check(caller_id="api.handler", target_id="some.target") is False


# === ACL.check() -- First-Match-Wins ===


class TestACLCheck:
    """Tests for first-match-wins rule evaluation."""

    # Test: first matching allow rule returns True
    def test_first_matching_allow_returns_true(self) -> None:
        """First rule that matches should be used; allow returns True."""
        acl = ACL(
            rules=[
                ACLRule(callers=["api.*"], targets=["db.*"], effect="allow"),
                ACLRule(callers=["api.*"], targets=["db.*"], effect="deny"),
            ]
        )
        assert acl.check(caller_id="api.handler", target_id="db.read") is True

    # Test: first matching deny rule returns False
    def test_first_matching_deny_returns_false(self) -> None:
        """First rule that matches should be used; deny returns False."""
        acl = ACL(
            rules=[
                ACLRule(callers=["api.*"], targets=["db.*"], effect="deny"),
                ACLRule(callers=["api.*"], targets=["db.*"], effect="allow"),
            ]
        )
        assert acl.check(caller_id="api.handler", target_id="db.read") is False

    # Test: no match returns default_effect
    def test_no_match_returns_default_effect_deny(self) -> None:
        """When no rule matches, default_effect='deny' returns False."""
        acl = ACL(rules=[], default_effect="deny")
        assert acl.check(caller_id="api.handler", target_id="db.read") is False

    # Test: default_effect="allow" returns True when no match
    def test_no_match_returns_default_effect_allow(self) -> None:
        """When no rule matches, default_effect='allow' returns True."""
        acl = ACL(rules=[], default_effect="allow")
        assert acl.check(caller_id="api.handler", target_id="db.read") is True

    # Test: default_effect="deny" returns False when no match
    def test_default_effect_deny(self) -> None:
        """Explicit default_effect='deny' returns False when no rule matches."""
        acl = ACL(
            rules=[ACLRule(callers=["other.*"], targets=["*"], effect="allow")],
            default_effect="deny",
        )
        assert acl.check(caller_id="api.handler", target_id="db.read") is False

    # Test: rules evaluated in order (first match wins, not best match)
    def test_rules_evaluated_in_order(self) -> None:
        """Rules are evaluated sequentially; first match wins regardless of specificity."""
        acl = ACL(
            rules=[
                ACLRule(callers=["*"], targets=["*"], effect="deny"),
                ACLRule(callers=["api.handler"], targets=["db.read"], effect="allow"),
            ]
        )
        # The wildcard deny rule matches first, so even the more specific allow is ignored.
        assert acl.check(caller_id="api.handler", target_id="db.read") is False


# === ACL.load() -- YAML Loading ===


class TestACLLoad:
    """Tests for loading ACL configuration from YAML files."""

    # Test: load valid YAML with rules
    def test_load_valid_yaml(self, tmp_path: Path) -> None:
        """Load a valid YAML ACL configuration file."""
        yaml_content = textwrap.dedent("""\
            version: "1.0"
            default_effect: deny
            rules:
              - callers: ["api.*"]
                targets: ["db.*"]
                effect: allow
                description: "API can access DB"
              - callers: ["*"]
                targets: ["admin.*"]
                effect: deny
        """)
        yaml_file = tmp_path / "acl.yaml"
        yaml_file.write_text(yaml_content)

        acl = ACL.load(str(yaml_file))
        assert acl.check(caller_id="api.handler", target_id="db.read") is True
        assert acl.check(caller_id="random.caller", target_id="admin.panel") is False

    # Test: load missing file raises ConfigNotFoundError
    def test_load_missing_file_raises_config_not_found(self, tmp_path: Path) -> None:
        """Loading a nonexistent file raises ConfigNotFoundError."""
        with pytest.raises(ConfigNotFoundError):
            ACL.load(str(tmp_path / "nonexistent.yaml"))

    # Test: load invalid YAML raises ACLRuleError
    def test_load_invalid_yaml_raises_acl_rule_error(self, tmp_path: Path) -> None:
        """Malformed YAML content raises ACLRuleError."""
        yaml_file = tmp_path / "bad.yaml"
        yaml_file.write_text(":::invalid yaml{{{}}}:::")
        with pytest.raises(ACLRuleError):
            ACL.load(str(yaml_file))

    # Test: load YAML with missing "rules" key raises ACLRuleError
    def test_load_yaml_missing_rules_key(self, tmp_path: Path) -> None:
        """YAML without a 'rules' key raises ACLRuleError."""
        yaml_file = tmp_path / "no_rules.yaml"
        yaml_file.write_text("version: '1.0'\ndefault_effect: deny\n")
        with pytest.raises(ACLRuleError):
            ACL.load(str(yaml_file))

    # Test: load YAML with non-list "rules" raises ACLRuleError
    def test_load_yaml_rules_not_list(self, tmp_path: Path) -> None:
        """YAML where 'rules' is not a list raises ACLRuleError."""
        yaml_file = tmp_path / "rules_dict.yaml"
        yaml_file.write_text("version: '1.0'\nrules: not_a_list\n")
        with pytest.raises(ACLRuleError):
            ACL.load(str(yaml_file))

    # Test: load YAML with rule missing "callers" raises ACLRuleError
    def test_load_yaml_rule_missing_callers(self, tmp_path: Path) -> None:
        """A rule without 'callers' key raises ACLRuleError."""
        yaml_content = textwrap.dedent("""\
            version: "1.0"
            rules:
              - targets: ["*"]
                effect: allow
        """)
        yaml_file = tmp_path / "no_callers.yaml"
        yaml_file.write_text(yaml_content)
        with pytest.raises(ACLRuleError):
            ACL.load(str(yaml_file))

    # Test: load YAML with rule missing "targets" raises ACLRuleError
    def test_load_yaml_rule_missing_targets(self, tmp_path: Path) -> None:
        """A rule without 'targets' key raises ACLRuleError."""
        yaml_content = textwrap.dedent("""\
            version: "1.0"
            rules:
              - callers: ["*"]
                effect: allow
        """)
        yaml_file = tmp_path / "no_targets.yaml"
        yaml_file.write_text(yaml_content)
        with pytest.raises(ACLRuleError):
            ACL.load(str(yaml_file))

    # Test: load YAML with rule missing "effect" raises ACLRuleError
    def test_load_yaml_rule_missing_effect(self, tmp_path: Path) -> None:
        """A rule without 'effect' key raises ACLRuleError."""
        yaml_content = textwrap.dedent("""\
            version: "1.0"
            rules:
              - callers: ["*"]
                targets: ["*"]
        """)
        yaml_file = tmp_path / "no_effect.yaml"
        yaml_file.write_text(yaml_content)
        with pytest.raises(ACLRuleError):
            ACL.load(str(yaml_file))

    # Test: load YAML with invalid effect (not allow/deny) raises ACLRuleError
    def test_load_yaml_invalid_effect(self, tmp_path: Path) -> None:
        """A rule with effect other than 'allow'/'deny' raises ACLRuleError."""
        yaml_content = textwrap.dedent("""\
            version: "1.0"
            rules:
              - callers: ["*"]
                targets: ["*"]
                effect: maybe
        """)
        yaml_file = tmp_path / "bad_effect.yaml"
        yaml_file.write_text(yaml_content)
        with pytest.raises(ACLRuleError):
            ACL.load(str(yaml_file))

    # Test: load YAML with callers as string (not list) raises ACLRuleError
    def test_load_yaml_callers_not_list(self, tmp_path: Path) -> None:
        """A rule with 'callers' as a string instead of list raises ACLRuleError."""
        yaml_content = textwrap.dedent("""\
            version: "1.0"
            rules:
              - callers: "api.*"
                targets: ["*"]
                effect: allow
        """)
        yaml_file = tmp_path / "callers_string.yaml"
        yaml_file.write_text(yaml_content)
        with pytest.raises(ACLRuleError):
            ACL.load(str(yaml_file))

    # Test: load YAML with optional description and conditions
    def test_load_yaml_with_description_and_conditions(self, tmp_path: Path) -> None:
        """Rules with optional description and conditions fields are parsed correctly."""
        yaml_content = textwrap.dedent("""\
            version: "1.0"
            default_effect: deny
            rules:
              - callers: ["api.*"]
                targets: ["db.*"]
                effect: allow
                description: "API to DB access"
                conditions:
                  identity_types: ["service"]
                  max_call_depth: 5
        """)
        yaml_file = tmp_path / "with_conditions.yaml"
        yaml_file.write_text(yaml_content)

        acl = ACL.load(str(yaml_file))
        assert len(acl._rules) == 1
        rule = acl._rules[0]
        assert rule.description == "API to DB access"
        assert rule.conditions is not None
        assert rule.conditions["identity_types"] == ["service"]
        assert rule.conditions["max_call_depth"] == 5


# === Conditional Rules ===


class TestConditionalRules:
    """Tests for conditional rule evaluation."""

    # Test: identity_types condition matches when identity.type in list
    def test_identity_types_matches(self) -> None:
        """Condition passes when context identity type is in the allowed list."""
        acl = ACL(
            rules=[
                ACLRule(
                    callers=["*"],
                    targets=["*"],
                    effect="allow",
                    conditions={"identity_types": ["service", "system"]},
                ),
            ],
            default_effect="deny",
        )
        ctx = Context.create(identity=Identity(id="svc_1", type="service"))
        assert acl.check(caller_id="caller", target_id="target", context=ctx) is True

    # Test: identity_types condition fails when identity.type not in list
    def test_identity_types_fails(self) -> None:
        """Condition fails when context identity type is not in the allowed list."""
        acl = ACL(
            rules=[
                ACLRule(
                    callers=["*"],
                    targets=["*"],
                    effect="allow",
                    conditions={"identity_types": ["service"]},
                ),
            ],
            default_effect="deny",
        )
        ctx = Context.create(identity=Identity(id="u_1", type="user"))
        assert acl.check(caller_id="caller", target_id="target", context=ctx) is False

    # Test: roles condition matches when intersection is non-empty
    def test_roles_condition_matches(self) -> None:
        """Condition passes when identity roles intersect with required roles."""
        acl = ACL(
            rules=[
                ACLRule(
                    callers=["*"],
                    targets=["*"],
                    effect="allow",
                    conditions={"roles": ["admin", "superuser"]},
                ),
            ],
            default_effect="deny",
        )
        ctx = Context.create(identity=Identity(id="u_1", type="user", roles=["admin", "reader"]))
        assert acl.check(caller_id="caller", target_id="target", context=ctx) is True

    # Test: roles condition fails when no intersection
    def test_roles_condition_fails(self) -> None:
        """Condition fails when identity roles have no intersection with required roles."""
        acl = ACL(
            rules=[
                ACLRule(
                    callers=["*"],
                    targets=["*"],
                    effect="allow",
                    conditions={"roles": ["admin"]},
                ),
            ],
            default_effect="deny",
        )
        ctx = Context.create(identity=Identity(id="u_1", type="user", roles=["reader"]))
        assert acl.check(caller_id="caller", target_id="target", context=ctx) is False

    # Test: max_call_depth condition passes when depth within limit
    def test_max_call_depth_passes(self) -> None:
        """Condition passes when call chain depth is within the limit."""
        acl = ACL(
            rules=[
                ACLRule(
                    callers=["*"],
                    targets=["*"],
                    effect="allow",
                    conditions={"max_call_depth": 5},
                ),
            ],
            default_effect="deny",
        )
        ctx = Context.create()
        ctx.call_chain = ["a", "b", "c"]  # depth 3, within limit of 5
        assert acl.check(caller_id="caller", target_id="target", context=ctx) is True

    # Test: max_call_depth condition fails when depth exceeds limit
    def test_max_call_depth_fails(self) -> None:
        """Condition fails when call chain depth exceeds the limit."""
        acl = ACL(
            rules=[
                ACLRule(
                    callers=["*"],
                    targets=["*"],
                    effect="allow",
                    conditions={"max_call_depth": 2},
                ),
            ],
            default_effect="deny",
        )
        ctx = Context.create()
        ctx.call_chain = ["a", "b", "c"]  # depth 3, exceeds limit of 2
        assert acl.check(caller_id="caller", target_id="target", context=ctx) is False

    # Test: conditions fail when context is None
    def test_conditions_fail_when_context_none(self) -> None:
        """Conditional rules fail to match when no context is provided."""
        acl = ACL(
            rules=[
                ACLRule(
                    callers=["*"],
                    targets=["*"],
                    effect="allow",
                    conditions={"identity_types": ["service"]},
                ),
            ],
            default_effect="deny",
        )
        assert acl.check(caller_id="caller", target_id="target", context=None) is False

    # Test: conditions fail when context.identity is None
    def test_conditions_fail_when_identity_none(self) -> None:
        """Conditional rules requiring identity fail when context has no identity."""
        acl = ACL(
            rules=[
                ACLRule(
                    callers=["*"],
                    targets=["*"],
                    effect="allow",
                    conditions={"identity_types": ["service"]},
                ),
            ],
            default_effect="deny",
        )
        ctx = Context.create()  # identity defaults to None
        assert acl.check(caller_id="caller", target_id="target", context=ctx) is False


# === Runtime Modification ===


class TestACLRuntimeModification:
    """Tests for runtime rule modification."""

    # Test: add_rule inserts at position 0
    def test_add_rule_inserts_at_position_0(self) -> None:
        """add_rule() inserts new rules at the highest priority (position 0)."""
        acl = ACL(rules=[ACLRule(callers=["*"], targets=["*"], effect="deny")])
        acl.add_rule(ACLRule(callers=["api.*"], targets=["db.*"], effect="allow"))
        # The new allow rule is at position 0, so it matches first.
        assert acl.check(caller_id="api.handler", target_id="db.read") is True

    # Test: remove_rule removes matching rule and returns True
    def test_remove_rule_returns_true(self) -> None:
        """remove_rule() removes a rule matching callers+targets and returns True."""
        acl = ACL(rules=[ACLRule(callers=["api.*"], targets=["db.*"], effect="allow")])
        result = acl.remove_rule(callers=["api.*"], targets=["db.*"])
        assert result is True
        assert len(acl._rules) == 0

    # Test: remove_rule returns False when no match
    def test_remove_rule_returns_false_when_no_match(self) -> None:
        """remove_rule() returns False when no rule has matching callers+targets."""
        acl = ACL(rules=[ACLRule(callers=["api.*"], targets=["db.*"], effect="allow")])
        result = acl.remove_rule(callers=["other.*"], targets=["other.*"])
        assert result is False
        assert len(acl._rules) == 1

    # Test: reload() re-reads from YAML file
    def test_reload_rereads_yaml(self, tmp_path: Path) -> None:
        """reload() re-reads the YAML file and updates rules."""
        yaml_content_v1 = textwrap.dedent("""\
            version: "1.0"
            default_effect: deny
            rules:
              - callers: ["*"]
                targets: ["*"]
                effect: deny
        """)
        yaml_file = tmp_path / "acl.yaml"
        yaml_file.write_text(yaml_content_v1)

        acl = ACL.load(str(yaml_file))
        assert acl.check(caller_id="api.handler", target_id="db.read") is False

        # Update the file
        yaml_content_v2 = textwrap.dedent("""\
            version: "1.0"
            default_effect: allow
            rules:
              - callers: ["*"]
                targets: ["*"]
                effect: allow
        """)
        yaml_file.write_text(yaml_content_v2)

        acl.reload()
        assert acl.check(caller_id="api.handler", target_id="db.read") is True


# === ACL with Context ===


class TestACLWithContext:
    """Tests for ACL check() interactions with Context."""

    # Test: check with caller_id=None uses @external
    def test_check_with_none_caller_uses_external(self) -> None:
        """When caller_id is None, effective caller becomes '@external'."""
        acl = ACL(
            rules=[ACLRule(callers=["@external"], targets=["public.*"], effect="allow")],
            default_effect="deny",
        )
        assert acl.check(caller_id=None, target_id="public.api") is True
        assert acl.check(caller_id=None, target_id="private.api") is False

    # Test: check passes context to conditional rules
    def test_check_passes_context_to_conditions(self) -> None:
        """Context is forwarded to conditional rule evaluation."""
        acl = ACL(
            rules=[
                ACLRule(
                    callers=["*"],
                    targets=["*"],
                    effect="allow",
                    conditions={"roles": ["admin"]},
                ),
            ],
            default_effect="deny",
        )
        ctx_admin = Context.create(identity=Identity(id="u_1", type="user", roles=["admin"]))
        ctx_reader = Context.create(identity=Identity(id="u_2", type="user", roles=["reader"]))
        assert acl.check(caller_id="caller", target_id="target", context=ctx_admin) is True
        assert acl.check(caller_id="caller", target_id="target", context=ctx_reader) is False
