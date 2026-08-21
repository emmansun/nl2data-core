"""Bounded in-memory telemetry sinks for deterministic tests."""

from __future__ import annotations

from typing import Any

from .models import AuditEvent, LogRecord, MetricRecord, SpanRecord
from .ports import TelemetryError


class InMemoryTelemetryPort:
    """Bounded in-memory sink; snapshots are deterministic and immutable."""

    def __init__(self, max_records_per_type: int = 1_000) -> None:
        if max_records_per_type <= 0:
            raise ValueError("max_records_per_type must be positive")
        self._max = max_records_per_type
        self._logs: list[LogRecord] = []
        self._spans: list[SpanRecord] = []
        self._metrics: list[MetricRecord] = []
        self._audits: list[AuditEvent] = []

    def _append(self, target: list[Any], record: Any) -> None:
        if len(target) >= self._max:
            raise TelemetryError(
                "telemetry sink capacity exceeded",
                details={"max_records": self._max},
            )
        target.append(record)

    def emit_log(self, record: LogRecord) -> None:
        self._append(self._logs, record)

    def emit_span(self, record: SpanRecord) -> None:
        self._append(self._spans, record)

    def emit_metric(self, record: MetricRecord) -> None:
        self._append(self._metrics, record)

    def emit_audit(self, record: AuditEvent) -> None:
        self._append(self._audits, record)

    @property
    def logs(self) -> tuple[LogRecord, ...]:
        return tuple(self._logs)

    @property
    def spans(self) -> tuple[SpanRecord, ...]:
        return tuple(self._spans)

    @property
    def metrics(self) -> tuple[MetricRecord, ...]:
        return tuple(self._metrics)

    @property
    def audits(self) -> tuple[AuditEvent, ...]:
        return tuple(self._audits)

    def counts(self) -> dict[str, int]:
        """Deterministic counts per record type."""
        return {
            "logs": len(self._logs),
            "spans": len(self._spans),
            "metrics": len(self._metrics),
            "audits": len(self._audits),
        }


class FailingTelemetryPort:
    """A port that always fails, for degradation tests."""

    def emit_log(self, record: LogRecord) -> None:
        raise TelemetryError("simulated log sink failure")

    def emit_span(self, record: SpanRecord) -> None:
        raise TelemetryError("simulated span sink failure")

    def emit_metric(self, record: MetricRecord) -> None:
        raise TelemetryError("simulated metric sink failure")

    def emit_audit(self, record: AuditEvent) -> None:
        raise TelemetryError("simulated audit sink failure")
