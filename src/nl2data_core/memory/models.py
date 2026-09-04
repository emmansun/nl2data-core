"""Immutable bounded Memory records and safe logical references.

Memory stores logical facts only: typed payloads carrying bounded text,
identifiers, and protected SHA-256 fingerprints.  Raw prompts, SQL/MQL
text, rows/documents, secrets, and native objects are rejected at the
model boundary so a provider can never persist executable material or
protected data.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nl2data_core.canonical import strict_sha256_fingerprint

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"
_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"

#: Bounds applied to every record field so memory stays small and typed.
_MAX_METADATA_KEYS = 32
_MAX_METADATA_VALUE_CHARS = 256
_MAX_SEMANTIC_TEXT_CHARS = 2_000
_MAX_TTL_SECONDS = 3_153_600  # 365 days
_MAX_REFERENCE_FIELD_IDS = 256
_MAX_RECALL_RECORDS = 1_000

#: Approximate SQL statement shape (defense in depth; the record boundary
#: is enforced by validation, detection alone is never authoritative).
_SQL_STATEMENT = re.compile(
    r"\b(select|insert|update|delete|drop|create|alter|truncate|merge|exec|execute)\b"
    r"[\s\S]{0,200}\b(from|into|set|table|values)\b",
    re.IGNORECASE,
)

#: Low-signal text markers that usually precede credential material.
_SECRET_MARKERS = (
    "password=",
    "passwd=",
    "secret=",
    "api_key=",
    "apikey=",
    "token=",
    "dsn=",
    "private key",
    "bearer ",
    "client_secret=",
    "access_key=",
)

#: Key names that signal raw payload material at any depth.
_RAW_PAYLOAD_KEY_NAMES = frozenset(
    {
        "sql",
        "mql",
        "query",
        "prompt",
        "rows",
        "documents",
        "credentials",
        "password",
        "secret",
        "api_key",
        "token",
        "cursor",
        "connection",
        "statement",
        "shell",
        "script",
        "code",
        "ast",
        "driver",
        "native",
    }
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def scan_raw_text(value: str) -> str | None:
    """Return a violation reason when text looks like raw query/secret material.

    Returns ``None`` when the text is safe; the scan is defense in depth
    only and never replaces the record validation boundary.
    """
    lowered = value.lower()
    if _SQL_STATEMENT.search(value):
        return "executable_sql"
    if any(marker in lowered for marker in _SECRET_MARKERS):
        return "secret_marker"
    return None


def reject_raw_payload(value: Any, path: str) -> None:
    """Raise when a payload contains raw-data key names at any depth.

    Recursively rejects maps and lists whose keys signal SQL/MQL, prompts,
    rows/documents, credentials, or native driver objects.
    """
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).lower() in _RAW_PAYLOAD_KEY_NAMES:
                raise ValueError(
                    f"{path}.{key} is a raw payload field and cannot be stored in memory"
                )
            reject_raw_payload(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            reject_raw_payload(item, f"{path}[{index}]")


class MemoryRecordKind(StrEnum):
    """Typed kinds of memory records with explicit semantics."""

    WORKING = "working"
    SESSION = "session"
    QUERY_REFERENCE = "query_reference"
    SEMANTIC_DECISION = "semantic_decision"
    AUDIT_REFERENCE = "audit_reference"


class MemoryScope(BaseModel):
    """Immutable scope binding for one memory record.

    Working memory may be session-scoped only; every other kind requires a
    tenant scope fingerprint so recalled context can never cross a tenant
    boundary.  The scope fingerprint is a stable reference, never proof of
    identity or authority.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_scope_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    session_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    conversation_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    adapter_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    source_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    fingerprint: str = Field(default="", pattern=_FINGERPRINT_PATTERN)

    @model_validator(mode="after")
    def _compute_fingerprint(self) -> MemoryScope:
        fingerprint = strict_sha256_fingerprint(
            {
                "tenant_scope_fingerprint": self.tenant_scope_fingerprint,
                "session_id": self.session_id,
                "conversation_id": self.conversation_id,
                "adapter_id": self.adapter_id,
                "source_id": self.source_id,
            }
        )
        object.__setattr__(self, "fingerprint", fingerprint)
        return self

    def safe_dump(self) -> dict[str, Any]:
        """Serialize with fingerprints and bounded identifiers only."""
        return {
            "tenant_scope_fingerprint": self.tenant_scope_fingerprint,
            "session_id": self.session_id,
            "conversation_id": self.conversation_id,
            "adapter_id": self.adapter_id,
            "source_id": self.source_id,
            "fingerprint": self.fingerprint,
        }


