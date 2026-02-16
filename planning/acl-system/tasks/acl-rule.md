# Task: ACLRule Dataclass

## Goal

Define the `ACLRule` dataclass in `src/apcore/acl.py` that represents a single access control rule. Each rule specifies caller patterns, target patterns, an effect (allow/deny), an optional description, and optional conditions for context-based evaluation.

## Files Involved

- `src/apcore/acl.py` -- `ACLRule` dataclass definition
- `tests/test_acl.py` -- Unit tests for ACLRule construction

## Steps

### 1. Write failing tests (TDD)

Create tests verifying:
- ACLRule can be constructed with required fields (`callers`, `targets`, `effect`)
- Default values: `description` defaults to `""`, `conditions` defaults to `None`
- ACLRule with all fields populated including conditions dict
- Fields are accessible as attributes

### 2. Implement ACLRule dataclass

```python
@dataclass
class ACLRule:
    callers: list[str]
    targets: list[str]
    effect: str
    description: str = ""
    conditions: dict[str, Any] | None = None
```

Key design decisions:
- Use `@dataclass` for simplicity and immutability intent
- `callers` and `targets` are lists of patterns (OR logic within each)
- `effect` is a string ("allow" or "deny") rather than an enum for YAML compatibility
- `conditions` is an optional dict to support extensible condition types

### 3. Verify tests pass

Run `pytest tests/test_acl.py -k "acl_rule" -v`.

## Acceptance Criteria

- [x] ACLRule is a dataclass with fields: callers, targets, effect, description, conditions
- [x] Default values work correctly (description="", conditions=None)
- [x] ACLRule is exported via `__all__` in `acl.py`

## Dependencies

- None for the dataclass itself (conditions evaluation is handled in task `conditional-rules`)

## Estimated Time

30 minutes
