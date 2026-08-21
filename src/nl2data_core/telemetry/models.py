"""Typed telemetry and audit event models with opaque identifiers."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .redaction import redact_attributes

_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"
_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"
_MAX_ATTRIBUTES = 64


def _utc_now() -> datetime:
    return datetime.now(UTC)


class TelemetryContext(BaseModel):
    """Opaque correlation context.

    Carries opaque identifiers and optional fingerprints only; it must never
    be used as an authorization decision or identity source.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    workflow_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    config_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    policy_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    metadata_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    semantic_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    artifact_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)


class TelemetryLevel(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class AuditOutcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    DENIED = "denied"


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    context: TelemetryContext
    occurred_at: datetime = Field(default_factory=_utc_now)


class LogRecord(_Record):
    """A structured log record with bounded attributes."""

    record_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    level: TelemetryLevel = TelemetryLevel.INFO
    message: str = Field(min_length=1, max_length=2000)
    attributes: dict[str, str] = Field(default_factory=dict, max_length=_MAX_ATTRIBUTES)


class SpanRecord(_Record):
    """A span record for tracing with opaque trace/span IDs."""

    span_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    trace_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    name: str = Field(min_length=1, max_length=256)
    started_at: datetime = Field(default_factory=_utc_now)
    ended_at: datetime | None = None
    attributes: dict[str, str] = Field(default_factory=dict, max_length=_MAX_ATTRIBUTES)


class MetricRecord(_Record):
    """A typed metric sample."""

    metric_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    name: str = Field(min_length=1, max_length=128)
    value: float
    unit: str = Field(default="", max_length=32)
    attributes: dict[str, str] = Field(default_factory=dict, max_length=_MAX_ATTRIBUTES)


class AuditEvent(_Record):
    """An immutable audit event with optional evidence fingerprint."""

    event_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    action: str = Field(min_length=1, max_length=128)
    outcome: AuditOutcome = AuditOutcome.SUCCESS
    actor_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    attributes: dict[str, str] = Field(default_factory=dict, max_length=_MAX_ATTRIBUTES)
    evidence_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)

    def serialize_safe(self) -> dict[str, Any]:
        """Audit serialization with bounded, safe fields only.

        Attributes are passed through the default safe profile so raw
        prompts, queries, results and credentials are never emitted.
        """
        return {
            "event_id": self.event_id,
            "action": self.action,
            "outcome": self.outcome.value,
            "actor_id": self.actor_id,
            "request_id": self.context.request_id,
            "workflow_id": self.context.workflow_id,
            "attributes": redact_attributes(self.attributes),
            "evidence_fingerprint": self.evidence_fingerprint,
            "occurred_at": self.occurred_at.isoformat(),
        }
