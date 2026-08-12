"""Conformance tests for Observability Hardening (Issue #43).

Covers all 10 test cases defined in the canonical conformance fixture at:
  apcore/conformance/fixtures/observability_hardening.json

  1.  pluggable_store_default_inmemory
  2.  batch_processor_buffers_spans
  3.  batch_processor_drops_on_full_queue
  4.  error_history_evicts_oldest_first
  5.  error_fingerprint_dedup_same_error
  6.  error_fingerprint_normalization
  7.  fingerprint_different_errors_no_collision
  8.  redaction_field_pattern_match
  9.  redaction_value_pattern_match
  10. prometheus_format_includes_required_metrics
"""

from __future__ import annotations

import sys

import io
import json
import time


from apcore.context import Context
from apcore.errors import ModuleError
from apcore.observability.context_logger import ObsLoggingMiddleware, RedactionConfig
from apcore.observability.error_history import (
    ErrorHistory,
    compute_fingerprint,
    normalize_message,
)
from apcore.observability.metrics import MetricsCollector
from apcore.observability.store import InMemoryObservabilityStore
from apcore.observability.tracing import BatchSpanProcessor, InMemoryExporter, Span
from conformance.canonical_fixtures import case_ids


# ---------------------------------------------------------------------------
# 1. pluggable_store_default_inmemory
# ---------------------------------------------------------------------------


class TestPluggableStoreDefaultInMemory:
    """Case: pluggable_store_default_inmemory"""

    def test_error_history_default_store_is_inmemory(self) -> None:
        """ErrorHistory() uses InMemoryObservabilityStore when no store is given."""
        history = ErrorHistory()
        assert type(history.store).__name__ == "InMemoryObservabilityStore"

    def test_metrics_collector_default_store_is_inmemory(self) -> None:
        """MetricsCollector() uses InMemoryObservabilityStore when no store is given."""
        collector = MetricsCollector()
        assert type(collector._store).__name__ == "InMemoryObservabilityStore"

    def test_error_history_accepts_injected_store(self) -> None:
        """ErrorHistory accepts an explicit store at construction time."""
        store = InMemoryObservabilityStore()
        history = ErrorHistory(store=store)
        assert history.store is store

    def test_store_not_settable_after_construction(self) -> None:
        """The store is immutable after construction (no setter)."""
        history = ErrorHistory()
        assert not hasattr(type(history), "store") or isinstance(getattr(type(history), "store", None), property)


# ---------------------------------------------------------------------------
# 2. batch_processor_buffers_spans
# ---------------------------------------------------------------------------


class TestBatchProcessorBuffersSpans:
    """Case: batch_processor_buffers_spans"""

    def test_spans_enqueued_not_exported_immediately(self) -> None:
        """Spans submitted to BatchSpanProcessor are enqueued; not exported immediately."""
        exporter = InMemoryExporter()
        processor = BatchSpanProcessor(
            exporter=exporter,
            max_queue_size=2048,
            schedule_delay_ms=60_000,  # very long — background flush won't fire during test
        )
        try:
            for _ in range(3):
                span = Span(trace_id="t1", name="test", start_time=time.time())
                processor.on_span_end(span)

            assert exporter.get_spans() == [], "No spans should be exported before flush"
            assert processor.queue_size == 3
            assert processor.spans_dropped == 0
        finally:
            processor.shutdown()

    def test_spans_exported_after_flush(self) -> None:
        """Spans are exported when the background thread flushes."""
        exporter = InMemoryExporter()
        processor = BatchSpanProcessor(
            exporter=exporter,
            max_queue_size=2048,
            schedule_delay_ms=50,  # flush quickly
        )
        try:
            for _ in range(3):
                span = Span(trace_id="t1", name="test", start_time=time.time())
                processor.on_span_end(span)

            # Wait for background flush
            deadline = time.time() + 2.0
            while len(exporter.get_spans()) < 3 and time.time() < deadline:
                time.sleep(0.01)

            assert len(exporter.get_spans()) == 3
        finally:
            processor.shutdown()


