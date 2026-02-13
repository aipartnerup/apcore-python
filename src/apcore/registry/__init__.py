"""apcore registry and module discovery system.

Provides module registration, discovery, validation, and schema export.

Usage::

    from apcore.registry import Registry

    registry = Registry(extensions_dir="./extensions")
    count = registry.discover()
"""

from __future__ import annotations

from apcore.registry.dependencies import resolve_dependencies
from apcore.registry.entry_point import resolve_entry_point, snake_to_pascal
from apcore.registry.metadata import load_id_map, load_metadata
from apcore.registry.registry import Registry
from apcore.registry.scanner import scan_extensions, scan_multi_root
from apcore.registry.types import DependencyInfo, DiscoveredModule, ModuleDescriptor
from apcore.registry.validation import validate_module

__all__ = [
    "DependencyInfo",
    "DiscoveredModule",
    "ModuleDescriptor",
    "Registry",
    "load_id_map",
    "load_metadata",
    "resolve_dependencies",
    "resolve_entry_point",
    "scan_extensions",
    "scan_multi_root",
    "snake_to_pascal",
    "validate_module",
]