class QueryReference(BaseModel):
    """A logical reference to a prior query, never the query itself.

    Accepts only protected fingerprints (intent/IR/artifact/policy/
    catalog) plus bounded semantic identifiers; prompts, SQL/MQL, and
    result rows are structurally impossible in this model.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    reference_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    intent_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    ir_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    artifact_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    policy_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    catalog_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    semantic_view_fingerprint: str | None = Field(
        default=None, pattern=_FINGERPRINT_PATTERN
    )
    adapter_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    source_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    root_entity_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    field_ids: frozenset[str] = Field(
        default_factory=frozenset, max_length=_MAX_REFERENCE_FIELD_IDS
    )
    fingerprint: str = Field(default="", pattern=_FINGERPRINT_PATTERN)

    @field_validator("field_ids")
    @classmethod
    def _valid_field_ids(cls, value: frozenset[str]) -> frozenset[str]:
        for field_id in value:
            if len(field_id) > 128 or not field_id:
                raise ValueError(
                    "reference field ids must be non-empty and at most 128 characters"
                )
        return value

    @model_validator(mode="after")
    def _compute_fingerprint(self) -> QueryReference:
        object.__setattr__(self, "fingerprint", strict_sha256_fingerprint(self.canonical_payload()))
        return self

    def canonical_payload(self) -> dict[str, Any]:
        """Order-independent payload covering every fingerprint input."""
        return {
            "reference_id": self.reference_id,
            "intent_fingerprint": self.intent_fingerprint,
            "ir_fingerprint": self.ir_fingerprint,
            "artifact_fingerprint": self.artifact_fingerprint,
            "policy_fingerprint": self.policy_fingerprint,
            "catalog_fingerprint": self.catalog_fingerprint,
            "semantic_view_fingerprint": self.semantic_view_fingerprint,
            "adapter_id": self.adapter_id,
            "source_id": self.source_id,
            "root_entity_id": self.root_entity_id,
            "field_ids": sorted(self.field_ids),
        }


class SemanticDecision(BaseModel):
    """A confirmed interpretation from a prior clarification.

    The interpretation is bounded free text that is scanned for raw query
    or secret material; policy and catalog fingerprints record the scope
    the decision was made under.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    confirmed_interpretation: str = Field(min_length=1, max_length=_MAX_SEMANTIC_TEXT_CHARS)
    policy_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    catalog_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    fingerprint: str = Field(default="", pattern=_FINGERPRINT_PATTERN)

    @field_validator("confirmed_interpretation")
    @classmethod
    def _scan_interpretation(cls, value: str) -> str:
        violation = scan_raw_text(value)
        if violation is not None:
            raise ValueError(f"confirmed interpretation contains raw payload material: {violation}")
        return value

    @model_validator(mode="after")
    def _compute_fingerprint(self) -> SemanticDecision:
        object.__setattr__(
            self,
            "fingerprint",
            strict_sha256_fingerprint(
                {
                    "decision_id": self.decision_id,
                    "confirmed_interpretation": self.confirmed_interpretation,
                    "policy_fingerprint": self.policy_fingerprint,
                    "catalog_fingerprint": self.catalog_fingerprint,
                }
            ),
        )
        return self


