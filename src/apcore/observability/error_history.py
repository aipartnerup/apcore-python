"""Thread-safe error history with min-heap eviction, fingerprinting, and pluggable storage."""

from __future__ import annotations

import hashlib
import heapq
import re
import threading
import traceback
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from apcore.errors import ModuleError
from apcore.observability.store import ObservabilityStore


def normalize_message(msg: str) -> str:
    """Replace ephemeral values with placeholders before fingerprint hashing.

    Steps (applied in order to avoid conflicts):
    1. UUID patterns (8-4-4-4-12 hex) → <UUID>
    2. ISO 8601 timestamps → <TIMESTAMP>  (must precede integer step: years are 4 digits)
    3. Long hex runs (≥ 8 hex chars) → <HEX>
    4. Integers ≥ 4 digits → <ID>
    """
    # Step 1: UUID patterns (8-4-4-4-12 hex)
    msg = re.sub(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        "<UUID>",
        msg,
    )
    # Step 2: ISO 8601 timestamps (before integers, to protect 4-digit years)
    msg = re.sub(
        r"\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})?)?",
        "<TIMESTAMP>",
        msg,
    )
    # Step 3: hex IDs of 8+ chars (e.g. ``0xdeadbeef``, raw hashes).
    msg = re.sub(r"\b(?:0x)?[0-9a-fA-F]{8,}\b", "<HEX>", msg)
    # Step 4: integers > 3 digits (word-boundary on both sides)
    msg = re.sub(r"\b\d{4,}\b", "<ID>", msg)
    return msg.strip().lower()


def compute_fingerprint(error_code: str, module_id: str, message: str) -> str:
    """Compute SHA-256(error_code:module_id:normalized_message) as 64-char hex.

    Legacy 3-arg form preserved for callers that don't have a traceback.
    Prefer :func:`compute_error_fingerprint` when an exception is available
    so the top stack frame can be folded into the fingerprint (Issue #43 §4).
    """
    normalized = normalize_message(message)
    raw = f"{error_code}:{module_id}:{normalized}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _top_frame_signature(error: BaseException) -> str:
    """Return ``file:lineno:func`` for the deepest frame of ``error.__traceback__``.

    Returns the empty string when no traceback is attached (e.g. errors
    constructed without ``raise``).  Only the basename of the file is used
    so that fingerprints are stable across machines / tmp paths.
    """
    tb = getattr(error, "__traceback__", None)
    if tb is None:
        return ""
    frames = traceback.extract_tb(tb)
    if not frames:
        return ""
    last = frames[-1]
    import os

    return f"{os.path.basename(last.filename)}:{last.lineno}:{last.name}"


def compute_error_fingerprint(
    error: ModuleError | BaseException,
    module_id: str,
) -> str:
    """Compute a fingerprint for an exception (Issue #43 §4).

    Inputs folded into the SHA-256 digest (in order):

    1. **Error code** if the exception exposes one (``ModuleError.code``,
       falling back to the exception class name).
    2. **Module id** the error fired in.
    3. **Top stack frame** signature ``file:lineno:func`` (basename only —
       absolute paths would break cross-machine deduplication).  Empty
       string when the exception has no traceback attached.
    4. **Sanitized message template** — UUIDs, ISO timestamps, hex IDs
       and digit runs ≥ 4 chars are replaced with placeholders so that
       cosmetically-different messages collapse to the same fingerprint.

    Returns a 64-char hex digest.
    """
    code = getattr(error, "code", None) or type(error).__name__
    message = getattr(error, "message", None)
    if message is None:
        message = str(error)
    normalized = normalize_message(message)
    frame = _top_frame_signature(error)
    raw = f"{code}:{module_id}:{frame}:{normalized}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class ErrorEntry:
    """A single deduplicated error history entry."""

    module_id: str
    code: str
    message: str
    ai_guidance: str | None
    timestamp: str
    count: int
    first_occurred: str
    last_occurred: str
    fingerprint: str = field(default="")


