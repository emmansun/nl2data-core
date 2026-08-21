"""Vendor-neutral telemetry and audit interfaces with safe in-memory sinks."""

from .models import (
    AuditEvent,
    AuditOutcome,
    LogRecord,
    MetricRecord,
    SpanRecord,
    TelemetryContext,
    TelemetryLevel,
)
from .ports import TelemetryError, TelemetryPort, TelemetryReporter
from .redaction import DEFAULT_SAFE_PROFILE, SafeProfile, redact_attributes
from .sinks import FailingTelemetryPort, InMemoryTelemetryPort

__all__ = [
    "AuditEvent",
    "AuditOutcome",
    "DEFAULT_SAFE_PROFILE",
    "FailingTelemetryPort",
    "InMemoryTelemetryPort",
    "LogRecord",
    "MetricRecord",
    "SafeProfile",
    "SpanRecord",
    "TelemetryContext",
    "TelemetryError",
    "TelemetryLevel",
    "TelemetryPort",
    "TelemetryReporter",
    "redact_attributes",
]