class WorkingPayload(BaseModel):
    """Bounded working-memory note for the current session."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    payload_kind: Literal["working"] = "working"
    label: str = Field(min_length=1, max_length=256)
    detail: str | None = Field(default=None, max_length=_MAX_SEMANTIC_TEXT_CHARS)

    @field_validator("detail")
    @classmethod
    def _scan_detail(cls, value: str | None) -> str | None:
        if value is not None:
            violation = scan_raw_text(value)
            if violation is not None:
                raise ValueError(f"detail contains raw payload material: {violation}")
        return value


class SessionPayload(BaseModel):
    """Bounded session summary; never raw rows, prompts, or documents."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    payload_kind: Literal["session"] = "session"
    session_summary: str = Field(min_length=1, max_length=_MAX_SEMANTIC_TEXT_CHARS)

    @field_validator("session_summary")
    @classmethod
    def _scan_summary(cls, value: str) -> str:
        violation = scan_raw_text(value)
        if violation is not None:
            raise ValueError(f"session summary contains raw payload material: {violation}")
        return value


class QueryReferencePayload(BaseModel):
    """A logical query reference; the reference model rejects raw queries."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    payload_kind: Literal["query_reference"] = "query_reference"
    reference: QueryReference


class SemanticDecisionPayload(BaseModel):
    """A confirmed interpretation from a prior clarification."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    payload_kind: Literal["semantic_decision"] = "semantic_decision"
    decision: SemanticDecision