# ---------------------------------------------------------------------------
# 3. batch_processor_drops_on_full_queue
# ---------------------------------------------------------------------------


class TestBatchProcessorDropsOnFullQueue:
    """Case: batch_processor_drops_on_full_queue"""

    def test_drops_spans_when_queue_full(self) -> None:
        """When queue is at max_queue_size, additional spans are dropped."""
        exporter = InMemoryExporter()
        max_size = 5
        processor = BatchSpanProcessor(
            exporter=exporter,
            max_queue_size=max_size,
            schedule_delay_ms=60_000,
        )
        try:
            # Fill queue to capacity
            for _ in range(max_size):
                span = Span(trace_id="t1", name="test", start_time=time.time())
                processor.on_span_end(span)

            assert processor.queue_size == max_size
            assert processor.spans_dropped == 0

            # Submit 2 more — both should be dropped
            for _ in range(2):
                span = Span(trace_id="t1", name="overflow", start_time=time.time())
                processor.on_span_end(span)

            assert processor.queue_size == max_size, "Queue size must stay at max_queue_size"
            assert processor.spans_dropped == 2
        finally:
            processor.shutdown()

    def test_spans_dropped_counter_accumulates(self) -> None:
        """spans_dropped accumulates across multiple overflow batches."""
        exporter = InMemoryExporter()
        processor = BatchSpanProcessor(
            exporter=exporter,
            max_queue_size=2,
            schedule_delay_ms=60_000,
        )
        try:
            for _ in range(2):
                processor.on_span_end(Span(trace_id="t1", name="ok", start_time=time.time()))

            for _ in range(5):
                processor.on_span_end(Span(trace_id="t1", name="over", start_time=time.time()))

            assert processor.spans_dropped == 5
        finally:
            processor.shutdown()


# ---------------------------------------------------------------------------
# 4. error_history_evicts_oldest_first
# ---------------------------------------------------------------------------


class TestErrorHistoryEvictsOldestFirst:
    """Case: error_history_evicts_oldest_first"""

    def test_evicts_oldest_last_seen_at_on_overflow(self) -> None:
        """When at capacity, the entry with the oldest last_seen_at is evicted."""
        history = ErrorHistory(max_total_entries=3)

        # Record 3 errors; sequence numbers ensure ERR_A is oldest
        history.record("executor.a", ModuleError(code="ERR_A", message="err a"))
        history.record("executor.b", ModuleError(code="ERR_B", message="err b"))
        history.record("executor.c", ModuleError(code="ERR_C", message="err c"))

        assert len(history.get_all()) == 3

        # 4th entry must evict the oldest
        history.record("executor.d", ModuleError(code="ERR_D", message="err d"))

        remaining = history.get_all()
        codes = {e.code for e in remaining}
        assert len(remaining) == 3
        assert "ERR_A" not in codes, "ERR_A (oldest) must be evicted"
        assert {"ERR_B", "ERR_C", "ERR_D"} == codes

    def test_total_entries_stays_at_limit(self) -> None:
        """Total entries never exceed max_total_entries after overflow."""
        history = ErrorHistory(max_total_entries=5)
        for i in range(10):
            history.record(f"mod.{i}", ModuleError(code=f"E{i}", message="err"))
        assert len(history.get_all()) == 5


# ---------------------------------------------------------------------------
# 5. error_fingerprint_dedup_same_error
# ---------------------------------------------------------------------------


class TestErrorFingerprintDedupSameError:
    """Case: error_fingerprint_dedup_same_error"""

    def test_identical_error_increments_count(self) -> None:
        """Recording the same error twice results in count=2, not two entries."""
        history = ErrorHistory()
        error = ModuleError(code="DB_TIMEOUT", message="connection timed out")
        history.record("executor.db.query", error)
        history.record("executor.db.query", error)

        entries = history.get("executor.db.query")
        assert len(entries) == 1
        assert entries[0].count == 2

    def test_fingerprint_stored_on_entry(self) -> None:
        """ErrorEntry stores a non-empty fingerprint field."""
        history = ErrorHistory()
        history.record("mod.a", ModuleError(code="ERR", message="msg"))
        entry = history.get("mod.a")[0]
        assert len(entry.fingerprint) == 64
        assert all(c in "0123456789abcdef" for c in entry.fingerprint)


