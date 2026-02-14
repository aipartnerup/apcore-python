# Task: Conditional Rules Support

## Goal

Implement the `_check_conditions()` method in the `ACL` class to support conditional rule evaluation based on execution context. Conditions use AND logic -- all specified conditions must be satisfied for the rule to match.

## Files Involved

- `src/apcore/acl.py` -- `ACL._check_conditions()` method
- `src/apcore/context.py` -- `Context` and `Identity` dataclasses providing condition evaluation data
- `tests/test_acl.py` -- Conditional rule tests

## Steps

### 1. Write failing tests (TDD)

Create tests for:
- **identity_types**: Rule matches when `context.identity.type` is in the condition list; fails when not in list
- **roles**: Rule matches when at least one of `context.identity.roles` overlaps with condition roles (set intersection); fails when no overlap
- **max_call_depth**: Rule matches when `len(context.call_chain)` does not exceed the threshold; fails when exceeded
- **Combined conditions**: Multiple conditions specified on same rule, all must pass (AND logic)
- **No context**: Conditions fail when `context` is None
- **No identity**: `identity_types` and `roles` conditions fail when `context.identity` is None
- **Conditions in YAML**: Load rules with conditions from YAML, verify they evaluate correctly

### 2. Implement _check_conditions()

```python
def _check_conditions(self, conditions: dict[str, Any], context: Context | None) -> bool:
    if context is None:
        return False
    if "identity_types" in conditions:
        if context.identity is None or context.identity.type not in conditions["identity_types"]:
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
```

### 3. Integrate with _matches_rule()

Ensure `_matches_rule()` calls `_check_conditions()` when `rule.conditions is not None`, and returns False if conditions are not satisfied.

### 4. Verify tests pass

Run `pytest tests/test_acl.py -k "condition" -v`.

## Acceptance Criteria

- [x] `identity_types` condition: checks `context.identity.type` membership
- [x] `roles` condition: checks set intersection between identity roles and condition roles
- [x] `max_call_depth` condition: checks `len(context.call_chain)` against threshold
- [x] All conditions use AND logic (all must pass)
- [x] Returns False when context is None and conditions are present
- [x] Returns False when identity is None for identity_types/roles conditions
- [x] Conditions loaded from YAML are evaluated correctly

## Dependencies

- `acl-core` -- `_matches_rule()` must call `_check_conditions()`
- `acl-rule` -- `conditions` field on ACLRule

## Estimated Time

1.5 hours