class AuditReferencePayload(BaseModel):
    """A stable reference to an audit event; never the event payload."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    payload_kind: Literal["audit_reference"] = "audit_reference"
    audit_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    event_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)


MemoryPayload = Annotated[
    WorkingPayload
    | SessionPayload
    | QueryReferencePayload
    | SemanticDecisionPayload
    | AuditReferencePayload,
    Field(discriminator="payload_kind"),
]


class MemoryRecord(BaseModel):
    """Immutable typed memory record with TTL, scope, and fingerprint.

    ``expires_at`` is derived from ``ttl_seconds`` when not supplied and
    must always be after ``created_at``.  Non-working records require a
    tenant scope fingerprint; the fingerprint covers the canonical payload
    so equal records always produce equal references.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    record_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    scope: MemoryScope
    payload: MemoryPayload
    created_at: datetime = Field(default_factory=_utc_now)
    ttl_seconds: int = Field(default=86_400, ge=1, le=_MAX_TTL_SECONDS)
    expires_at: datetime | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    fingerprint: str = Field(default="", pattern=_FINGERPRINT_PATTERN)

    @field_validator("metadata")
    @classmethod
    def _valid_metadata(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > _MAX_METADATA_KEYS:
            raise ValueError(f"memory record metadata is limited to {_MAX_METADATA_KEYS} keys")
        for key, item in value.items():
            if not key or len(key) > 64 or not re.fullmatch(r"[A-Za-z0-9_\-\.]+", key):
                raise ValueError("metadata keys must be bounded identifiers")
            if len(item) > _MAX_METADATA_VALUE_CHARS:
                raise ValueError(
                    f"metadata values are limited to {_MAX_METADATA_VALUE_CHARS} chars"
                )
            violation = scan_raw_text(item)
            if violation is not None:
                raise ValueError(f"metadata contains raw payload material: {violation}")
        reject_raw_payload(value, "metadata")
        return value

    @model_validator(mode="after")
    def _finalize(self) -> MemoryRecord:
        created = self.created_at
        expires = self.expires_at or (created + timedelta(seconds=self.ttl_seconds))
        if expires <= created:
            raise ValueError("expires_at must be after created_at")
        if (self.kind is not MemoryRecordKind.WORKING
                and self.scope.tenant_scope_fingerprint is None):
            raise ValueError("non-working memory records require a tenant scope fingerprint")
        object.__setattr__(self, "expires_at", expires)
        object.__setattr__(self, "fingerprint", strict_sha256_fingerprint(self.canonical_payload()))
        return self

    @property
    def kind(self) -> MemoryRecordKind:
        """The typed kind derived from the payload discriminator."""
        return MemoryRecordKind(self.payload.payload_kind)

    def is_expired(self, *, now: datetime | None = None) -> bool:
        """Whether the record has expired at ``now``."""
        if self.expires_at is None:
            return False
        return self.expires_at <= (now or _utc_now())

    def canonical_payload(self) -> dict[str, Any]:
        """Order-independent payload covering every record field.

        ``field_ids`` is sorted so equal records fingerprint identically
        no matter how their set was constructed (set literal, JSON
        round-trip, or a different process hash seed).
        """
        payload_dump = self.payload.model_dump(mode="json")
        reference = payload_dump.get("reference")
        if isinstance(reference, dict) and isinstance(reference.get("field_ids"), list):
            reference["field_ids"] = sorted(reference["field_ids"])
        return {
            "record_id": self.record_id,
            "scope_fingerprint": self.scope.fingerprint,
            "payload": payload_dump,
            "created_at": self.created_at.isoformat(),
            "ttl_seconds": self.ttl_seconds,
            "expires_at": self.expires_at.isoformat() if self.expires_at is not None else None,
            "metadata": dict(sorted(self.metadata.items())),
        }

    def safe_dump(self) -> dict[str, Any]:
        """Serialize with bounded fields and fingerprints only."""
        return {
            "record_id": self.record_id,
            "kind": self.kind.value,
            "scope": self.scope.safe_dump(),
            "payload": self.payload.model_dump(mode="json"),
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at is not None else None,
            "ttl_seconds": self.ttl_seconds,
            "metadata": dict(sorted(self.metadata.items())),
            "fingerprint": self.fingerprint,
        }


class MemoryRecallBudget(BaseModel):
    """Bounded recall budget; a projection never exceeds any limit."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_records: int = Field(default=100, ge=1, le=_MAX_RECALL_RECORDS)
    max_chars: int = Field(default=100_000, ge=1, le=1_000_000)
    max_tokens: int = Field(default=25_000, ge=1, le=250_000)


class MemoryRecallProjection(BaseModel):
    """Immutable result of one bounded recall operation.

    Only records that match the query scope and are not expired may
    appear; ``truncated`` is set when eligible records were dropped by a
    budget limit.  The fingerprint covers only record references, never
    raw content.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    scope_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    records: tuple[MemoryRecord, ...] = Field(default_factory=tuple, max_length=_MAX_RECALL_RECORDS)
    truncated: bool = False
    char_count: int = Field(default=0, ge=0)
    token_estimate: int = Field(default=0, ge=0)
    fingerprint: str = Field(default="", pattern=_FINGERPRINT_PATTERN)

    @model_validator(mode="after")
    def _compute_fingerprint(self) -> MemoryRecallProjection:
        fingerprint = strict_sha256_fingerprint(
            {
                "scope_fingerprint": self.scope_fingerprint,
                "records": [record.fingerprint for record in self.records],
                "truncated": self.truncated,
                "char_count": self.char_count,
                "token_estimate": self.token_estimate,
            }
        )
        object.__setattr__(self, "fingerprint", fingerprint)
        return self

    @property
    def record_count(self) -> int:
        """Number of recalled records."""
        return len(self.records)

    def safe_payload(self) -> dict[str, Any]:
        """Provider-safe projection: record fingerprints only, never content."""
        return {
            "scope_fingerprint": self.scope_fingerprint,
            "record_fingerprints": [record.fingerprint for record in self.records],
            "truncated": self.truncated,
            "char_count": self.char_count,
            "token_estimate": self.token_estimate,
            "fingerprint": self.fingerprint,
        }
