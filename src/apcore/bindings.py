"""YAML binding loader for zero-code-modification module integration.

Implements DECLARATIVE_CONFIG_SPEC.md §3 (Bindings YAML).
"""

from __future__ import annotations

import importlib
import logging
import pathlib
from typing import Any, Callable

import yaml
from pydantic import BaseModel, ConfigDict, create_model

from apcore.decorator import (
    FunctionModule,
    generate_input_model,
    generate_output_model,
)
from apcore.errors import (
    BindingCallableNotFoundError,
    BindingFileInvalidError,
    BindingInvalidTargetError,
    BindingModuleNotFoundError,
    BindingNotCallableError,
    BindingSchemaInferenceFailedError,
    BindingSchemaModeConflictError,
    FuncMissingReturnTypeError,
    FuncMissingTypeHintError,
)
from apcore.registry import Registry
from apcore.schema.openai_strict import assert_openai_strict_compatible

__all__ = ["BindingLoader"]

_logger = logging.getLogger(__name__)

_JSON_SCHEMA_TYPE_MAP: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}

_UNSUPPORTED_KEYS = {"oneOf", "anyOf", "allOf", "$ref", "format"}

_SUPPORTED_SPEC_VERSIONS = {"1.0"}

_AUTO_SCHEMA_VALID_STRINGS = {"true", "permissive", "strict"}


def _build_model_from_json_schema(schema: dict[str, Any], model_name: str = "DynamicModel") -> type[BaseModel]:
    """Build a Pydantic model from a simple JSON Schema dict."""
    if _UNSUPPORTED_KEYS & schema.keys():
        return create_model(model_name, __config__=ConfigDict(extra="allow"))

    properties = schema.get("properties", {})
    required = set(schema.get("required", []))

    if not properties:
        return create_model(model_name, __config__=ConfigDict(extra="allow"))

    fields: dict[str, Any] = {}
    for prop_name, prop_schema in properties.items():
        json_type = prop_schema.get("type", "string")
        python_type = _JSON_SCHEMA_TYPE_MAP.get(json_type, Any)
        if prop_name in required:
            fields[prop_name] = (python_type, ...)
        else:
            fields[prop_name] = (python_type, None)

    return create_model(model_name, **fields)


def _normalize_auto_schema(value: Any) -> str | None:
    """Coerce auto_schema YAML value to canonical string ('permissive' | 'strict') or None.

    Accepts: bool True (→ 'permissive'), bool False (→ None / disabled),
    or strings 'true'/'permissive'/'strict'. Returns None when auto mode disabled.
    Raises ValueError on invalid input.
    """
    if value is None:
        return None
    if value is True:
        return "permissive"
    if value is False:
        return None
    if isinstance(value, str) and value in _AUTO_SCHEMA_VALID_STRINGS:
        return "permissive" if value == "true" else value
    raise ValueError(f"auto_schema must be a boolean or one of {sorted(_AUTO_SCHEMA_VALID_STRINGS)}; got {value!r}")


def _detect_schema_modes(binding: dict[str, Any]) -> list[str]:
    """Return the list of schema mode keys present in the binding entry."""
    modes: list[str] = []
    if "auto_schema" in binding:
        modes.append("auto_schema")
    if "input_schema" in binding or "output_schema" in binding:
        modes.append("input_schema/output_schema")
    if "schema_ref" in binding:
        modes.append("schema_ref")
    return modes


