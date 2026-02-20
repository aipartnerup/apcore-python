"""Error hierarchy for the apcore framework."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

__all__ = [
    "ModuleError",
    "ConfigNotFoundError",
    "ConfigError",
    "ACLRuleError",
    "ACLDeniedError",
    "ModuleNotFoundError",
    "ModuleTimeoutError",
    "SchemaValidationError",
    "SchemaNotFoundError",
    "SchemaParseError",
    "SchemaCircularRefError",
    "CallDepthExceededError",
    "CircularCallError",
    "CallFrequencyExceededError",
    "InvalidInputError",
    "FuncMissingTypeHintError",
    "FuncMissingReturnTypeError",
    "BindingInvalidTargetError",
    "BindingModuleNotFoundError",
    "BindingCallableNotFoundError",
    "BindingNotCallableError",
    "BindingSchemaMissingError",
    "BindingFileInvalidError",
    "CircularDependencyError",
    "ModuleLoadError",
    "ErrorCodes",
]


class ModuleError(Exception):
    """Base error for all apcore framework errors."""

    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        cause: Exception | None = None,
        trace_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details: dict[str, Any] = details or {}
        self.cause = cause
        self.trace_id = trace_id
        self.timestamp = datetime.now(timezone.utc).isoformat()

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


class ConfigNotFoundError(ModuleError):
    """Raised when a configuration file cannot be found."""

    def __init__(self, config_path: str, **kwargs: Any) -> None:
        super().__init__(
            code="CONFIG_NOT_FOUND",
            message=f"Configuration file not found: {config_path}",
            details={"config_path": config_path},
            **kwargs,
        )


class ConfigError(ModuleError):
    """Raised when configuration is invalid."""

    def __init__(self, message: str, **kwargs: Any) -> None:
        super().__init__(code="CONFIG_INVALID", message=message, **kwargs)


class ACLRuleError(ModuleError):
    """Raised when an ACL rule is invalid."""

    def __init__(self, message: str, **kwargs: Any) -> None:
        super().__init__(code="ACL_RULE_ERROR", message=message, **kwargs)


class ACLDeniedError(ModuleError):
    """Raised when ACL denies access."""

    def __init__(self, caller_id: str | None, target_id: str, **kwargs: Any) -> None:
        super().__init__(
            code="ACL_DENIED",
            message=f"Access denied: {caller_id} -> {target_id}",
            details={"caller_id": caller_id, "target_id": target_id},
            **kwargs,
        )

    @property
    def caller_id(self) -> str | None:
        """The caller ID that was denied."""
        return self.details["caller_id"]

    @property
    def target_id(self) -> str:
        """The target module ID that was denied access to."""
        return self.details["target_id"]


class ModuleNotFoundError(ModuleError):
    """Raised when a module cannot be found."""

    def __init__(self, module_id: str, **kwargs: Any) -> None:
        super().__init__(
            code="MODULE_NOT_FOUND",
            message=f"Module not found: {module_id}",
            details={"module_id": module_id},
            **kwargs,
        )


class ModuleTimeoutError(ModuleError):
    """Raised when module execution exceeds timeout."""

    def __init__(self, module_id: str, timeout_ms: int, **kwargs: Any) -> None:
        super().__init__(
            code="MODULE_TIMEOUT",
            message=f"Module {module_id} timed out after {timeout_ms}ms",
            details={"module_id": module_id, "timeout_ms": timeout_ms},
            **kwargs,
        )

    @property
    def module_id(self) -> str:
        """The module ID that timed out."""
        return self.details["module_id"]

    @property
    def timeout_ms(self) -> int:
        """The timeout value in milliseconds."""
        return self.details["timeout_ms"]


class SchemaValidationError(ModuleError):
    """Raised when schema validation fails."""

    def __init__(
        self,
        message: str = "Schema validation failed",
        errors: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            code="SCHEMA_VALIDATION_ERROR",
            message=message,
            details={"errors": errors or []},
            **kwargs,
        )


class SchemaNotFoundError(ModuleError):
    """Raised when a schema file or reference target cannot be found."""

    def __init__(self, schema_id: str, **kwargs: Any) -> None:
        super().__init__(
            code="SCHEMA_NOT_FOUND",
            message=f"Schema not found: {schema_id}",
            details={"schema_id": schema_id},
            **kwargs,
        )


class SchemaParseError(ModuleError):
    """Raised when a schema file has invalid syntax."""

    def __init__(self, message: str, **kwargs: Any) -> None:
        super().__init__(code="SCHEMA_PARSE_ERROR", message=message, **kwargs)


class SchemaCircularRefError(ModuleError):
    """Raised when circular $ref references are detected."""

    def __init__(self, ref_path: str, **kwargs: Any) -> None:
        super().__init__(
            code="SCHEMA_CIRCULAR_REF",
            message=f"Circular reference detected: {ref_path}",
            details={"ref_path": ref_path},
            **kwargs,
        )


class CallDepthExceededError(ModuleError):
    """Raised when call chain exceeds maximum depth."""

    def __init__(self, depth: int, max_depth: int, call_chain: list[str], **kwargs: Any) -> None:
        super().__init__(
            code="CALL_DEPTH_EXCEEDED",
            message=f"Call depth {depth} exceeds maximum {max_depth}",
            details={"depth": depth, "max_depth": max_depth, "call_chain": call_chain},
            **kwargs,
        )

    @property
    def current_depth(self) -> int:
        """The current call chain depth that exceeded the limit."""
        return self.details["depth"]

    @property
    def max_depth(self) -> int:
        """The configured maximum call depth."""
        return self.details["max_depth"]


class CircularCallError(ModuleError):
    """Raised when a circular call is detected."""

    def __init__(self, module_id: str, call_chain: list[str], **kwargs: Any) -> None:
        super().__init__(
            code="CIRCULAR_CALL",
            message=f"Circular call detected for module {module_id}",
            details={"module_id": module_id, "call_chain": call_chain},
            **kwargs,
        )

    @property
    def module_id(self) -> str:
        """The module ID that formed the circular call."""
        return self.details["module_id"]


class CallFrequencyExceededError(ModuleError):
    """Raised when a module is called too many times."""

    def __init__(
        self,
        module_id: str,
        count: int,
        max_repeat: int,
        call_chain: list[str],
        **kwargs: Any,
    ) -> None:
        super().__init__(
            code="CALL_FREQUENCY_EXCEEDED",
            message=f"Module {module_id} called {count} times, max is {max_repeat}",
            details={
                "module_id": module_id,
                "count": count,
                "max_repeat": max_repeat,
                "call_chain": call_chain,
            },
            **kwargs,
        )

    @property
    def module_id(self) -> str:
        """The module ID that exceeded the frequency limit."""
        return self.details["module_id"]

    @property
    def count(self) -> int:
        """The current invocation count."""
        return self.details["count"]

    @property
    def max_repeat(self) -> int:
        """The configured maximum repeat count."""
        return self.details["max_repeat"]


class InvalidInputError(ModuleError):
    """Raised for invalid input."""

    def __init__(self, message: str = "Invalid input", **kwargs: Any) -> None:
        super().__init__(code="GENERAL_INVALID_INPUT", message=message, **kwargs)


class FuncMissingTypeHintError(ModuleError):
    """Raised when a function parameter has no type annotation or a forward reference cannot be resolved."""

    def __init__(self, *, function_name: str, parameter_name: str, **kwargs: Any) -> None:
        super().__init__(
            code="FUNC_MISSING_TYPE_HINT",
            message=(
                f"Parameter '{parameter_name}' in function '{function_name}' has no type annotation. "
                f"Add a type hint like '{parameter_name}: str'."
            ),
            details={"function_name": function_name, "parameter_name": parameter_name},
            **kwargs,
        )


class FuncMissingReturnTypeError(ModuleError):
    """Raised when a function has no return type annotation."""

    def __init__(self, *, function_name: str, **kwargs: Any) -> None:
        super().__init__(
            code="FUNC_MISSING_RETURN_TYPE",
            message=f"Function '{function_name}' has no return type annotation. Add a return type like '-> dict'.",
            details={"function_name": function_name},
            **kwargs,
        )


class BindingInvalidTargetError(ModuleError):
    """Raised when a binding target string does not contain a ':' separator."""

    def __init__(self, *, target: str, **kwargs: Any) -> None:
        super().__init__(
            code="BINDING_INVALID_TARGET",
            message=f"Invalid binding target '{target}'. Expected format: 'module.path:callable_name'.",
            details={"target": target},
            **kwargs,
        )


class BindingModuleNotFoundError(ModuleError):
    """Raised when a binding target module cannot be imported."""

    def __init__(self, *, module_path: str, **kwargs: Any) -> None:
        super().__init__(
            code="BINDING_MODULE_NOT_FOUND",
            message=f"Cannot import module '{module_path}'.",
            details={"module_path": module_path},
            **kwargs,
        )


class BindingCallableNotFoundError(ModuleError):
    """Raised when a callable cannot be found in the target module."""

    def __init__(self, *, callable_name: str, module_path: str, **kwargs: Any) -> None:
        super().__init__(
            code="BINDING_CALLABLE_NOT_FOUND",
            message=f"Cannot find callable '{callable_name}' in module '{module_path}'.",
            details={"callable_name": callable_name, "module_path": module_path},
            **kwargs,
        )


class BindingNotCallableError(ModuleError):
    """Raised when a resolved binding target is not callable."""

    def __init__(self, *, target: str, **kwargs: Any) -> None:
        super().__init__(
            code="BINDING_NOT_CALLABLE",
            message=f"Resolved target '{target}' is not callable.",
            details={"target": target},
            **kwargs,
        )


class BindingSchemaMissingError(ModuleError):
    """Raised when no schema is provided and auto-generation from type hints fails."""

    def __init__(self, *, target: str, **kwargs: Any) -> None:
        super().__init__(
            code="BINDING_SCHEMA_MISSING",
            message=f"No schema available for target '{target}'. Add type hints or provide an explicit schema.",
            details={"target": target},
            **kwargs,
        )


class BindingFileInvalidError(ModuleError):
    """Raised when a binding file has parse errors, missing required fields, or is empty."""

    def __init__(self, *, file_path: str, reason: str, **kwargs: Any) -> None:
        super().__init__(
            code="BINDING_FILE_INVALID",
            message=f"Invalid binding file '{file_path}': {reason}",
            details={"file_path": file_path, "reason": reason},
            **kwargs,
        )


class CircularDependencyError(ModuleError):
    """Raised when circular dependencies are detected among modules."""

    def __init__(self, cycle_path: list[str], **kwargs: Any) -> None:
        super().__init__(
            code="CIRCULAR_DEPENDENCY",
            message=f"Circular dependency detected: {' -> '.join(cycle_path)}",
            details={"cycle_path": cycle_path},
            **kwargs,
        )


class ModuleLoadError(ModuleError):
    """Raised when a module file cannot be loaded or resolved."""

    def __init__(self, module_id: str, reason: str, **kwargs: Any) -> None:
        super().__init__(
            code="MODULE_LOAD_ERROR",
            message=f"Failed to load module '{module_id}': {reason}",
            details={"module_id": module_id, "reason": reason},
            **kwargs,
        )


class ErrorCodes:
    """All framework error codes as constants.

    Use these instead of hardcoding error code strings.

    Example:
        if error.code == ErrorCodes.MODULE_NOT_FOUND:
            handle_not_found()
    """

    CONFIG_NOT_FOUND = "CONFIG_NOT_FOUND"
    CONFIG_INVALID = "CONFIG_INVALID"
    ACL_RULE_ERROR = "ACL_RULE_ERROR"
    ACL_DENIED = "ACL_DENIED"
    MODULE_NOT_FOUND = "MODULE_NOT_FOUND"
    MODULE_TIMEOUT = "MODULE_TIMEOUT"
    MODULE_LOAD_ERROR = "MODULE_LOAD_ERROR"
    MODULE_EXECUTE_ERROR = "MODULE_EXECUTE_ERROR"
    SCHEMA_VALIDATION_ERROR = "SCHEMA_VALIDATION_ERROR"
    SCHEMA_NOT_FOUND = "SCHEMA_NOT_FOUND"
    SCHEMA_PARSE_ERROR = "SCHEMA_PARSE_ERROR"
    SCHEMA_CIRCULAR_REF = "SCHEMA_CIRCULAR_REF"
    CALL_DEPTH_EXCEEDED = "CALL_DEPTH_EXCEEDED"
    CIRCULAR_CALL = "CIRCULAR_CALL"
    CALL_FREQUENCY_EXCEEDED = "CALL_FREQUENCY_EXCEEDED"
    GENERAL_INVALID_INPUT = "GENERAL_INVALID_INPUT"
    GENERAL_INTERNAL_ERROR = "GENERAL_INTERNAL_ERROR"
    FUNC_MISSING_TYPE_HINT = "FUNC_MISSING_TYPE_HINT"
    FUNC_MISSING_RETURN_TYPE = "FUNC_MISSING_RETURN_TYPE"
    BINDING_INVALID_TARGET = "BINDING_INVALID_TARGET"
    BINDING_MODULE_NOT_FOUND = "BINDING_MODULE_NOT_FOUND"
    BINDING_CALLABLE_NOT_FOUND = "BINDING_CALLABLE_NOT_FOUND"
    BINDING_NOT_CALLABLE = "BINDING_NOT_CALLABLE"
    BINDING_SCHEMA_MISSING = "BINDING_SCHEMA_MISSING"
    BINDING_FILE_INVALID = "BINDING_FILE_INVALID"
    CIRCULAR_DEPENDENCY = "CIRCULAR_DEPENDENCY"

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("ErrorCodes is immutable")
