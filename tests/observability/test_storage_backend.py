"""Tests for the generic StorageBackend protocol (Issue #43 §1).

Validates the namespaced ``save / get / list / delete`` surface mirrored on
the ``TaskStore`` pattern, the default ``InMemoryStorageBackend`` implementation,
and that observability collectors accept an injected backend.
"""

from __future__ import annotations

from apcore.errors import ModuleError
from apcore.observability.error_history import ErrorHistory
from apcore.observability.storage import (
    InMemoryStorageBackend,
    StorageBackend,
)


class TestInMemoryStorageBackend:
    def test_protocol_satisfied(self) -> None:
        assert isinstance(InMemoryStorageBackend(), StorageBackend)

    def test_save_then_get(self) -> None:
        backend = InMemoryStorageBackend()
        backend.save("ns1", "k1", {"a": 1})
        assert backend.get("ns1", "k1") == {"a": 1}

    def test_get_missing_returns_none(self) -> None:
        assert InMemoryStorageBackend().get("ns", "missing") is None

    def test_namespaces_are_isolated(self) -> None:
        backend = InMemoryStorageBackend()
        backend.save("ns1", "shared", {"v": 1})
        backend.save("ns2", "shared", {"v": 2})
        assert backend.get("ns1", "shared") == {"v": 1}
        assert backend.get("ns2", "shared") == {"v": 2}

    def test_list_returns_all_entries(self) -> None:
        backend = InMemoryStorageBackend()
        backend.save("ns", "a", {"v": 1})
        backend.save("ns", "b", {"v": 2})
        items = dict(backend.list("ns"))
        assert items == {"a": {"v": 1}, "b": {"v": 2}}

    def test_list_with_prefix(self) -> None:
        backend = InMemoryStorageBackend()
        backend.save("ns", "task:1", {"v": 1})
        backend.save("ns", "task:2", {"v": 2})
        backend.save("ns", "user:1", {"v": 3})
        items = dict(backend.list("ns", prefix="task:"))
        assert set(items.keys()) == {"task:1", "task:2"}

    def test_list_empty_namespace(self) -> None:
        assert InMemoryStorageBackend().list("nonexistent") == []

    def test_delete_removes_entry(self) -> None:
        backend = InMemoryStorageBackend()
        backend.save("ns", "k", {"v": 1})
        backend.delete("ns", "k")
        assert backend.get("ns", "k") is None

    def test_delete_missing_is_noop(self) -> None:
        backend = InMemoryStorageBackend()
        backend.delete("ns", "missing")  # must not raise


class TestErrorHistoryAcceptsStorageBackend:
    def test_error_history_uses_injected_backend(self) -> None:
        backend = InMemoryStorageBackend()
        hist = ErrorHistory(storage=backend)
        hist.record("mod.x", ModuleError(code="E1", message="boom"))

        # The error should be reachable through both the in-memory index and
        # the injected backend (under a stable namespace).
        entries = hist.get("mod.x")
        assert len(entries) == 1
        # Backend has an "errors" namespace populated.
        backend_items = backend.list("errors")
        assert len(backend_items) >= 1
