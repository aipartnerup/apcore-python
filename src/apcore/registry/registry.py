"""Central module registry for discovering, registering, and querying modules."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterator

from apcore.errors import (
    InvalidInputError,
    ModuleNotFoundError,
)
from apcore.registry.dependencies import resolve_dependencies
from apcore.registry.entry_point import resolve_entry_point
from apcore.registry.metadata import (
    load_id_map,
    load_metadata,
    merge_module_metadata,
    parse_dependencies,
)
from apcore.registry.scanner import scan_extensions, scan_multi_root
from apcore.registry.types import DependencyInfo, ModuleDescriptor
from apcore.registry.validation import validate_module

if TYPE_CHECKING:
    from apcore.config import Config

logger = logging.getLogger(__name__)

__all__ = ["Registry"]


class Registry:
    """Central module registry for discovering, registering, and querying modules."""

    def __init__(
        self,
        config: Config | None = None,
        extensions_dir: str | None = None,
        extensions_dirs: list[str | dict] | None = None,
        id_map_path: str | None = None,
    ) -> None:
        """Initialize the Registry.

        Args:
            config: Optional Config object for framework-wide settings.
            extensions_dir: Single extensions directory path.
            extensions_dirs: List of extension root configs (mutually exclusive with extensions_dir).
            id_map_path: Path to ID Map YAML file for overriding canonical IDs.

        Raises:
            InvalidInputError: If both extensions_dir and extensions_dirs are specified.
        """
        if extensions_dir is not None and extensions_dirs is not None:
            raise InvalidInputError(message="Cannot specify both extensions_dir and extensions_dirs")

        # Determine extension roots: individual params > config > defaults
        if extensions_dir is not None:
            self._extension_roots: list[dict[str, Any]] = [{"root": extensions_dir}]
        elif extensions_dirs is not None:
            self._extension_roots = [{"root": item} if isinstance(item, str) else item for item in extensions_dirs]
        elif config is not None:
            ext_root = config.get("extensions.root")
            if ext_root:
                self._extension_roots = [{"root": ext_root}]
            else:
                self._extension_roots = [{"root": "./extensions"}]
        else:
            self._extension_roots = [{"root": "./extensions"}]

        # Internal state
        self._modules: dict[str, Any] = {}
        self._module_meta: dict[str, dict[str, Any]] = {}
        self._callbacks: dict[str, list[Callable[..., Any]]] = {
            "register": [],
            "unregister": [],
        }
        self._write_lock = threading.RLock()
        self._id_map: dict[str, dict[str, Any]] = {}
        self._schema_cache: dict[str, dict[str, Any]] = {}
        self._config = config

        # Load ID map if provided
        if id_map_path is not None:
            self._id_map = load_id_map(Path(id_map_path))

    # ----- Discovery -----

    def discover(self) -> int:
        """Discover and register modules from configured extension directories.

        Returns:
            Number of modules successfully registered in this discovery pass.

        Raises:
            CircularDependencyError: If circular dependencies detected among modules.
            ConfigNotFoundError: If a configured extension root does not exist.
        """
        # Determine scan params from config
        max_depth = 8
        follow_symlinks = False
        if self._config is not None:
            max_depth = self._config.get("extensions.max_depth", 8)
            follow_symlinks = self._config.get("extensions.follow_symlinks", False)

        # Step 1: Scan extension roots
        has_namespace = any("namespace" in r for r in self._extension_roots)
        if len(self._extension_roots) > 1 or has_namespace:
            discovered = scan_multi_root(
                roots=self._extension_roots,
                max_depth=max_depth,
                follow_symlinks=follow_symlinks,
            )
        else:
            root_path = Path(self._extension_roots[0]["root"])
            discovered = scan_extensions(
                root=root_path,
                max_depth=max_depth,
                follow_symlinks=follow_symlinks,
            )

        # Step 2: Apply ID Map overrides
        if self._id_map:
            resolved_roots = [Path(r["root"]).resolve() for r in self._extension_roots]
            for dm in discovered:
                rel_path = None
                for root in resolved_roots:
                    try:
                        rel_path = str(dm.file_path.relative_to(root))
                        break
                    except ValueError:
                        continue
                if rel_path and rel_path in self._id_map:
                    map_entry = self._id_map[rel_path]
                    dm.canonical_id = map_entry["id"]

        # Step 3: Load metadata for each discovered module
        raw_metadata: dict[str, dict[str, Any]] = {}
        for dm in discovered:
            if dm.meta_path:
                raw_metadata[dm.canonical_id] = load_metadata(dm.meta_path)
            else:
                raw_metadata[dm.canonical_id] = {}

        # Step 4: Resolve entry points
        resolved_classes: dict[str, type] = {}
        for dm in discovered:
            meta = raw_metadata.get(dm.canonical_id, {})
            # Inject class override from ID map
            if dm.canonical_id in self._id_map:
                map_entry = self._id_map[dm.canonical_id]
                if map_entry.get("class"):
                    stem = dm.file_path.stem
                    meta.setdefault("entry_point", f"{stem}:{map_entry['class']}")
            try:
                cls = resolve_entry_point(dm.file_path, meta=meta)
            except Exception as e:
                logger.warning("Failed to resolve entry point for '%s': %s", dm.canonical_id, e)
                continue
            resolved_classes[dm.canonical_id] = cls

        # Step 5: Validate module classes
        valid_classes: dict[str, type] = {}
        for mod_id, cls in resolved_classes.items():
            errors = validate_module(cls)
            if errors:
                logger.warning("Module '%s' failed validation: %s", mod_id, "; ".join(errors))
                continue
            valid_classes[mod_id] = cls

        # Step 6: Collect dependencies
        modules_with_deps: list[tuple[str, list[DependencyInfo]]] = []
        for mod_id in valid_classes:
            meta = raw_metadata.get(mod_id, {})
            deps_raw = meta.get("dependencies", [])
            deps = parse_dependencies(deps_raw) if deps_raw else []
            modules_with_deps.append((mod_id, deps))

        # Step 7: Resolve dependency order (may raise CircularDependencyError)
        known_ids = {mod_id for mod_id, _ in modules_with_deps}
        load_order = resolve_dependencies(modules_with_deps, known_ids=known_ids)

        # Step 8: Instantiate and register in dependency order
        registered_count = 0
        for mod_id in load_order:
            cls = valid_classes[mod_id]
            meta = raw_metadata.get(mod_id, {})
            try:
                module = cls()
            except Exception as e:
                logger.error("Failed to instantiate module '%s': %s", mod_id, e)
                continue

            merged_meta = merge_module_metadata(cls, meta)

            with self._write_lock:
                self._modules[mod_id] = module
                self._module_meta[mod_id] = merged_meta

            # Call on_load if available
            if hasattr(module, "on_load") and callable(module.on_load):
                try:
                    module.on_load()
                except Exception as e:
                    logger.error("on_load() failed for module '%s': %s", mod_id, e)
                    with self._write_lock:
                        self._modules.pop(mod_id, None)
                        self._module_meta.pop(mod_id, None)
                    continue

            self._trigger_event("register", mod_id, module)
            registered_count += 1

        if registered_count == 0 and discovered:
            logger.warning(
                "No modules successfully registered from %d discovered files",
                len(discovered),
            )
        elif registered_count == 0:
            logger.warning("No modules discovered")

        return registered_count

    # ----- Manual Registration -----

    def register(self, module_id: str, module: Any) -> None:
        """Manually register a module instance.

        Args:
            module_id: Unique identifier for the module.
            module: Module instance to register.

        Raises:
            InvalidInputError: If module_id is already registered.
            RuntimeError: If module.on_load() fails (propagated).
        """
        if not module_id:
            raise InvalidInputError(message="module_id must be a non-empty string")

        with self._write_lock:
            if module_id in self._modules:
                raise InvalidInputError(message=f"Module already exists: {module_id}")
            self._modules[module_id] = module

        # Call on_load if available
        if hasattr(module, "on_load") and callable(module.on_load):
            try:
                module.on_load()
            except Exception:
                with self._write_lock:
                    self._modules.pop(module_id, None)
                raise

        self._trigger_event("register", module_id, module)

    def unregister(self, module_id: str) -> bool:
        """Remove a module from the registry.

        Returns False if module was not registered.
        """
        with self._write_lock:
            if module_id not in self._modules:
                return False
            module = self._modules.pop(module_id)
            self._module_meta.pop(module_id, None)
            self._schema_cache.pop(module_id, None)

        # Call on_unload if available
        if hasattr(module, "on_unload") and callable(module.on_unload):
            try:
                module.on_unload()
            except Exception as e:
                logger.error("on_unload() failed for module '%s': %s", module_id, e)

        self._trigger_event("unregister", module_id, module)
        return True

    # ----- Query Methods -----

    def get(self, module_id: str) -> Any:
        """Look up a module by ID. Returns None if not found.

        Raises:
            ModuleNotFoundError: If module_id is empty string.
        """
        if module_id == "":
            raise ModuleNotFoundError(module_id="")
        with self._write_lock:
            return self._modules.get(module_id)

    def has(self, module_id: str) -> bool:
        """Check whether a module is registered."""
        with self._write_lock:
            return module_id in self._modules

    def list(self, tags: list[str] | None = None, prefix: str | None = None) -> list[str]:
        """Return sorted list of registered module IDs, optionally filtered."""
        with self._write_lock:
            snapshot = dict(self._modules)
            meta_snapshot = dict(self._module_meta)

        ids = list(snapshot.keys())

        if prefix is not None:
            ids = [mid for mid in ids if mid.startswith(prefix)]

        if tags is not None:
            tag_set = set(tags)

            def has_all_tags(mid: str) -> bool:
                mod = snapshot[mid]
                # Check module-level tags attribute
                mod_tags = set(getattr(mod, "tags", []) or [])
                # Also check merged metadata tags
                meta_tags = meta_snapshot.get(mid, {}).get("tags", [])
                if meta_tags:
                    mod_tags.update(meta_tags)
                return tag_set.issubset(mod_tags)

            ids = [mid for mid in ids if has_all_tags(mid)]

        return sorted(ids)

    def iter(self) -> Iterator[tuple[str, Any]]:
        """Return an iterator of (module_id, module) tuples (snapshot-based)."""
        with self._write_lock:
            items = list(self._modules.items())
        return iter(items)

    @property
    def count(self) -> int:
        """Number of registered modules."""
        with self._write_lock:
            return len(self._modules)

    @property
    def module_ids(self) -> list[str]:
        """Sorted list of registered module IDs."""
        with self._write_lock:
            return sorted(self._modules.keys())

    def get_definition(self, module_id: str) -> ModuleDescriptor | None:
        """Get a ModuleDescriptor for a registered module. Returns None if not found."""
        with self._write_lock:
            module = self._modules.get(module_id)
            if module is None:
                return None
            meta = dict(self._module_meta.get(module_id, {}))

        cls = type(module)

        input_schema_cls = getattr(module, "input_schema", None) or getattr(cls, "input_schema", None)
        output_schema_cls = getattr(module, "output_schema", None) or getattr(cls, "output_schema", None)

        input_json = input_schema_cls.model_json_schema() if input_schema_cls else {}
        output_json = output_schema_cls.model_json_schema() if output_schema_cls else {}

        return ModuleDescriptor(
            module_id=module_id,
            name=meta.get("name") or getattr(module, "name", None),
            description=meta.get("description") or getattr(module, "description", ""),
            documentation=meta.get("documentation") or getattr(module, "documentation", None),
            input_schema=input_json,
            output_schema=output_json,
            version=meta.get("version") or getattr(module, "version", "1.0.0"),
            tags=(meta.get("tags") if meta.get("tags") is not None else list(getattr(module, "tags", []) or [])),
            annotations=getattr(module, "annotations", None),
            examples=list(getattr(module, "examples", []) or []),
            metadata=meta.get("metadata", {}),
        )

    # ----- Event System -----

    def on(self, event: str, callback: Callable[..., Any]) -> None:
        """Register an event callback.

        Args:
            event: Event name ('register' or 'unregister').
            callback: Callable(module_id, module) to invoke on the event.

        Raises:
            InvalidInputError: If event name is invalid.
        """
        with self._write_lock:
            if event not in self._callbacks:
                raise InvalidInputError(message=f"Invalid event: {event}. Must be 'register' or 'unregister'")
            self._callbacks[event].append(callback)

    def _trigger_event(self, event: str, module_id: str, module: Any) -> None:
        """Trigger all callbacks for an event. Errors are logged and swallowed."""
        with self._write_lock:
            callbacks = list(self._callbacks.get(event, []))
        for cb in callbacks:
            try:
                cb(module_id, module)
            except Exception as e:
                logger.error(
                    "Callback error for event '%s' on module '%s': %s",
                    event,
                    module_id,
                    e,
                )

    # ----- Cache -----

    def clear_cache(self) -> None:
        """Clear the schema cache."""
        with self._write_lock:
            self._schema_cache.clear()