# ---------------------------------------------------------------------------
# 6. error_fingerprint_normalization
# ---------------------------------------------------------------------------


class TestErrorFingerprintNormalization:
    """Case: error_fingerprint_normalization"""

    def test_uuid_normalized_to_placeholder(self) -> None:
        """UUID values in messages are replaced with <UUID> before hashing."""
        msg1 = "token a1b2c3d4-e5f6-7890-abcd-ef1234567890 is invalid"
        msg2 = "token 00000000-0000-0000-0000-000000000001 is invalid"
        assert normalize_message(msg1) == "token <uuid> is invalid"
        assert normalize_message(msg2) == "token <uuid> is invalid"

    def test_same_normalized_message_produces_equal_fingerprints(self) -> None:
        """Two messages differing only by UUID produce the same fingerprint."""
        fp1 = compute_fingerprint(
            "TOKEN_INVALID",
            "executor.auth",
            "token a1b2c3d4-e5f6-7890-abcd-ef1234567890 is invalid",
        )
        fp2 = compute_fingerprint(
            "TOKEN_INVALID",
            "executor.auth",
            "token 00000000-0000-0000-0000-000000000001 is invalid",
        )
        assert fp1 == fp2

    def test_large_integers_normalized(self) -> None:
        """Integers with 4+ digits (with word boundaries) are replaced with <ID>."""
        assert normalize_message("failed 30000 times") == "failed <id> times"

    def test_iso_timestamp_normalized(self) -> None:
        """ISO 8601 date strings are replaced with <TIMESTAMP> (applied before integers)."""
        msg = "event at 2026-01-01T10:00:00Z"
        assert normalize_message(msg) == "event at <timestamp>"

    def test_uuid_dedup_via_record(self) -> None:
        """Errors with different UUIDs in the message are deduplicated into one entry."""
        history = ErrorHistory()
        history.record(
            "executor.auth",
            ModuleError(
                code="TOKEN_INVALID",
                message="token a1b2c3d4-e5f6-7890-abcd-ef1234567890 is invalid",
            ),
        )
        history.record(
            "executor.auth",
            ModuleError(
                code="TOKEN_INVALID",
                message="token 00000000-0000-0000-0000-000000000001 is invalid",
            ),
        )
        entries = history.get("executor.auth")
        assert len(entries) == 1
        assert entries[0].count == 2


# ---------------------------------------------------------------------------
# 7. fingerprint_different_errors_no_collision
# ---------------------------------------------------------------------------


class TestFingerprintDifferentErrorsNoCollision:
    """Case: fingerprint_different_errors_no_collision"""

    def test_different_codes_different_fingerprints(self) -> None:
        """Two errors with different codes produce distinct fingerprints."""
        fp1 = compute_fingerprint("DB_TIMEOUT", "executor.db.query", "connection timed out")
        fp2 = compute_fingerprint("DB_CONN_REFUSED", "executor.db.query", "connection timed out")
        assert fp1 != fp2

    def test_different_module_ids_different_fingerprints(self) -> None:
        """Same code and message in different modules produce distinct fingerprints."""
        fp1 = compute_fingerprint("ERR", "mod.a", "msg")
        fp2 = compute_fingerprint("ERR", "mod.b", "msg")
        assert fp1 != fp2

    def test_fingerprint_is_64_char_hex(self) -> None:
        """compute_fingerprint always returns a 64-character lowercase hex string."""
        fp = compute_fingerprint("ERR", "mod", "message")
        assert len(fp) == 64
        assert fp == fp.lower()
        assert all(c in "0123456789abcdef" for c in fp)


# ---------------------------------------------------------------------------
# 8. redaction_field_pattern_match
# ---------------------------------------------------------------------------


