"""Directory scanner for discovering Python extension modules."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from apcore.errors import ConfigError, ConfigNotFoundError
from apcore.registry.types import DiscoveredModule

logger = logging.getLogger(__name__)

__all__ = ["scan_extensions", "scan_multi_root"]

_SKIP_DIR_NAMES = {"__pycache__", "node_modules"}
_SKIP_FILE_SUFFIXES = {".pyc"}


def scan_extensions(
    root: Path,
    max_depth: int = 8,
    follow_symlinks: bool = False,
) -> list[DiscoveredModule]:
    """Recursively scan an extensions directory for Python module files."""
    root = Path(root).resolve()
    if not root.exists():
        raise ConfigNotFoundError(config_path=str(root))

    visited_real_paths: set[Path] = {root.resolve()}
    results: list[DiscoveredModule] = []
    seen_ids: dict[str, Path] = {}
    seen_ids_lower: dict[str, str] = {}

    def _scan_dir(dir_path: Path, depth: int) -> None:
        if depth > max_depth:
            logger.info("Max depth %d exceeded at %s, skipping", max_depth, dir_path)
            return

        try:
            entries = list(os.scandir(dir_path))
        except PermissionError as e:
            logger.error("Permission denied scanning %s: %s", dir_path, e)
            return
        except OSError as e:
            logger.error("OS error scanning %s: %s", dir_path, e)
            return

        for entry in entries:
            name = entry.name
            if name.startswith(".") or name.startswith("_"):
                continue
            if name in _SKIP_DIR_NAMES:
                continue

            try:
                is_dir = entry.is_dir(follow_symlinks=follow_symlinks)
                is_file = entry.is_file(follow_symlinks=follow_symlinks)
                is_symlink = entry.is_symlink()
            except OSError as e:
                logger.error("OS error accessing %s: %s", entry.path, e)
                continue

            entry_path = Path(entry.path)

            if is_dir:
                if is_symlink:
                    if not follow_symlinks:
                        continue
                    real = entry_path.resolve()
                    if real in visited_real_paths:
                        logger.warning(
                            "Symlink cycle detected at %s -> %s, skipping",
                            entry_path,
                            real,
                        )
                        continue
                    visited_real_paths.add(real)
                _scan_dir(entry_path, depth + 1)
            elif is_file:
                suffix = Path(name).suffix
                if suffix in _SKIP_FILE_SUFFIXES:
                    continue
                if suffix != ".py":
                    continue

                rel = entry_path.relative_to(root)
                canonical_id = str(rel.with_suffix("")).replace(os.sep, ".")

                if canonical_id in seen_ids:
                    logger.error(
                        "Duplicate module ID '%s' at %s, already found at %s. Skipping.",
                        canonical_id,
                        entry_path,
                        seen_ids[canonical_id],
                    )
                    continue

                lower_id = canonical_id.lower()
                if lower_id in seen_ids_lower and seen_ids_lower[lower_id] != canonical_id:
                    logger.warning(
                        "Case collision: '%s' and '%s' differ only by case",
                        canonical_id,
                        seen_ids_lower[lower_id],
                    )

                meta_path = entry_path.with_name(entry_path.stem + "_meta.yaml")
                if not meta_path.exists():
                    meta_path = None

                dm = DiscoveredModule(
                    file_path=entry_path,
                    canonical_id=canonical_id,
                    meta_path=meta_path,
                    namespace=None,
                )
                seen_ids[canonical_id] = entry_path
                seen_ids_lower[lower_id] = canonical_id
                results.append(dm)

    _scan_dir(root, depth=1)
    return results


def scan_multi_root(
    roots: list[dict],
    max_depth: int = 8,
    follow_symlinks: bool = False,
) -> list[DiscoveredModule]:
    """Scan multiple extension roots with namespace prefixing."""
    all_results: list[DiscoveredModule] = []
    seen_namespaces: set[str] = set()

    # Validate all namespaces before scanning
    resolved: list[tuple[Path, str]] = []
    for entry in roots:
        root_path = Path(entry["root"])
        namespace = entry.get("namespace") or root_path.name
        if namespace in seen_namespaces:
            raise ConfigError(message=f"Duplicate namespace: '{namespace}'")
        seen_namespaces.add(namespace)
        resolved.append((root_path, namespace))

    for root_path, namespace in resolved:
        modules = scan_extensions(root_path, max_depth=max_depth, follow_symlinks=follow_symlinks)
        for m in modules:
            all_results.append(
                DiscoveredModule(
                    file_path=m.file_path,
                    canonical_id=f"{namespace}.{m.canonical_id}",
                    meta_path=m.meta_path,
                    namespace=namespace,
                )
            )

    return all_results
