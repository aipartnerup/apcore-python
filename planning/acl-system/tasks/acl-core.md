# Task: ACL Core Class with First-Match-Wins Evaluation

## Goal

Implement the `ACL` class in `src/apcore/acl.py` with `check()`, `add_rule()`, `remove_rule()`, and internal matching helpers. The class uses first-match-wins evaluation semantics and is thread-safe via `threading.Lock`.

## Files Involved

- `src/apcore/acl.py` -- `ACL` class implementation
- `src/apcore/utils/pattern.py` -- Used by `_match_pattern()` for wildcard matching
- `src/apcore/context.py` -- `Context` and `Identity` used for `@system` pattern and conditions
- `tests/test_acl.py` -- Unit tests for ACL evaluation

## Steps

### 1. Write failing tests (TDD)

Create tests for:
- **First-match-wins**: First matching allow rule returns True; first matching deny returns False
- **Default effect**: When no rule matches, `default_effect="deny"` returns False, `default_effect="allow"` returns True
- **`@external` pattern**: `caller_id=None` maps to `@external`; string callers do not match `@external`
- **`@system` pattern**: Matches when context has system-type identity; fails for None or non-system identities
- **Wildcard patterns**: `*` matches any caller/target; `admin.*` matches `admin.users` but not `api.users`
- **add_rule()**: Inserts at position 0 (highest priority)
- **remove_rule()**: Returns True when found/removed, False when not found
- **Thread safety**: 10 threads x 200 iterations of concurrent `check()` calls without errors; concurrent `add_rule()` + `check()` without corruption

### 2. Implement ACL class

- `__init__(self, rules, default_effect="deny")` -- Store rules, default effect, create Lock
- `check(self, caller_id, target_id, context=None) -> bool` -- Copy rules under lock, iterate with first-match-wins
- `_match_pattern(self, pattern, value, context=None) -> bool` -- Handle `@external`, `@system` locally, delegate to `match_pattern()` for others
- `_matches_rule(self, rule, caller, target, context) -> bool` -- OR logic for callers, OR logic for targets, AND logic for conditions
- `add_rule(self, rule)` -- Insert at position 0 under lock
- `remove_rule(self, callers, targets) -> bool` -- Find and remove first matching rule under lock

### 3. Verify tests pass

Run `pytest tests/test_acl.py -v` and confirm all core tests pass.

## Acceptance Criteria

- [x] `check()` implements first-match-wins: first matching rule determines the decision
- [x] `caller_id=None` is treated as `@external`
- [x] `@system` pattern checks `context.identity.type == "system"`
- [x] Wildcard patterns delegated to `match_pattern()` from `utils/pattern.py`
- [x] `add_rule()` inserts at position 0 (highest priority)
- [x] `remove_rule()` removes first rule matching callers and targets, returns True/False
- [x] All public methods are thread-safe via `threading.Lock`
- [x] Debug logging for access decisions via `logging.getLogger("apcore.acl")`

## Dependencies

- `pattern-matching` -- `match_pattern()` utility must be implemented first
- `acl-rule` -- `ACLRule` dataclass must be defined

## Estimated Time

3 hours
