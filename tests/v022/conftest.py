"""Shared fixtures for v0.22.0 hardening tests."""

from __future__ import annotations

import pytest

from apcore.context import Context


@pytest.fixture
def make_context():
    """Factory that creates a minimal Context for unit tests."""

    def _make() -> Context:
        return Context(trace_id="test-trace-id-v022")

    return _make
