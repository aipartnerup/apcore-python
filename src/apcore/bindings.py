"""YAML binding loader for zero-code-modification module integration."""

from __future__ import annotations

import importlib
import pathlib
from typing import Any, Callable

import yaml
from pydantic import BaseModel, ConfigDict, create_model

from apcore.decorator import (
    FunctionModule,
    _generate_input_model,
    _generate_output_model,
)
from apcore.errors import (
    BindingCallableNotFoundError,
    BindingFileInvalidError,
    BindingInvalidTargetError,
    BindingModuleNotFoundError,
    BindingNotCallableError,
    BindingSchemaMissingError,
    FuncMissingReturnTypeError,
    FuncMissingTypeHintError,
)
from apcore.registry import Registry

__all__ = ["BindingLoader"]

_JSON_SCHEMA_TYPE_MAP: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}

_UNSUPPORTED_KEYS = {"oneOf", "anyOf", "allOf", "$ref", "format"}


def _build_model_from_json_schema(
    schema: dict, model_name: str = "DynamicModel"
) -> type[BaseModel]:
    """Build a Pydantic model from a simple JSON Schema dict."""
    # Check for unsupported top-level features
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


class BindingLoader:
    """Loads YAML binding files and creates FunctionModule instances."""

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
            raise BindingFileInvalidError(
                file_path=file_path, reason=f"YAML parse error: {exc}"
            ) from exc

        if data is None:
            raise BindingFileInvalidError(file_path=file_path, reason="File is empty")

        if "bindings" not in data:
            raise BindingFileInvalidError(
                file_path=file_path, reason="Missing 'bindings' key"
            )

        bindings = data["bindings"]
        if not isinstance(bindings, list):
            raise BindingFileInvalidError(
                file_path=file_path, reason="'bindings' must be a list"
            )

        results: list[FunctionModule] = []
        for entry in bindings:
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
            fm = self._create_module_from_binding(entry, binding_file_dir)
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
            raise BindingFileInvalidError(
                file_path=dir_path, reason="Directory does not exist"
            )

        results: list[FunctionModule] = []
        for f in sorted(p.glob(pattern)):
            results.extend(self.load_bindings(str(f), registry))
        return results

    def resolve_target(self, target_string: str) -> Callable:
        """Resolve 'module.path:callable' to actual callable."""
        if ":" not in target_string:
            raise BindingInvalidTargetError(target=target_string)

        module_path, callable_name = target_string.split(":", 1)

        try:
            mod = importlib.import_module(module_path)
        except ImportError as exc:
            raise BindingModuleNotFoundError(module_path=module_path) from exc

        if "." in callable_name:
            class_name, method_name = callable_name.split(".", 1)
            try:
                cls = getattr(mod, class_name)
            except AttributeError as exc:
                raise BindingCallableNotFoundError(
                    callable_name=class_name, module_path=module_path
                ) from exc
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
                raise BindingCallableNotFoundError(
                    callable_name=callable_name, module_path=module_path
                ) from exc
        else:
            try:
                result = getattr(mod, callable_name)
            except AttributeError as exc:
                raise BindingCallableNotFoundError(
                    callable_name=callable_name, module_path=module_path
                ) from exc

        if not callable(result):
            raise BindingNotCallableError(target=target_string)

        return result

    def _create_module_from_binding(
        self, binding: dict, binding_file_dir: str
    ) -> FunctionModule:
        """Create a FunctionModule from a single binding entry."""
        func = self.resolve_target(binding["target"])
        module_id = binding["module_id"]

        # Determine schema mode
        if binding.get("auto_schema"):
            try:
                input_schema = _generate_input_model(func)
                output_schema = _generate_output_model(func)
            except (FuncMissingTypeHintError, FuncMissingReturnTypeError) as exc:
                raise BindingSchemaMissingError(target=binding["target"]) from exc
        elif "input_schema" in binding or "output_schema" in binding:
            input_schema_dict = binding.get("input_schema", {})
            output_schema_dict = binding.get("output_schema", {})
            input_schema = _build_model_from_json_schema(
                input_schema_dict, "InputModel"
            )
            output_schema = _build_model_from_json_schema(
                output_schema_dict, "OutputModel"
            )
        elif "schema_ref" in binding:
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
            input_schema = _build_model_from_json_schema(
                ref_data.get("input_schema", {}), "InputModel"
            )
            output_schema = _build_model_from_json_schema(
                ref_data.get("output_schema", {}), "OutputModel"
            )
        else:
            # No schema mode specified, try auto_schema as default
            try:
                input_schema = _generate_input_model(func)
                output_schema = _generate_output_model(func)
            except (FuncMissingTypeHintError, FuncMissingReturnTypeError) as exc:
                raise BindingSchemaMissingError(target=binding["target"]) from exc

        return FunctionModule(
            func=func,
            module_id=module_id,
            description=binding.get("description"),
            tags=binding.get("tags"),
            version=binding.get("version", "1.0.0"),
            input_schema=input_schema,
            output_schema=output_schema,
        )
