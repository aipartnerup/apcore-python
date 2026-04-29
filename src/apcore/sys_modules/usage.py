"""system.usage sys modules -- usage summary and single module detail (PRD F14/F15)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from apcore.errors import InvalidInputError, ModuleNotFoundError
from apcore.module import ModuleAnnotations
from apcore.observability.usage import (
    HourlyBucket,
    ModuleUsageSummary,
    UsageCollector,
    _bucket_key,
)
from apcore.registry.registry import Registry

__all__ = ["UsageModule", "UsageModuleModule", "UsageSummaryModule"]


@dataclass
class ModuleUsageEntry:
    """A single module's usage entry in the summary output."""

    module_id: str
    call_count: int
    error_count: int
    avg_latency_ms: float
    unique_callers: int
    trend: str

    def to_dict(self) -> dict[str, Any]:
        """Convert to a plain dictionary for output."""
        return {
            "module_id": self.module_id,
            "call_count": self.call_count,
            "error_count": self.error_count,
            "avg_latency_ms": self.avg_latency_ms,
            "unique_callers": self.unique_callers,
            "trend": self.trend,
        }


def _summaries_to_entries(
    summaries: list[ModuleUsageSummary],
) -> list[ModuleUsageEntry]:
    """Convert UsageCollector summaries to ModuleUsageEntry list."""
    return [
        ModuleUsageEntry(
            module_id=s.module_id,
            call_count=s.call_count,
            error_count=s.error_count,
            avg_latency_ms=s.avg_latency_ms,
            unique_callers=s.unique_callers,
            trend=s.trend,
        )
        for s in summaries
    ]


class UsageSummaryModule:
    """Aggregated usage overview of all registered modules with trend detection."""

    description = "All modules usage overview with trend detection"
    annotations = ModuleAnnotations(readonly=True, idempotent=True)
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "period": {
                "type": "string",
                "description": "Time period for usage data",
                "default": "24h",
            },
        },
    }
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "period": {"type": "string", "description": "Requested time period"},
            "total_calls": {
                "type": "integer",
                "description": "Total calls across all modules",
            },
            "total_errors": {
                "type": "integer",
                "description": "Total errors across all modules",
            },
            "modules": {"type": "array", "description": "Per-module usage entries"},
        },
        "required": ["period", "total_calls", "total_errors", "modules"],
    }

    def __init__(self, collector: UsageCollector) -> None:
        self._collector = collector

    def execute(self, inputs: dict[str, Any], context: Any) -> dict[str, Any]:
        """Produce a usage summary for all modules within the requested period."""
        period: str = inputs.get("period", "24h")
        summaries = self._collector.get_summary(period)
        entries = _summaries_to_entries(summaries)
        entries.sort(key=lambda e: e.call_count, reverse=True)

        total_calls = sum(e.call_count for e in entries)
        total_errors = sum(e.error_count for e in entries)

        return {
            "period": period,
            "total_calls": total_calls,
            "total_errors": total_errors,
            "modules": [e.to_dict() for e in entries],
        }


# ---------------------------------------------------------------------------
# system.usage.module (PRD F15)
# ---------------------------------------------------------------------------


@dataclass
class CallerUsage:
    """Per-caller usage entry in the module detail output."""

    caller_id: str
    call_count: int
    error_count: int
    avg_latency_ms: float

    def to_dict(self) -> dict[str, Any]:
        """Convert to a plain dictionary for output."""
        return {
            "caller_id": self.caller_id,
            "call_count": self.call_count,
            "error_count": self.error_count,
            "avg_latency_ms": self.avg_latency_ms,
        }


@dataclass
class HourlyBucketEntry:
    """Single hour entry in the hourly distribution output."""

    hour: str
    call_count: int
    error_count: int

    def to_dict(self) -> dict[str, Any]:
        """Convert to a plain dictionary for output."""
        return {
            "hour": self.hour,
            "call_count": self.call_count,
            "error_count": self.error_count,
        }


def _compute_p99(latencies: list[float]) -> float:
    """Compute the 99th percentile from a list of latency values using nearest-rank."""
    if not latencies:
        return 0.0
    sorted_lat = sorted(latencies)
    # Nearest-rank method: index = ceil(p/100 * N) - 1
    rank = math.ceil(0.99 * len(sorted_lat))
    idx = min(rank, len(sorted_lat)) - 1
    # For the 99th percentile, we want the value at or above the 99th rank
    # Use the next higher index when there are enough values
    if rank < len(sorted_lat):
        return sorted_lat[rank]
    return sorted_lat[idx]


