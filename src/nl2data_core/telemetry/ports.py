"""Vendor-neutral telemetry ports and bounded sink-failure behavior."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, TypeVar, runtime_checkable

from nl2data.errors import ErrorCategory, ErrorCode, ErrorRecord, NL2DataError

from .models import AuditEvent, LogRecord, MetricRecord, SpanRecord
from .redaction import DEFAULT_SAFE_PROFILE, SafeProfile, redact_attributes

#: Type variable bounded to the concrete telemetry record types.
_RecordT = TypeVar("_RecordT", LogRecord, SpanRecord, MetricRecord, AuditEvent)


class TelemetryError(NL2DataError):
    """Raised by a telemetry sink when it cannot accept a record."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            ErrorCategory.TELEMETRY,
            ErrorCode.TELEMETRY_SINK_FAILURE,
            message,
            retryable=True,
            details=details,
        )


@runtime_checkable
class TelemetryPort(Protocol):
    """Vendor-neutral typed port for logs, spans, metrics and audit events."""

    def emit_log(self, record: LogRecord) -> None:
        """Emit a structured log record; raise :class:`TelemetryError` on failure."""
        ...

    def emit_span(self, record: SpanRecord) -> None:
        """Emit a span record; raise :class:`TelemetryError` on failure."""
        ...

    def emit_metric(self, record: MetricRecord) -> None:
        """Emit a metric sample; raise :class:`TelemetryError` on failure."""
        ...

    def emit_audit(self, record: AuditEvent) -> None:
        """Emit an audit event; raise :class:`TelemetryError` on failure."""
        ...


class TelemetryReporter:
    """Safe telemetry facade with redaction and bounded degradation.

    Sink failures are observable through :attr:`degradations` but never
    change authorization or application error outcomes: emit methods never
    raise.
    """

    def __init__(
        self,
        port: TelemetryPort,
        profile: SafeProfile = DEFAULT_SAFE_PROFILE,
    ) -> None:
        self._port = port
        self._profile = profile
        self._degradations = 0
        self._last_degradation: ErrorRecord | None = None

    @property
    def degradations(self) -> int:
        """Number of sink failures observed (bounded degradation signal)."""
        return self._degradations

    @property
    def last_degradation(self) -> ErrorRecord | None:
        return self._last_degradation

    def _redact(self, record: _RecordT) -> _RecordT:
        attributes = redact_attributes(record.attributes, self._profile)
        return record.model_copy(update={"attributes": attributes})

    def _safe_emit(self, operation: str, emit: Callable[[], None]) -> None:
        try:
            emit()
        except Exception as exc:  # degradation must never propagate
            self._degradations += 1
            if isinstance(exc, NL2DataError):
                record = exc.to_record()
            else:
                record = ErrorRecord(
                    code=ErrorCode.TELEMETRY_SINK_FAILURE,
                    category=ErrorCategory.TELEMETRY,
                    message="telemetry sink failure",
                    retryable=True,
                    details={"operation": operation, "cause_type": type(exc).__name__},
                )
            self._last_degradation = record

    def emit_log(self, record: LogRecord) -> None:
        redacted = self._redact(record)
        self._safe_emit("log", lambda: self._port.emit_log(redacted))

    def emit_span(self, record: SpanRecord) -> None:
        redacted = self._redact(record)
        self._safe_emit("span", lambda: self._port.emit_span(redacted))

    def emit_metric(self, record: MetricRecord) -> None:
        redacted = self._redact(record)
        self._safe_emit("metric", lambda: self._port.emit_metric(redacted))

    def emit_audit(self, record: AuditEvent) -> None:
        redacted = self._redact(record)
        self._safe_emit("audit", lambda: self._port.emit_audit(redacted))