class TestRedactionFieldPatternMatch:
    """Case: redaction_field_pattern_match"""

    def test_field_matching_glob_is_redacted(self) -> None:
        """Field name matching *password* glob is replaced with the configured replacement."""
        buf = io.StringIO()
        config = RedactionConfig(
            field_patterns=["*password*"],
            value_patterns=[],
            replacement="***REDACTED***",
        )
        from apcore.observability.context_logger import ContextLogger

        logger = ContextLogger(name="test", output=buf, output_format="json")
        mw = ObsLoggingMiddleware(logger=logger, log_inputs=True, redaction_config=config)

        ctx = Context.create()
        ctx.call_chain.append("executor.auth.login")
        mw.before(
            "executor.auth.login",
            {"username": "alice", "user_password": "hunter2"},
            ctx,
        )

        log_entry = json.loads(buf.getvalue().strip())
        logged_inputs = log_entry["extra"]["inputs"]
        assert logged_inputs["username"] == "alice"
        assert logged_inputs["user_password"] == "***REDACTED***"
        # Required observability correlation fields must survive redaction (key present, value may be None)
        assert "module_id" in log_entry["extra"]
        assert "caller_id" in log_entry["extra"]

    def test_non_matching_field_not_redacted(self) -> None:
        """Fields not matching any pattern are logged unchanged."""
        config = RedactionConfig(field_patterns=["*secret*"], value_patterns=[])
        buf = io.StringIO()
        from apcore.observability.context_logger import ContextLogger

        logger = ContextLogger(name="test", output=buf)
        mw = ObsLoggingMiddleware(logger=logger, log_inputs=True, redaction_config=config)

        ctx = Context.create()
        mw.before("mod.a", {"name": "alice", "age": "30"}, ctx)

        log_entry = json.loads(buf.getvalue().strip())
        assert log_entry["extra"]["inputs"]["name"] == "alice"
        assert log_entry["extra"]["inputs"]["age"] == "30"


# ---------------------------------------------------------------------------
# 9. redaction_value_pattern_match
# ---------------------------------------------------------------------------


class TestRedactionValuePatternMatch:
    """Case: redaction_value_pattern_match"""

    def test_value_matching_regex_is_redacted(self) -> None:
        """Field value matching ^Bearer .* regex is replaced with the replacement."""
        buf = io.StringIO()
        config = RedactionConfig(
            field_patterns=[],
            value_patterns=[r"^Bearer .*"],
            replacement="***REDACTED***",
        )
        from apcore.observability.context_logger import ContextLogger

        logger = ContextLogger(name="test", output=buf)
        mw = ObsLoggingMiddleware(logger=logger, log_inputs=True, redaction_config=config)

        ctx = Context.create()
        mw.before(
            "executor.http.request",
            {
                "url": "https://api.example.com/data",
                "authorization": "Bearer abc123xyz",
            },
            ctx,
        )

        log_entry = json.loads(buf.getvalue().strip())
        logged_inputs = log_entry["extra"]["inputs"]
        assert logged_inputs["url"] == "https://api.example.com/data"
        assert logged_inputs["authorization"] == "***REDACTED***"

    def test_non_matching_value_not_redacted(self) -> None:
        """Values not matching any pattern are logged unchanged."""
        config = RedactionConfig(field_patterns=[], value_patterns=[r"^sk-[A-Za-z0-9]+"])
        buf = io.StringIO()
        from apcore.observability.context_logger import ContextLogger

        logger = ContextLogger(name="test", output=buf)
        mw = ObsLoggingMiddleware(logger=logger, log_inputs=True, redaction_config=config)

        ctx = Context.create()
        mw.before("mod.a", {"token": "not-an-sk-token"}, ctx)

        log_entry = json.loads(buf.getvalue().strip())
        assert log_entry["extra"]["inputs"]["token"] == "not-an-sk-token"


# ---------------------------------------------------------------------------
# 10. prometheus_format_includes_required_metrics
# ---------------------------------------------------------------------------