class ErrorHistory:
    """Thread-safe error tracker with min-heap O(log N) eviction and SHA-256 deduplication.

    Data structure:
      _fp_index: fingerprint → ErrorEntry        O(1) dedup lookup
      _module_index: module_id → deque[entry]    O(1) module get; O(1) popleft eviction
      _heap: min-heap (last_occurred, seq, entry) O(log N) eviction of oldest by last_seen_at

    Heap note: lazy deletion is used — stale heap entries (from dedup timestamp refreshes)
    are skipped on pop. The heap may grow to O(total_records) in a high-dedup workload;
    it is bounded in practice by max_total_entries × dedup_factor.

    Deduplication is keyed on SHA-256(code:module_id:normalize(message)) so ephemeral
    values in messages (UUIDs, timestamps, large integers) do not create duplicate entries.

    _secret_ prefix note: the ``_secret_`` redaction convention (ContextLogger) only applies
    to top-level extra dict keys, not to nested module input fields.  Use ``RedactionConfig``
    with ``field_patterns=["_secret_*"]`` on ``ObsLoggingMiddleware`` to cover input fields.
    """

    def __init__(
        self,
        max_entries_per_module: int = 50,
        max_total_entries: int = 1000,
        store: ObservabilityStore | None = None,
    ) -> None:
        from apcore.observability.store import InMemoryObservabilityStore

        self._max_entries_per_module = max_entries_per_module
        self._max_total_entries = max_total_entries
        self._store: ObservabilityStore = store if store is not None else InMemoryObservabilityStore()
        self._lock = threading.Lock()
        self._fp_index: dict[str, ErrorEntry] = {}
        self._module_index: dict[str, deque[ErrorEntry]] = {}
        # min-heap: (last_occurred_str, seq, entry)
        self._heap: list[tuple[str, int, ErrorEntry]] = []
        self._seq = 0

    @property
    def store(self) -> ObservabilityStore:
        return self._store

    def record(self, module_id: str, error: ModuleError) -> None:
        """Record an error, deduplicating by fingerprint.

        Issue #43 §4: when the error carries a traceback, the fingerprint
        also includes a top-frame signature so that two failures with the
        same code+message but different originating frames are kept
        separate.  Errors without a traceback fall through to the legacy
        ``code:module_id:normalized_message`` digest.
        """
        now = datetime.now(timezone.utc).isoformat()
        fp = compute_error_fingerprint(error, module_id)
        with self._lock:
            existing = self._fp_index.get(fp)
            if existing is not None:
                existing.count += 1
                existing.last_occurred = now
                self._seq += 1
                heapq.heappush(self._heap, (now, self._seq, existing))
                entry_to_notify = existing
            else:
                entry = ErrorEntry(
                    module_id=module_id,
                    code=error.code,
                    message=error.message,
                    ai_guidance=error.ai_guidance,
                    timestamp=now,
                    count=1,
                    first_occurred=now,
                    last_occurred=now,
                    fingerprint=fp,
                )
                self._fp_index[fp] = entry
                self._module_index.setdefault(module_id, deque()).append(entry)
                self._seq += 1
                heapq.heappush(self._heap, (now, self._seq, entry))
                self._evict_module(module_id)
                self._evict_total()
                entry_to_notify = entry
        # Notify store outside the internal lock to avoid lock-ordering issues.
        self._store.record_error(entry_to_notify)

    def get(self, module_id: str, limit: int | None = None) -> list[ErrorEntry]:
        """Return entries for a module, newest first (by insertion order)."""
        with self._lock:
            module_entries = self._module_index.get(module_id, deque())
            result = list(reversed(module_entries))
        if limit is not None:
            result = result[:limit]
        return result

    def get_all(self, limit: int | None = None) -> list[ErrorEntry]:
        """Return all entries sorted by last_occurred, newest first."""
        with self._lock:
            all_entries = list(self._fp_index.values())
        all_entries.sort(key=lambda e: e.last_occurred, reverse=True)
        if limit is not None:
            all_entries = all_entries[:limit]
        return all_entries

    def _evict_module(self, module_id: str) -> None:
        """Remove oldest entries (O(1) popleft) when per-module limit is exceeded."""
        module_entries = self._module_index.get(module_id)
        if module_entries is None:
            return
        while len(module_entries) > self._max_entries_per_module:
            evicted = module_entries.popleft()
            self._fp_index.pop(evicted.fingerprint, None)

    def _evict_total(self) -> None:
        """Evict the oldest entry (by last_occurred) until total is within limit."""
        while len(self._fp_index) > self._max_total_entries:
            self._pop_oldest()

    def _pop_oldest(self) -> None:
        """Pop the oldest live entry from the heap and remove it from all indexes."""
        while self._heap:
            ts, _, entry = heapq.heappop(self._heap)
            # Skip stale heap entries: fingerprint evicted already, or refreshed by dedup.
            if entry.fingerprint in self._fp_index and entry.last_occurred == ts:
                self._fp_index.pop(entry.fingerprint, None)
                module_entries = self._module_index.get(entry.module_id)
                if module_entries is not None:
                    try:
                        module_entries.remove(entry)
                    except ValueError:
                        pass
                    if not module_entries:
                        self._module_index.pop(entry.module_id, None)
                return


__all__ = [
    "ErrorEntry",
    "ErrorHistory",
    "normalize_message",
    "compute_fingerprint",
    "compute_error_fingerprint",
]