def _pad_hourly_distribution(
    buckets: list[HourlyBucket],
    period: str,
) -> list[HourlyBucketEntry]:
    """Pad hourly distribution to exactly 24 entries, filling gaps with zeros.

    Generates keys for the last 24 hours, merging in any actual data buckets.
    """
    now = datetime.now(timezone.utc)
    existing: dict[str, HourlyBucket] = {b.hour: b for b in buckets}

    # Generate the 24 hourly keys covering from (now - 23h) to now
    keys: list[str] = []
    for i in range(24):
        hour_dt = now - timedelta(hours=23 - i)
        keys.append(_bucket_key(hour_dt))

    # Also include any data bucket keys not already in the generated set
    key_set = set(keys)
    for k in sorted(existing.keys()):
        if k not in key_set:
            keys.append(k)
            key_set.add(k)

    # Sort and take the latest 24
    keys = sorted(set(keys))[-24:]

    result: list[HourlyBucketEntry] = []
    for key in keys:
        if key in existing:
            b = existing[key]
            result.append(
                HourlyBucketEntry(
                    hour=key,
                    call_count=b.call_count,
                    error_count=b.error_count,
                )
            )
        else:
            result.append(HourlyBucketEntry(hour=key, call_count=0, error_count=0))

    return result


class UsageModule:
    """Detailed usage statistics for a single module with per-caller breakdown."""

    description = "Detailed usage statistics for a single module"
    annotations = ModuleAnnotations(readonly=True, idempotent=True)
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "module_id": {
                "type": "string",
                "description": "ID of the module to inspect",
            },
            "period": {
                "type": "string",
                "description": "Time period for usage data",
                "default": "24h",
            },
        },
        "required": ["module_id"],
    }
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "module_id": {"type": "string", "description": "Module identifier"},
            "period": {"type": "string", "description": "Requested time period"},
            "call_count": {
                "type": "integer",
                "description": "Total calls in the period",
            },
            "error_count": {
                "type": "integer",
                "description": "Total errors in the period",
            },
            "avg_latency_ms": {
                "type": "number",
                "description": "Average latency in milliseconds",
            },
            "p99_latency_ms": {
                "type": "number",
                "description": "99th percentile latency in milliseconds",
            },
            "trend": {"type": "string", "description": "Usage trend indicator"},
            "callers": {"type": "array", "description": "Per-caller usage breakdown"},
            "hourly_distribution": {
                "type": "array",
                "description": "Hourly call/error distribution",
            },
        },
        "required": [
            "module_id",
            "period",
            "call_count",
            "error_count",
            "avg_latency_ms",
            "p99_latency_ms",
            "trend",
            "callers",
            "hourly_distribution",
        ],
    }

    def __init__(
        self,
        registry: Registry,
        usage_collector: UsageCollector,
    ) -> None:
        self._registry = registry
        self._collector = usage_collector

    def execute(self, inputs: dict[str, Any], context: Any) -> dict[str, Any]:
        """Return detailed usage info for a single module."""
        module_id: str | None = inputs.get("module_id")
        if not module_id:
            raise InvalidInputError(message="module_id is required")

        if not self._registry.has(module_id):
            raise ModuleNotFoundError(module_id=module_id)

        period: str = inputs.get("period", "24h")
        detail = self._collector.get_module(module_id, period)

        latencies = self._collector.get_latencies(module_id, period)
        p99 = _compute_p99(latencies)

        callers = [
            CallerUsage(
                caller_id=c.caller_id,
                call_count=c.call_count,
                error_count=c.error_count,
                avg_latency_ms=c.avg_latency_ms,
            ).to_dict()
            for c in detail.callers
        ]

        hourly = _pad_hourly_distribution(detail.hourly_distribution, period)

        return {
            "module_id": detail.module_id,
            "period": period,
            "call_count": detail.call_count,
            "error_count": detail.error_count,
            "avg_latency_ms": detail.avg_latency_ms,
            "p99_latency_ms": p99,
            "trend": detail.trend,
            "callers": callers,
            "hourly_distribution": [h.to_dict() for h in hourly],
        }


#: Backward-compatible alias for :class:`UsageModule`.
UsageModuleModule = UsageModule