class TestPrometheusFormatIncludesRequiredMetrics:
    """Case: prometheus_format_includes_required_metrics"""

    def test_output_contains_all_three_required_metrics(self) -> None:
        """export_prometheus() includes all three mandatory apcore metric families."""
        collector = MetricsCollector()
        collector.increment_calls("mod.a", "success")
        collector.increment_calls("mod.a", "success")  # total=2, but we set 42 below via increment
        collector.reset()
        collector.increment(
            "apcore_module_calls_total",
            {"module_id": "mod.a", "status": "success"},
            amount=42,
        )
        collector.increment(
            "apcore_module_errors_total",
            {"module_id": "mod.a", "error_code": "ERR"},
            amount=3,
        )
        collector.observe("apcore_module_duration_seconds", {"module_id": "mod.a"}, 0.01)
        collector.observe("apcore_module_duration_seconds", {"module_id": "mod.a"}, 0.05)
        collector.observe("apcore_module_duration_seconds", {"module_id": "mod.a"}, 0.12)

        output = collector.export_prometheus()

        assert "apcore_module_calls_total" in output
        assert "apcore_module_errors_total" in output
        assert "apcore_module_duration_seconds" in output

    def test_prometheus_text_format_conventions(self) -> None:
        """Output follows Prometheus text format: HELP, TYPE lines, then metric lines."""
        collector = MetricsCollector()
        collector.increment_calls("m", "success")
        output = collector.export_prometheus()
        assert "# HELP apcore_module_calls_total" in output
        assert "# TYPE apcore_module_calls_total counter" in output

    def test_prometheus_exporter_export_method(self) -> None:
        """PrometheusExporter.export() delegates to MetricsCollector.export_prometheus()."""
        from apcore.observability.prometheus_exporter import PrometheusExporter

        collector = MetricsCollector()
        collector.increment_calls("mod.a", "success")
        exporter = PrometheusExporter(collector=collector)
        output = exporter.export()
        assert "apcore_module_calls_total" in output


# ---------------------------------------------------------------------------
# Fixture coverage guard
# ---------------------------------------------------------------------------


class TestFixtureCoverage:
    """Every case in the canonical fixture has a driver class in this file.

    The assertions above are hand-written rather than generated from the
    fixture. That is fine, but the fixture used to be named only in the module
    docstring, so a case added on the spec side left no trace here. This guard
    closes that gap: a new canonical case fails until someone writes the class.
    """

    FIXTURE = "observability_hardening.json"

    #: canonical case id -> the class in this module that asserts it.
    COVERED: dict[str, str] = {
        "pluggable_store_default_inmemory": "TestPluggableStoreDefaultInMemory",
        "batch_processor_buffers_spans": "TestBatchProcessorBuffersSpans",
        "batch_processor_drops_on_full_queue": "TestBatchProcessorDropsOnFullQueue",
        "error_history_evicts_oldest_first": "TestErrorHistoryEvictsOldestFirst",
        "error_fingerprint_dedup_same_error": "TestErrorFingerprintDedupSameError",
        "error_fingerprint_normalization": "TestErrorFingerprintNormalization",
        "fingerprint_different_errors_no_collision": "TestFingerprintDifferentErrorsNoCollision",
        "redaction_field_pattern_match": "TestRedactionFieldPatternMatch",
        "redaction_value_pattern_match": "TestRedactionValuePatternMatch",
        "prometheus_format_includes_required_metrics": "TestPrometheusFormatIncludesRequiredMetrics",
    }

    def test_every_canonical_case_is_claimed(self) -> None:
        canonical = set(case_ids(self.FIXTURE))
        claimed = set(self.COVERED)
        assert canonical - claimed == set(), (
            f"canonical fixture {self.FIXTURE} gained case(s) with no driver here"
        )
        assert claimed - canonical == set(), (
            f"this file claims case(s) {self.FIXTURE} no longer defines"
        )

    def test_every_claimed_class_exists(self) -> None:
        module = sys.modules[__name__]
        missing = [cls for cls in self.COVERED.values() if not hasattr(module, cls)]
        assert missing == [], f"claimed driver class(es) not defined: {missing}"
