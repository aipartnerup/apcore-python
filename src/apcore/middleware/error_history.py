"""Backward-compatibility shim. Import ErrorHistoryMiddleware from error_history_middleware instead."""

import warnings

warnings.warn(
    "apcore.middleware.error_history is deprecated; use apcore.middleware.error_history_middleware",
    DeprecationWarning,
    stacklevel=2,
)
