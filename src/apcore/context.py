"""Execution context, identity, and context logger."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Generic, Protocol, TypeVar, runtime_checkable

from apcore.cancel import CancelToken

if TYPE_CHECKING:
    from apcore.observability.context_logger import ContextLogger
    from apcore.trace_context import TraceParent


__all__ = ["Context", "Identity", "ContextFactory"]

T = TypeVar("T")


@dataclass(frozen=True)
class Identity:
    """Caller identity (human/service/AI generic)."""

    id: str
    type: str = "user"
    roles: tuple[str, ...] = field(default_factory=tuple)
    attrs: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Ensure attrs is a dict if None was passed, and protect against mutation
        # of the original dict passed to the constructor.
        if not isinstance(self.attrs, dict):
            object.__setattr__(self, "attrs", {})
        else:
            object.__setattr__(self, "attrs", dict(self.attrs))

    def get_attr(self, key: str, default: Any = None) -> Any:
        """Get an attribute value by key.

        Aligned with apcore D-03. May trigger a fetch if the identity
        is lazy-loaded (future extension).
        """
        return self.attrs.get(key, default)


@dataclass
class Context(Generic[T]):
    """Module execution context."""

    trace_id: str
    caller_id: str | None = None
    call_chain: list[str] = field(default_factory=list)
    executor: Any = None
    identity: Identity | None = None
    redacted_inputs: dict[str, Any] | None = None
    redacted_output: dict[str, Any] | None = None
    data: dict[str, Any] = field(default_factory=dict)
    services: T = None  # type: ignore[assignment]
    cancel_token: CancelToken | None = None
    global_deadline: float | None = field(default=None, repr=False)

    @classmethod
    def create(
        cls,
        identity: Identity | None = None,
        trace_parent: TraceParent | None = None,
        cancel_token: CancelToken | None = None,
        data: dict[str, Any] | None = None,
        services: T = None,  # type: ignore[assignment]
        global_deadline: float | None = None,
    ) -> Context[T]:
        """Create a new top-level Context with a generated trace_id.

        Unified signature per apcore PROTOCOL_SPEC §"Contract: Context.create"
        (v0.22.0, Issue #66). The accepted caller inputs are exactly:
        ``identity``, ``trace_parent``, ``cancel_token``, ``data``,
        ``services``, ``global_deadline``. ``executor`` and ``caller_id`` are
        intentionally NOT inputs — the executor is bound by the Executor at
        pipeline entry (see ``Context._bind_executor``), and ``caller_id`` is
        managed exclusively by ``Context.child()``.

        When *trace_parent* is provided, its ``trace_id`` is accepted only if
        it is exactly 32 lowercase hex characters and not the W3C-reserved
        all-zero or all-f value. Otherwise a fresh 32-hex trace_id is
        generated and a WARN-level log is emitted. No normalization (dashed
        UUID stripping, case folding) is performed here; such normalization
        is the responsibility of the TraceParent header parser or the
        caller's ContextFactory.
        """
        if trace_parent is not None:
            hex_id = trace_parent.trace_id
            if (
                len(hex_id) == 32
                and all(c in "0123456789abcdef" for c in hex_id)
                and hex_id != "0" * 32
                and hex_id != "f" * 32
            ):
                trace_id = hex_id
            else:
                logging.getLogger(__name__).warning(
                    "Invalid trace_id format in trace_parent: %s. Restarting trace.",
                    hex_id,
                )
                trace_id = uuid.uuid4().hex
        else:
            trace_id = uuid.uuid4().hex
        ctx_data: dict[str, Any] = data if data is not None else {}
        # Carry W3C trace_flags and tracestate through the request lifecycle so
        # downstream TraceContext.inject() can propagate the inbound sampling
        # decision and vendor state instead of hardcoding "01".
        if trace_parent is not None:
            flags = getattr(trace_parent, "trace_flags", None)
            if isinstance(flags, str) and len(flags) == 2:
                ctx_data.setdefault("_apcore.trace.flags", flags)
            tracestate = getattr(trace_parent, "tracestate", ())
            if tracestate:
                ctx_data.setdefault("_apcore.trace.state", tuple(tracestate))
        return cls(
            trace_id=trace_id,
            caller_id=None,
            call_chain=[],
            executor=None,
            identity=identity,
            data=ctx_data,
            services=services,  # type: ignore[arg-type]
            cancel_token=cancel_token,
            global_deadline=global_deadline,
        )

    def _bind_executor(self, executor: Any) -> None:
        """SDK-internal. Bind the Executor to this Context.

        Implements PROTOCOL_SPEC §"Contract: Executor binding to Context":
        - If ``self.executor`` is None, bind it.
        - If ``self.executor`` is already the same Executor instance
          (identity comparison), the rebind is a noop.
        - If ``self.executor`` is a *different* Executor instance, raise
          :class:`apcore.errors.ContextBindingError`.

        Not intended for public callers; the Executor invokes this before
        pipeline step 1 on every entry point that accepts a caller-supplied
        Context.
        """
        if self.executor is None:
            self.executor = executor
        elif self.executor is not executor:
            # Imported lazily to avoid a circular import (errors -> context).
            from apcore.errors import ContextBindingError

            raise ContextBindingError("Context already bound to a different Executor instance")
        # else: same executor instance, noop.

    def serialize(self) -> dict[str, Any]:
        """Serialize Context to a JSON-encodable dict.

        Includes ``_context_version: 1`` at top level for forward
        compatibility. Excludes non-serializable / transient fields
        (``executor``, ``services``, ``cancel_token``, ``global_deadline``)
        and filters ``_``-prefixed keys from ``data``.
        """
        result: dict[str, Any] = {
            "_context_version": 1,
            "trace_id": self.trace_id,
            "caller_id": self.caller_id,
            "call_chain": list(self.call_chain),
        }
        if self.identity is not None:
            result["identity"] = {
                "id": self.identity.id,
                "type": self.identity.type,
                "roles": list(self.identity.roles),
                "attrs": dict(self.identity.attrs),
            }
        else:
            result["identity"] = None
        if self.redacted_inputs is not None:
            result["redacted_inputs"] = dict(self.redacted_inputs)
        if self.redacted_output is not None:
            result["redacted_output"] = dict(self.redacted_output)
        result["data"] = {k: v for k, v in self.data.items() if not k.startswith("_")}
        return result

    @classmethod
    def deserialize(cls, data: dict[str, Any]) -> Context:
        """Reconstruct a Context from its :meth:`serialize` output.

        Non-serializable fields (``executor``, ``services``, ``cancel_token``,
        ``global_deadline``) are set to ``None``; callers that need them
        should re-inject after deserialization. A ``_context_version`` greater
        than 1 logs a warning and best-effort proceeds (forward compatibility).
        """
        version = data.get("_context_version", 1)
        if version > 1:
            logging.getLogger(__name__).warning(
                "Unknown _context_version %d (expected 1). Proceeding with best-effort deserialization.",
                version,
            )

        identity = None
        if data.get("identity") is not None:
            id_data = data["identity"]
            identity = Identity(
                id=id_data["id"],
                type=id_data.get("type", "user"),
                roles=tuple(id_data.get("roles", ())),
                attrs=id_data.get("attrs", {}),
            )

        return cls(
            trace_id=data.get("trace_id", ""),
            caller_id=data.get("caller_id"),
            call_chain=list(data.get("call_chain", [])),
            executor=None,
            identity=identity,
            redacted_inputs=data.get("redacted_inputs"),
            redacted_output=data.get("redacted_output"),
            data=dict(data.get("data", {})),
            services=None,  # type: ignore[arg-type]
            cancel_token=None,
        )

    @property
    def logger(self) -> ContextLogger:
        """Return a ContextLogger with this context's trace_id and caller_id."""
        from apcore.observability.context_logger import ContextLogger

        return ContextLogger.from_context(self, name=self.caller_id or "unknown")

    def child(self, target_module_id: str) -> Context[T]:
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
            services=self.services,
            cancel_token=self.cancel_token,
            global_deadline=self.global_deadline,
        )


@runtime_checkable
class ContextFactory(Protocol):
    """Protocol for creating Context from runtime-specific requests.

    Web framework integrations should implement this to extract Identity
    from HTTP requests (e.g., Django request.user, JWT tokens, API keys).

    Example:
        class DjangoContextFactory:
            def create_context(self, request: HttpRequest) -> Context:
                identity = Identity(
                    id=str(request.user.id),
                    type="user",
                    roles=list(request.user.groups.values_list("name", flat=True)),
                )
                return Context.create(identity=identity)
    """

    def create_context(self, request: Any) -> Context: ...
