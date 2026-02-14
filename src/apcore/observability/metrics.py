"""Thread-safe in-memory metrics collection with Prometheus export."""

from __future__ import annotations

import threading
import time
from typing import Any

from apcore.errors import ModuleError
from apcore.middleware import Middleware

_DESCRIPTIONS = {
    "apcore_module_calls_total": "Total module calls",
    "apcore_module_errors_total": "Total module errors",
    "apcore_module_duration_seconds": "Module execution duration",
}


class MetricsCollector:
    """Thread-safe in-memory metrics store for counters and histograms."""

    DEFAULT_BUCKETS: list[float] = [
        0.005,
        0.01,
        0.025,
        0.05,
        0.1,
        0.25,
        0.5,
        1.0,
        2.5,
        5.0,
        10.0,
        30.0,
        60.0,
    ]

    def __init__(self, buckets: list[float] | None = None) -> None:
        self._buckets = (
            sorted(buckets) if buckets is not None else list(self.DEFAULT_BUCKETS)
        )
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], int] = {}
        self._histogram_sums: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._histogram_counts: dict[tuple[str, tuple[tuple[str, str], ...]], int] = {}
        self._histogram_buckets: dict[
            tuple[str, tuple[tuple[str, str], ...], float], int
        ] = {}

    @staticmethod
    def _labels_key(labels: dict[str, str]) -> tuple[tuple[str, str], ...]:
        return tuple(sorted(labels.items()))

    def increment(self, name: str, labels: dict[str, str], amount: int = 1) -> None:
        key = (name, self._labels_key(labels))
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + amount

    def observe(self, name: str, labels: dict[str, str], value: float) -> None:
        labels_key = self._labels_key(labels)
        key = (name, labels_key)
        with self._lock:
            self._histogram_sums[key] = self._histogram_sums.get(key, 0.0) + value
            self._histogram_counts[key] = self._histogram_counts.get(key, 0) + 1
            for b in self._buckets:
                if value <= b:
                    bkey = (name, labels_key, b)
                    self._histogram_buckets[bkey] = (
                        self._histogram_buckets.get(bkey, 0) + 1
                    )
            # Always increment +Inf
            inf_key = (name, labels_key, float("inf"))
            self._histogram_buckets[inf_key] = (
                self._histogram_buckets.get(inf_key, 0) + 1
            )

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "counters": dict(self._counters),
                "histograms": {
                    "sums": dict(self._histogram_sums),
                    "counts": dict(self._histogram_counts),
                    "buckets": dict(self._histogram_buckets),
                },
            }

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._histogram_sums.clear()
            self._histogram_counts.clear()
            self._histogram_buckets.clear()

    def export_prometheus(self) -> str:
        with self._lock:
            lines: list[str] = []

            # Counters
            counter_names: set[str] = set()
            for (name, _labels_tuple), value in sorted(self._counters.items()):
                if name not in counter_names:
                    desc = _DESCRIPTIONS.get(name, name)
                    lines.append(f"# HELP {name} {desc}")
                    lines.append(f"# TYPE {name} counter")
                    counter_names.add(name)
                labels_str = self._format_labels(dict(_labels_tuple))
                lines.append(f"{name}{labels_str} {value}")

            # Histograms
            hist_names: set[str] = set()
            # Group by (name, labels_tuple)
            hist_keys = sorted(self._histogram_sums.keys())
            for name, labels_tuple in hist_keys:
                if name not in hist_names:
                    desc = _DESCRIPTIONS.get(name, name)
                    lines.append(f"# HELP {name} {desc}")
                    lines.append(f"# TYPE {name} histogram")
                    hist_names.add(name)

                labels_dict = dict(labels_tuple)
                labels_str = self._format_labels(labels_dict)

                # Bucket lines
                for b in self._buckets:
                    bkey = (name, labels_tuple, b)
                    count = self._histogram_buckets.get(bkey, 0)
                    le_str = f"{b:g}"
                    le_labels = {**labels_dict, "le": f"{le_str}"}
                    lines.append(
                        f"{name}_bucket{self._format_labels(le_labels)} {count}"
                    )

                # +Inf bucket
                inf_key = (name, labels_tuple, float("inf"))
                inf_count = self._histogram_buckets.get(inf_key, 0)
                inf_labels = {**labels_dict, "le": "+Inf"}
                lines.append(
                    f"{name}_bucket{self._format_labels(inf_labels)} {inf_count}"
                )

                # _sum and _count
                sum_val = self._histogram_sums.get((name, labels_tuple), 0.0)
                count_val = self._histogram_counts.get((name, labels_tuple), 0)
                lines.append(f"{name}_sum{labels_str} {sum_val}")
                lines.append(f"{name}_count{labels_str} {count_val}")

            return "\n".join(lines) + "\n" if lines else ""

    @staticmethod
    def _format_labels(labels: dict[str, str]) -> str:
        if not labels:
            return ""
        # Sort by key, but put 'le' last for histogram buckets
        sorted_items = sorted(labels.items(), key=lambda x: (x[0] == "le", x[0]))
        pairs = ",".join(f'{k}="{v}"' for k, v in sorted_items)
        return "{" + pairs + "}"

    # --- Convenience methods ---

    def increment_calls(self, module_id: str, status: str) -> None:
        self.increment(
            "apcore_module_calls_total", {"module_id": module_id, "status": status}
        )

    def increment_errors(self, module_id: str, error_code: str) -> None:
        self.increment(
            "apcore_module_errors_total",
            {"module_id": module_id, "error_code": error_code},
        )

    def observe_duration(self, module_id: str, duration_seconds: float) -> None:
        self.observe(
            "apcore_module_duration_seconds", {"module_id": module_id}, duration_seconds
        )


class MetricsMiddleware(Middleware):
    """Middleware that records call counts, error counts, and durations."""

    def __init__(self, collector: MetricsCollector) -> None:
        self._collector = collector

    def before(
        self, module_id: str, inputs: dict[str, Any], context: Any
    ) -> dict[str, Any] | None:
        context.data.setdefault("_metrics_starts", []).append(time.time())
        return None

    def after(
        self,
        module_id: str,
        inputs: dict[str, Any],
        output: dict[str, Any],
        context: Any,
    ) -> dict[str, Any] | None:
        start_time = context.data["_metrics_starts"].pop()
        duration_s = time.time() - start_time
        self._collector.increment_calls(module_id, "success")
        self._collector.observe_duration(module_id, duration_s)
        return None

    def on_error(
        self, module_id: str, inputs: dict[str, Any], error: Exception, context: Any
    ) -> dict[str, Any] | None:
        start_time = context.data["_metrics_starts"].pop()
        duration_s = time.time() - start_time
        error_code = (
            error.code if isinstance(error, ModuleError) else type(error).__name__
        )
        self._collector.increment_calls(module_id, "error")
        self._collector.increment_errors(module_id, error_code)
        self._collector.observe_duration(module_id, duration_s)
        return None


__all__ = ["MetricsCollector", "MetricsMiddleware"]