class BindingLoader:
    """Loads YAML binding files and creates FunctionModule instances.

    See DECLARATIVE_CONFIG_SPEC.md §3.

    Args:
        trusted_package_prefixes: Optional allowlist of module-path prefixes.
            When set, ``resolve_target`` refuses to ``import_module()`` any
            target whose ``module_path`` does not start with one of these
            prefixes. Use this to restrict binding-driven code execution to
            a vetted set of packages (e.g. ``{"my_app.", "plugins."}``).
            Default ``None`` preserves the historical "import anything"
            behaviour — appropriate for trusted, first-party YAML bindings.
    """

    def __init__(
        self,
        trusted_package_prefixes: set[str] | None = None,
    ) -> None:
        self._trusted_package_prefixes: frozenset[str] | None = (
            frozenset(trusted_package_prefixes) if trusted_package_prefixes is not None else None
        )

    def load_bindings(self, file_path: str, registry: Registry) -> list[FunctionModule]:
        """Load binding file and register all modules."""
        path = pathlib.Path(file_path)
        binding_file_dir = str(path.parent)

        try:
            content = path.read_text()
        except OSError as exc:
            raise BindingFileInvalidError(file_path=file_path, reason=str(exc)) from exc

        try:
            data = yaml.safe_load(content)
        except yaml.YAMLError as exc:
            raise BindingFileInvalidError(file_path=file_path, reason=f"YAML parse error: {exc}") from exc

        if data is None:
            raise BindingFileInvalidError(file_path=file_path, reason="File is empty")

        if not isinstance(data, dict):
            raise BindingFileInvalidError(file_path=file_path, reason="Top-level must be a mapping")

        spec_version = data.get("spec_version")
        if spec_version is None:
            _logger.warning(
                "%s: spec_version missing; defaulting to '1.0'. "
                "spec_version will be mandatory in spec 1.1. "
                "See DECLARATIVE_CONFIG_SPEC.md §2.4",
                file_path,
            )
        elif spec_version not in _SUPPORTED_SPEC_VERSIONS:
            _logger.warning(
                "%s: spec_version '%s' is newer than supported (%s); proceeding best-effort.",
                file_path,
                spec_version,
                sorted(_SUPPORTED_SPEC_VERSIONS),
            )

        if "bindings" not in data:
            raise BindingFileInvalidError(file_path=file_path, reason="Missing 'bindings' key")

        bindings = data["bindings"]
        if not isinstance(bindings, list):
            raise BindingFileInvalidError(file_path=file_path, reason="'bindings' must be a list")

        results: list[FunctionModule] = []
        for entry in bindings:
            if not isinstance(entry, dict):
                raise BindingFileInvalidError(
                    file_path=file_path,
                    reason="Each binding entry must be a mapping",
                )
            if "module_id" not in entry:
                raise BindingFileInvalidError(
                    file_path=file_path,
                    reason="Binding entry missing 'module_id'",
                )
            if "target" not in entry:
                raise BindingFileInvalidError(
                    file_path=file_path,
                    reason="Binding entry missing 'target'",
                )
            fm = self._create_module_from_binding(entry, binding_file_dir, file_path=file_path)
            registry.register(entry["module_id"], fm)
            results.append(fm)

        return results

    def load_binding_dir(
        self,
        dir_path: str,
        registry: Registry,
        pattern: str = "*.binding.yaml",
    ) -> list[FunctionModule]:
        """Load all binding files matching pattern in directory."""
        p = pathlib.Path(dir_path)
        if not p.is_dir():
            raise BindingFileInvalidError(file_path=dir_path, reason="Directory does not exist")

        results: list[FunctionModule] = []
        for f in sorted(p.glob(pattern)):
            results.extend(self.load_bindings(str(f), registry))
        return results

    def resolve_target(self, target_string: str) -> Callable:
        """Resolve 'module.path:callable' to actual callable.

        When ``trusted_package_prefixes`` is set on the loader, raises
        ``BindingInvalidTargetError`` before importing if the module path
        is not on the allowlist — preventing arbitrary code execution from
        an untrusted YAML binding file.
        """
        if ":" not in target_string:
            raise BindingInvalidTargetError(target=target_string)

        module_path, callable_name = target_string.split(":", 1)

        if self._trusted_package_prefixes is not None and not any(
            module_path == prefix.rstrip(".")
            or module_path.startswith(prefix if prefix.endswith(".") else prefix + ".")
            for prefix in self._trusted_package_prefixes
        ):
            raise BindingInvalidTargetError(
                target=target_string,
            )

        try:
            mod = importlib.import_module(module_path)
        except ImportError as exc:
            raise BindingModuleNotFoundError(module_path=module_path) from exc

        if "." in callable_name:
            class_name, method_name = callable_name.split(".", 1)
            try:
                cls = getattr(mod, class_name)
            except AttributeError as exc:
                raise BindingCallableNotFoundError(callable_name=class_name, module_path=module_path) from exc
            try:
                instance = cls()
            except TypeError as exc:
                raise BindingCallableNotFoundError(
                    callable_name=callable_name,
                    module_path=module_path,
                ) from exc
            try:
                result = getattr(instance, method_name)
            except AttributeError as exc:
                raise BindingCallableNotFoundError(callable_name=callable_name, module_path=module_path) from exc
        else:
            try:
                result = getattr(mod, callable_name)
            except AttributeError as exc:
                raise BindingCallableNotFoundError(callable_name=callable_name, module_path=module_path) from exc

        if not callable(result):
            raise BindingNotCallableError(target=target_string)

        return result

    def _resolve_schema(
        self,
        binding: dict[str, Any],
        func: Callable,
        binding_file_dir: str,
        *,
        file_path: str,
        module_id: str,
    ) -> tuple[type[BaseModel], type[BaseModel]]:
        """Resolve input/output schema per DECLARATIVE_CONFIG_SPEC.md §3.4."""
        modes = _detect_schema_modes(binding)
        if len(modes) > 1:
            raise BindingSchemaModeConflictError(
                module_id=module_id,
                modes_listed=modes,
                file_path=file_path,
            )

        # Mode 1: explicit input/output schemas
        if "input_schema" in binding or "output_schema" in binding:
            if "input_schema" not in binding or "output_schema" not in binding:
                raise BindingFileInvalidError(
                    file_path=file_path,
                    reason=(
                        f"binding '{module_id}': explicit schema mode requires both 'input_schema' and 'output_schema'"
                    ),
                )
            return (
                _build_model_from_json_schema(binding["input_schema"], "InputModel"),
                _build_model_from_json_schema(binding["output_schema"], "OutputModel"),
            )

        # Mode 2: external reference
        if "schema_ref" in binding:
            ref_path = pathlib.Path(binding_file_dir) / binding["schema_ref"]
            if not ref_path.exists():
                raise BindingFileInvalidError(
                    file_path=str(ref_path),
                    reason="Schema reference file not found",
                )
            try:
                ref_data = yaml.safe_load(ref_path.read_text())
            except yaml.YAMLError as exc:
                raise BindingFileInvalidError(
                    file_path=str(ref_path),
                    reason=f"YAML parse error: {exc}",
                ) from exc
            if ref_data is None:
                ref_data = {}
            return (
                _build_model_from_json_schema(ref_data.get("input_schema", {}), "InputModel"),
                _build_model_from_json_schema(ref_data.get("output_schema", {}), "OutputModel"),
            )

        # Mode 3: explicit auto_schema OR mode 4: implicit auto (default)
        try:
            auto_mode = _normalize_auto_schema(binding.get("auto_schema"))
        except ValueError as exc:
            raise BindingFileInvalidError(
                file_path=file_path,
                reason=f"binding '{module_id}': {exc}",
            ) from exc
        # If auto_schema explicitly set to false, no mode left → error.
        if "auto_schema" in binding and auto_mode is None:
            raise BindingSchemaInferenceFailedError(
                target=binding["target"],
                module_id=module_id,
                file_path=file_path,
                remediation="auto_schema is explicitly false; provide input_schema/output_schema or schema_ref instead",
            )
        # Implicit default: auto_schema true (permissive)
        if auto_mode is None:
            auto_mode = "permissive"

        try:
            input_schema = generate_input_model(func)
            output_schema = generate_output_model(func)
        except (FuncMissingTypeHintError, FuncMissingReturnTypeError) as exc:
            raise BindingSchemaInferenceFailedError(
                target=binding["target"],
                module_id=module_id,
                file_path=file_path,
            ) from exc

        if auto_mode == "strict":
            self._enforce_strict_or_raise(input_schema, "input", module_id=module_id, file_path=file_path)
            self._enforce_strict_or_raise(output_schema, "output", module_id=module_id, file_path=file_path)

        return input_schema, output_schema

    def _enforce_strict_or_raise(
        self,
        schema_model: type[BaseModel],
        side: str,
        *,
        module_id: str,
        file_path: str,
    ) -> None:
        """Validate that the model's JSON schema is OpenAI/Anthropic-strict-compatible."""
        assert_openai_strict_compatible(
            schema_model.model_json_schema(),
            module_id=module_id,
            side=side,
            file_path=file_path,
        )

    def _create_module_from_binding(
        self,
        binding: dict,
        binding_file_dir: str,
        *,
        file_path: str,
    ) -> FunctionModule:
        """Create a FunctionModule from a single binding entry."""
        module_id = binding["module_id"]
        func = self.resolve_target(binding["target"])

        input_schema, output_schema = self._resolve_schema(
            binding,
            func,
            binding_file_dir,
            file_path=file_path,
            module_id=module_id,
        )

        return FunctionModule(
            func=func,
            module_id=module_id,
            description=binding.get("description"),
            documentation=binding.get("documentation"),
            tags=binding.get("tags"),
            version=binding.get("version", "1.0.0"),
            annotations=binding.get("annotations"),
            metadata=binding.get("metadata"),
            display=binding.get("display"),
            input_schema=input_schema,
            output_schema=output_schema,
        )
