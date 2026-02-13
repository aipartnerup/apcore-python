"""Configuration loading and validation."""

from __future__ import annotations

from typing import Any

__all__ = ["Config"]


class Config:
    """Configuration accessor with dot-path key support.

    Stub implementation for use by the executor system.
    Will be replaced by the full implementation from 01-foundation.
    """

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self._data: dict[str, Any] = data or {}

    def get(self, key: str, default: Any = None) -> Any:
        """Get a configuration value by dot-path key."""
        parts = key.split(".")
        current: Any = self._data
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return default
        return current
