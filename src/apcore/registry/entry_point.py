"""Entry point resolution for discovered module files."""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from apcore.errors import ModuleLoadError

__all__ = ["resolve_entry_point", "snake_to_pascal"]


def snake_to_pascal(name: str) -> str:
    """Convert a snake_case string to PascalCase."""
    if not name:
        return ""
    return "".join(part.capitalize() for part in name.split("_"))


def _is_module_class(cls: type, loaded_module_name: str) -> bool:
    """Check if a class looks like an apcore module (duck-type detection)."""
    if cls.__module__ != loaded_module_name:
        return False
    input_schema = getattr(cls, "input_schema", None)
    output_schema = getattr(cls, "output_schema", None)
    if input_schema is None or output_schema is None:
        return False
    if not (inspect.isclass(input_schema) and issubclass(input_schema, BaseModel)):
        return False
    if not (inspect.isclass(output_schema) and issubclass(output_schema, BaseModel)):
        return False
    if not callable(getattr(cls, "execute", None)):
        return False
    return True


def _import_module_from_file(file_path: Path) -> Any:
    """Dynamically import a Python file and return the loaded module object."""
    module_name = f"apcore_ext_{file_path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, str(file_path))
    if spec is None or spec.loader is None:
        raise ModuleLoadError(
            module_id=str(file_path),
            reason=f"Cannot create import spec for {file_path}",
        )

    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as exc:
        raise ModuleLoadError(module_id=str(file_path), reason=f"Failed to import module: {exc}") from exc
    return mod


def resolve_entry_point(file_path: Path, meta: dict[str, Any] | None = None) -> type:
    """Resolve the module class from a discovered Python file.

    If meta contains an 'entry_point' key in format 'filename:ClassName',
    loads that specific class. Otherwise auto-infers the single module class.
    """
    loaded = _import_module_from_file(file_path)

    # Meta override mode
    if meta and "entry_point" in meta:
        class_name = meta["entry_point"].split(":")[-1]
        cls = getattr(loaded, class_name, None)
        if cls is None:
            raise ModuleLoadError(
                module_id=str(file_path),
                reason=f"Entry point class '{class_name}' not found in {file_path.name}",
            )
        return cls

    # Auto-infer mode
    candidates = [
        cls for _, cls in inspect.getmembers(loaded, inspect.isclass) if _is_module_class(cls, loaded.__name__)
    ]

    if len(candidates) == 1:
        return candidates[0]
    elif len(candidates) == 0:
        raise ModuleLoadError(module_id=str(file_path), reason="No Module subclass found in file")
    else:
        raise ModuleLoadError(
            module_id=str(file_path),
            reason="Ambiguous entry point: multiple Module subclasses found",
        )
