"""Execution context, identity, and context logger."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

__all__ = ["Context", "Identity"]


@dataclass(frozen=True)
class Identity:
    """Caller identity (human/service/AI generic)."""

    id: str
    type: str = "user"
    roles: list[str] = field(default_factory=list)
    attrs: dict[str, Any] = field(default_factory=dict)


@dataclass
class Context:
    """Module execution context."""

    trace_id: str
    caller_id: str | None = None
    call_chain: list[str] = field(default_factory=list)
    executor: Any = None
    identity: Identity | None = None
    redacted_inputs: dict[str, Any] | None = None
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        executor: Any = None,
        identity: Identity | None = None,
        data: dict[str, Any] | None = None,
    ) -> Context:
        """Create a new top-level Context with a generated UUID v4 trace_id."""
        return cls(
            trace_id=str(uuid.uuid4()),
            caller_id=None,
            call_chain=[],
            executor=executor,
            identity=identity,
            data=data if data is not None else {},
        )

    def child(self, target_module_id: str) -> Context:
        """Create a child Context for calling a target module.

        The ``data`` dict is intentionally shared (not copied) between parent
        and child contexts.  Middleware such as TracingMiddleware and
        MetricsMiddleware rely on this shared reference to maintain span and
        timing stacks across nested module-to-module calls.
        """
        return Context(
            trace_id=self.trace_id,
            caller_id=self.call_chain[-1] if self.call_chain else None,
            call_chain=[*self.call_chain, target_module_id],
            executor=self.executor,
            identity=self.identity,
            data=self.data,
        )
