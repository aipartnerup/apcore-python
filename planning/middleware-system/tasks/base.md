# Task: Middleware Base Class with No-Op Defaults

## Goal

Implement the `Middleware` base class that provides default no-op implementations for all three lifecycle phases (before, after, on_error), allowing subclasses to override only the methods they need without being forced to implement all of them.

## Files Involved

- `src/apcore/middleware/base.py` -- Middleware class (36 lines)
- `tests/test_middleware.py` -- Middleware base class unit tests

## Steps

1. **Define Middleware class** (TDD: test it is NOT an ABC, can be instantiated directly)
   - Plain class, not inheriting from `ABC`
   - No `@abstractmethod` decorators
   - Must be directly instantiable: `Middleware()` should not raise

2. **Implement before()** (TDD: test returns None by default)
   - Signature: `before(self, module_id: str, inputs: dict[str, Any], context: Context) -> dict[str, Any] | None`
   - Default implementation: `return None`
   - None signals "no modification" to the pipeline

3. **Implement after()** (TDD: test returns None by default)
   - Signature: `after(self, module_id: str, inputs: dict[str, Any], output: dict[str, Any], context: Context) -> dict[str, Any] | None`
   - Default implementation: `return None`
   - None signals "no modification" to the pipeline

4. **Implement on_error()** (TDD: test returns None by default)
   - Signature: `on_error(self, module_id: str, inputs: dict[str, Any], error: Exception, context: Context) -> dict[str, Any] | None`
   - Default implementation: `return None`
   - None signals "no recovery" to the pipeline

5. **Test selective override** (TDD: verify subclass can override only one method)
   - Create a subclass that overrides only `before()`
   - Verify `after()` and `on_error()` still return None

## Acceptance Criteria

- Middleware is not an ABC (no abstract methods)
- Middleware() can be instantiated directly
- All three methods (before, after, on_error) return None by default
- Subclasses can selectively override any combination of methods
- Non-overridden methods continue to return None
- Type signatures match the Context-based middleware protocol
- Import from `apcore.context` for Context type

## Dependencies

- `apcore.context.Context` (for type annotation in method signatures)

## Estimated Time

30 minutes
