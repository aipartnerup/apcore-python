"""Registry types: ModuleDescriptor, DiscoveredModule, DependencyInfo."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from apcore.module import ModuleAnnotations, ModuleExample

__all__ = [
    "ModuleDescriptor",
    "DiscoveredModule",
    "DependencyInfo",
]


@dataclass
class ModuleDescriptor:
    """Cross-language compatible module descriptor."""

    module_id: str
    name: str | None
    description: str
    documentation: str | None
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    version: str = "1.0.0"
    tags: list[str] = field(default_factory=list)
    annotations: ModuleAnnotations | None = None
    examples: list[ModuleExample] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DiscoveredModule:
    """Intermediate representation of a discovered module file."""

    file_path: Path
    canonical_id: str
    meta_path: Path | None = None
    namespace: str | None = None


@dataclass
class DependencyInfo:
    """Parsed dependency information from module metadata."""

    module_id: str
    version: str | None = None
    optional: bool = False
