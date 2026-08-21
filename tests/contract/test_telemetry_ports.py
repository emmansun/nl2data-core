"""Contract tests for telemetry ports, correlation, redaction and degradation."""

from __future__ import annotations

from nl2data_core.telemetry.models import (
    AuditEvent,
    AuditOutcome,
    LogRecord,
    MetricRecord,
    SpanRecord,
    TelemetryContext,
    TelemetryLevel,
)
from nl2data_core.telemetry.ports import TelemetryError, TelemetryPort, TelemetryReporter
from nl2data_core.telemetry.sinks import FailingTelemetryPort, InMemoryTelemetryPort

FINGERPRINT = "sha256:" + "12" * 32


def make_context() -> TelemetryContext:
    return TelemetryContext(
        request_id="req-1",
        workflow_id="wf-1",
        config_fingerprint=FINGERPRINT,
        artifact_fingerprint=FINGERPRINT,
    )


class TestInMemorySink:
    def test_typed_events_reach_the_sink(self) -> None:
        sink = InMemoryTelemetryPort()
        context = make_context()

        sink.emit_log(
            LogRecord(record_id="l-1", context=context, level=TelemetryLevel.INFO, message="ok")
        )
        sink.emit_span(SpanRecord(span_id="s-1", trace_id="t-1", context=context, name="query"))
        sink.emit_metric(MetricRecord(metric_id="m-1", context=context, name="attempts", value=1.0))
        sink.emit_audit(AuditEvent(event_id="a-1", context=context, action="query.submitted"))

        assert len(sink.logs) == 1
        assert len(sink.spans) == 1
        assert len(sink.metrics) == 1
        assert len(sink.audits) == 1
        assert sink.counts() == {"logs": 1, "spans": 1, "metrics": 1, "audits": 1}
        assert sink.audits[0].context.workflow_id == "wf-1"

    def test_sink_capacity_is_bounded(self) -> None:
        sink = InMemoryTelemetryPort(max_records_per_type=1)
        context = make_context()
        sink.emit_log(LogRecord(record_id="l-1", context=context, message="first"))
        with pytest_raises_telemetry():
            sink.emit_log(LogRecord(record_id="l-2", context=context, message="second"))


class TestCorrelation:
    def test_context_correlates_but_carries_no_identity_claims(self) -> None:
        context = make_context()
        assert context.request_id == "req-1"
        assert context.workflow_id == "wf-1"
        # No authorization/identity fields exist on the context.
        assert "authorization" not in TelemetryContext.model_fields
        assert "identity" not in TelemetryContext.model_fields

    def test_audit_serializes_safe_fields_only(self) -> None:
        event = AuditEvent(
            event_id="a-1",
            context=make_context(),
            action="query.submitted",
            outcome=AuditOutcome.SUCCESS,
            attributes={"adapter": "sql"},
            evidence_fingerprint=FINGERPRINT,
        )
        dumped = event.serialize_safe()
        assert dumped["request_id"] == "req-1"
        assert dumped["evidence_fingerprint"] == FINGERPRINT
        assert "raw_query" not in dumped


class TestRedactionAtBoundary:
    def test_reporter_redacts_unsafe_attributes(self) -> None:
        sink = InMemoryTelemetryPort()
        reporter = TelemetryReporter(sink)
        reporter.emit_log(
            LogRecord(
                record_id="l-1",
                context=make_context(),
                message="attempt",
                attributes={"password": "hunter2", "raw_query": "SELECT 1", "adapter": "sql"},
            )
        )
        stored = sink.logs[0]
        assert stored.attributes == {"adapter": "sql"}

    def test_reporter_passes_safe_records_through(self) -> None:
        sink = InMemoryTelemetryPort()
        reporter = TelemetryReporter(sink)
        reporter.emit_audit(
            AuditEvent(
                event_id="a-1",
                context=make_context(),
                action="engine.initialized",
                attributes={"duration_ms": "2"},
            )
        )
        assert sink.audits[0].action == "engine.initialized"


class TestBoundedDegradation:
    def test_sink_failure_is_reported_not_raised(self) -> None:
        reporter = TelemetryReporter(FailingTelemetryPort())
        reporter.emit_log(LogRecord(record_id="l-1", context=make_context(), message="x"))
        reporter.emit_audit(AuditEvent(event_id="a-1", context=make_context(), action="a"))
        assert reporter.degradations == 2
        assert reporter.last_degradation is not None
        assert reporter.last_degradation.retryable is True

    def test_degradation_never_changes_control_outcomes(self) -> None:
        reporter = TelemetryReporter(FailingTelemetryPort())
        # A caller that would otherwise deny/allow continues to do so:
        # reporter calls cannot raise and cannot flip authorization.
        denied = False
        try:
            reporter.emit_log(LogRecord(record_id="l-1", context=make_context(), message="x"))
        except TelemetryError:
            denied = True
        assert denied is False
        assert reporter.degradations == 1

    def test_in_memory_port_satisfies_protocol(self) -> None:
        assert isinstance(InMemoryTelemetryPort(), TelemetryPort)


def pytest_raises_telemetry():
    import pytest

    return pytest.raises(TelemetryError)
