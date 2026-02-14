# Task: Wildcard Pattern Matching Utility

## Goal

Implement a `match_pattern()` function in `src/apcore/utils/pattern.py` that supports wildcard matching of module IDs using `*` as a wildcard character. This is the foundational matching algorithm (Algorithm A08) used by the ACL system for caller and target pattern evaluation.

## Files Involved

- `src/apcore/utils/pattern.py` -- Implementation of `match_pattern(pattern, module_id) -> bool`
- `tests/test_pattern.py` -- Unit tests for pattern matching

## Steps

### 1. Write failing tests (TDD)

Create `tests/test_pattern.py` with test cases covering:
- Exact match: `match_pattern("foo.bar", "foo.bar")` returns True
- Exact mismatch: `match_pattern("foo.bar", "foo.baz")` returns False
- Universal wildcard: `match_pattern("*", "anything.here")` returns True
- Prefix wildcard: `match_pattern("admin.*", "admin.users")` returns True
- Prefix wildcard mismatch: `match_pattern("admin.*", "api.users")` returns False
- Suffix wildcard: `match_pattern("*.service", "auth.service")` returns True
- Infix wildcard: `match_pattern("a.*.z", "a.b.c.z")` returns True
- Multiple wildcards: `match_pattern("a.*.b.*", "a.x.b.y")` returns True
- No wildcard, no match: `match_pattern("abc", "def")` returns False
- Empty module_id: `match_pattern("*", "")` returns True

### 2. Implement `match_pattern()`

- If pattern is `"*"`, return True immediately (universal match).
- If pattern contains no `"*"`, return exact equality check.
- Split pattern by `"*"` into segments.
- If pattern does not start with `"*"`, verify module_id starts with the first segment.
- For each subsequent segment, find it in module_id starting from current position.
- If pattern does not end with `"*"`, verify module_id ends with the last segment.
- Return True if all checks pass.

### 3. Verify all tests pass

Run `pytest tests/test_pattern.py -v` and confirm 100% pass rate.

## Acceptance Criteria

- [x] `match_pattern("*", value)` returns True for any value
- [x] Exact string match works when no wildcards present
- [x] Prefix wildcards (`admin.*`) match correctly
- [x] Suffix and infix wildcards work correctly
- [x] Multiple wildcards in a single pattern are supported
- [x] Function is exported via `__all__`

## Dependencies

None -- this is the foundational utility with no internal dependencies.

## Estimated Time

1 hour
