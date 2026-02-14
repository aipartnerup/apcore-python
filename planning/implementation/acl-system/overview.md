# Feature: ACL System

## Overview

Pattern-based Access Control List (ACL) with first-match-wins evaluation for module access control. The system enforces which callers may invoke which target modules using wildcard patterns, special identity patterns (`@external`, `@system`), and optional conditions based on identity type, roles, and call depth. Configuration is loaded from YAML files with strict validation and supports hot-reload at runtime. All public methods are thread-safe via `threading.Lock`.

## Scope

### Included

- `ACLRule` dataclass with callers, targets, effect, description, and conditions fields
- `ACL` class with `check()` implementing first-match-wins evaluation, `add_rule()` (position 0), `remove_rule()`, and `reload()`
- `ACL.load()` classmethod for YAML configuration with strict structural validation
- Wildcard pattern matching via `match_pattern()` utility supporting `*` wildcards
- Special patterns: `@external` (no caller / external entry points), `@system` (system-type identity)
- Conditional rules: `identity_types`, `roles` (set intersection), `max_call_depth`
- Thread-safe operations via `threading.Lock` with copy-under-lock for `check()`

### Excluded

- Role hierarchy or inheritance (flat role matching only)
- Pattern negation (no `!pattern` support)
- Rule ordering beyond insertion order (no weighted priorities)

## Technology Stack

- **Language**: Python 3.10+
- **Dependencies**: PyYAML (`yaml`), stdlib (`threading`, `os`, `logging`, `dataclasses`)
- **Internal**: `apcore.context.Context`, `apcore.context.Identity`, `apcore.errors.ACLRuleError`, `apcore.errors.ConfigNotFoundError`, `apcore.utils.pattern.match_pattern`
- **Testing**: pytest

## Task Execution Order

| # | Task File | Description | Status |
|---|-----------|-------------|--------|
| 1 | [pattern-matching](./tasks/pattern-matching.md) | Wildcard pattern matching utility in `utils/pattern.py` supporting `*` wildcards | completed |
| 2 | [acl-rule](./tasks/acl-rule.md) | `ACLRule` dataclass with callers, targets, effect, description, and conditions | completed |
| 3 | [acl-core](./tasks/acl-core.md) | `ACL` class with `check()`, first-match-wins evaluation, `add_rule`/`remove_rule`, thread safety | completed |
| 4 | [yaml-loading](./tasks/yaml-loading.md) | `ACL.load()` classmethod for YAML configuration with strict validation and `reload()` | completed |
| 5 | [conditional-rules](./tasks/conditional-rules.md) | Conditional rule support: `identity_types`, `roles`, `max_call_depth` | completed |

## Progress

| Total | Completed | In Progress | Pending |
|-------|-----------|-------------|---------|
| 5     | 5         | 0           | 0       |

## Reference Documents

- [ACL System Feature Specification](../../features/acl-system.md)
