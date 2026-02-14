# Task: YAML Configuration Loading and Reload

## Goal

Implement `ACL.load()` classmethod for loading ACL configuration from YAML files with strict validation, and `reload()` for hot-reloading the configuration at runtime. Both methods must provide clear error messages for invalid configurations.

## Files Involved

- `src/apcore/acl.py` -- `ACL.load()` and `ACL.reload()` methods
- `src/apcore/errors.py` -- `ACLRuleError`, `ConfigNotFoundError` error classes
- `tests/test_acl.py` -- YAML loading and reload tests

## Steps

### 1. Write failing tests (TDD)

Create tests for:
- **Successful loading**: Load a valid YAML file with rules, verify rules are parsed correctly
- **default_effect**: Defaults to "deny" when not specified in YAML
- **Missing file**: Raises `ConfigNotFoundError` for nonexistent path
- **Invalid YAML**: Raises `ACLRuleError` for malformed YAML
- **Missing 'rules' key**: Raises `ACLRuleError`
- **Non-list 'rules'**: Raises `ACLRuleError` when rules value is not a list
- **Missing required keys**: Raises `ACLRuleError` when callers, targets, or effect is missing from a rule
- **Invalid effect**: Raises `ACLRuleError` when effect is not "allow" or "deny"
- **Non-list callers/targets**: Raises `ACLRuleError` for non-list callers or targets
- **Reload**: Modify YAML file, call `reload()`, verify rules are updated
- **Reload without YAML path**: Raises `ACLRuleError` when ACL was not loaded from YAML

### 2. Implement ACL.load()

- Validate file existence with `os.path.isfile()`, raise `ConfigNotFoundError` if missing
- Parse YAML with `yaml.safe_load()`, catch `yaml.YAMLError` and wrap in `ACLRuleError`
- Validate top-level structure: must be dict, must have "rules" key, rules must be list
- For each rule entry: validate required keys (callers, targets, effect), validate types, validate effect value
- Construct `ACLRule` instances with optional description and conditions
- Store `_yaml_path` for later `reload()` support

### 3. Implement ACL.reload()

- Check `_yaml_path` is set (under lock), raise `ACLRuleError` if None
- Call `ACL.load()` with stored path to get new instance
- Under lock, replace `_rules` and `_default_effect` with reloaded values

### 4. Verify tests pass

Run `pytest tests/test_acl.py -k "yaml or reload or load" -v`.

## Acceptance Criteria

- [x] `ACL.load(path)` parses YAML and returns configured ACL instance
- [x] Strict validation: missing file, invalid YAML, missing keys, invalid effect, wrong types
- [x] Each validation error provides a descriptive message including rule index
- [x] `_yaml_path` is stored for reload support
- [x] `reload()` re-reads YAML file and updates rules under lock
- [x] `reload()` raises `ACLRuleError` when not loaded from YAML
- [x] Optional fields (description, conditions) are handled with defaults

## Dependencies

- `acl-core` -- ACL class must be implemented
- `acl-rule` -- ACLRule dataclass must be defined

## Estimated Time

2.5 hours
