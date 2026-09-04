"""Durable workflow state foundation: records, safe snapshots, and schema.

SQLite persistence stores normalized safe JSON snapshots plus indexed
identity columns.  Raw prompts, queries, results, credentials, and provider
objects are rejected before they reach the database, and unsupported
snapshot versions are refused on read instead of being guessed at.  Tenant
scope travels only as opaque fingerprints and derived namespaces - never as
raw tenant or principal claims.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from nl2data.errors import ErrorCategory, ErrorCode, NL2DataError
from nl2data.models import QueryOutcome
from nl2data_core.canonical import strict_sha256_fingerprint

from .models import WorkflowState, WorkflowStatus

_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"
_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"
_KEY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"

#: Current serialization schema version of durable snapshots.
SNAPSHOT_SCHEMA_VERSION = 1

#: Current database schema version tracked through ``PRAGMA user_version``.
SCHEMA_VERSION = 1

#: Field names that must never appear inside a safe snapshot; any raw
#: payload key is rejected before the snapshot is persisted or restored.
RAW_PAYLOAD_FIELDS = frozenset(
    {
        "prompt",
        "raw_prompt",
        "query",
        "raw_query",
        "sql",
        "statement",
        "result",
        "raw_result",
        "rows",
        "credentials",
        "password",
        "secret",
        "token",
        "api_key",
        "authorization",
        "cookie",
        "private_key",
        "connection",
        "cursor",
        "provider",
        "client",
        "session",
    }
)

#: Bounded schema: identity columns are indexed; raw payloads are never
#: stored, so no content indexes exist.  Scope namespaces keep every
#: tenant's records isolated within the same tables.
SCHEMA_DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS schema_metadata (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS workflow_states (
        workflow_id TEXT NOT NULL,
        request_id TEXT NOT NULL,
        tenant_scope_fingerprint TEXT,
        scope_namespace TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL,
        revision INTEGER NOT NULL DEFAULT 1,
        schema_version INTEGER NOT NULL,
        snapshot TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        expires_at TEXT,
        PRIMARY KEY (scope_namespace, workflow_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_workflow_request
        ON workflow_states (scope_namespace, request_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_workflow_status
        ON workflow_states (status, updated_at)
    """,
    """
    CREATE TABLE IF NOT EXISTS idempotency_records (
        idempotency_key TEXT NOT NULL,
        request_id TEXT NOT NULL,
        tenant_scope_fingerprint TEXT,
        scope_namespace TEXT NOT NULL DEFAULT '',
        workflow_id TEXT NOT NULL,
        status TEXT NOT NULL,
        terminal_outcome_fingerprint TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        expires_at TEXT,
        PRIMARY KEY (scope_namespace, idempotency_key)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_idempotency_expiry
        ON idempotency_records (expires_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_idempotency_request
        ON idempotency_records (scope_namespace, request_id)
    """,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class WorkflowSerializationError(NL2DataError):
    """Raised when a durable snapshot cannot be read or written safely."""

    def __init__(
        self,
        message: str,
        *,
        code: ErrorCode = ErrorCode.UNSUPPORTED_SCHEMA_VERSION,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            ErrorCategory.WORKFLOW,
            code,
            message,
            retryable=False,
            details=details,
        )


class IdempotencyConflictError(NL2DataError):
    """Raised when an idempotency key is reused with conflicting bindings."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            ErrorCategory.WORKFLOW,
            ErrorCode.IDEMPOTENCY_CONFLICT,
            message,
            retryable=False,
            details=details,
        )


class DurableWorkflowRecord(BaseModel):
    """One persisted workflow record: identity columns plus safe snapshot.

    The ``snapshot`` field carries the canonical safe serialization, and
    ``revision`` is the monotonic compare-and-set counter.  Only the opaque
    scope fingerprint and its derived namespace are persisted - never raw
    tenant or principal claims.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = Field(default=SNAPSHOT_SCHEMA_VERSION, ge=1, le=1)
    workflow_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    request_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    tenant_scope_fingerprint: str | None = Field(
        default=None, pattern=_FINGERPRINT_PATTERN
    )
    scope_namespace: str = ""
    status: WorkflowStatus
    revision: int = Field(default=1, ge=1)
    snapshot: str = Field(min_length=1)
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None = None


class IdempotencyStatus(StrEnum):
    """Lifecycle of one bounded idempotency-key record."""

    RESERVED = "reserved"
    COMPLETED = "completed"


class IdempotencyRecord(BaseModel):
    """A bounded idempotency-key record binding key, request, and workflow.

    A completed record stores only the safe terminal outcome fingerprint
    reference - never the outcome payload itself.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    idempotency_key: str = Field(pattern=_KEY_PATTERN)
    request_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    tenant_scope_fingerprint: str | None = Field(
        default=None, pattern=_FINGERPRINT_PATTERN
    )
    scope_namespace: str = ""
    workflow_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    status: IdempotencyStatus = IdempotencyStatus.RESERVED
    terminal_outcome_fingerprint: str | None = Field(
        default=None, pattern=_FINGERPRINT_PATTERN
    )
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None = None


@runtime_checkable
class IdempotencyStore(Protocol):
    """Optional durable capability: bounded idempotency-key records.

    Implemented by the SQLite store; the in-memory store intentionally does
    not implement it because in-process state cannot survive a restart that
    would make duplicate suppression meaningful.
    """

    def reserve_idempotency(
        self,
        key: str,
        *,
        request_id: str,
        workflow_id: str,
        tenant_scope_fingerprint: str | None = None,
        expires_at: datetime | None = None,
    ) -> IdempotencyRecord:
        """Bind a key to one request/scope; conflict or completed records are returned."""
        ...

    def complete_idempotency(
        self,
        key: str,
        *,
        workflow_id: str,
        terminal_outcome_fingerprint: str,
        tenant_scope_fingerprint: str | None = None,
    ) -> IdempotencyRecord:
        """Store the terminal outcome reference on a reserved key."""
        ...

    def get_idempotency(
        self, key: str, *, tenant_scope_fingerprint: str | None = None
    ) -> IdempotencyRecord | None:
        """Retrieve a key record within the matching scope namespace."""
        ...


def tenant_scope_namespace(scope_fingerprint: str, *, kind: str = "workflow") -> str:
    """A deterministic tenant namespace derived from a scope fingerprint.

    The namespace carries only the opaque scope reference - never raw
    tenant or principal claims - so scoped records can be routed and
    isolated without persisting identity material.  Equivalent fingerprints
    produce equivalent namespaces; different scopes never collide.
    """
    if re.fullmatch(_FINGERPRINT_PATTERN, scope_fingerprint) is None:
        raise ValueError("scope fingerprint must be a sha256 fingerprint")
    if re.fullmatch(_IDENTIFIER_PATTERN, kind) is None:
        raise ValueError("kind must be an identifier-safe component")
    return f"tenant:{kind}:{scope_fingerprint}"


def serialize_snapshot(state: WorkflowState) -> str:
    """Canonical safe JSON snapshot for durable persistence.

    The output is deterministic for equal states and contains only the safe
    representation produced by ``serialize_safe()`` - never raw prompts,
    queries, results, credentials, or provider objects.
    """
    return json.dumps(
        {"schema_version": SNAPSHOT_SCHEMA_VERSION, "state": state.serialize_safe()},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def deserialize_snapshot(payload: str) -> WorkflowState:
    """Restore a safe snapshot, rejecting unsupported versions and raw fields.

    Unsupported schema versions, malformed JSON, raw payload fields, and
    unknown state fields all fail with a structured
    :class:`WorkflowSerializationError` instead of being guessed at.
    """
    try:
        document = json.loads(payload)
    except (json.JSONDecodeError, TypeError) as error:
        raise WorkflowSerializationError(
            "durable snapshot is not valid JSON",
            code=ErrorCode.INVALID_INPUT,
            details={"cause_type": type(error).__name__},
        ) from error
    if not isinstance(document, dict):
        raise WorkflowSerializationError(
            "durable snapshot must be a JSON object",
            code=ErrorCode.INVALID_INPUT,
        )
    version = document.get("schema_version")
    if version != SNAPSHOT_SCHEMA_VERSION:
        raise WorkflowSerializationError(
            f"unsupported durable snapshot schema version {version!r}",
            details={"schema_version": str(version)},
        )
    state_document = document.get("state")
    if not isinstance(state_document, dict):
        raise WorkflowSerializationError(
            "durable snapshot is missing the safe state object",
            code=ErrorCode.INVALID_INPUT,
        )
    raw_fields = sorted(set(state_document) & RAW_PAYLOAD_FIELDS)
    if raw_fields:
        raise WorkflowSerializationError(
            "durable snapshot contains raw payload fields",
            code=ErrorCode.INVALID_INPUT,
            details={"fields": ",".join(raw_fields)},
        )
    try:
        return WorkflowState.model_validate(state_document)
    except ValidationError as error:
        raise WorkflowSerializationError(
            "durable snapshot is not a valid workflow state",
            code=ErrorCode.INVALID_INPUT,
            details={"cause_type": type(error).__name__},
        ) from error


def terminal_outcome_fingerprint(outcome: QueryOutcome) -> str:
    """Safe fingerprint reference of a terminal public outcome.

    Only stable identity and reference fields are included - status,
    request/workflow identity, protected result fingerprint, and error code
    - never the outcome payload, rows, prompts, or credentials.
    """
    return strict_sha256_fingerprint(
        {
            "status": outcome.status.value,
            "request_id": outcome.request_id,
            "workflow_id": outcome.workflow_id,
            "result_fingerprint": (
                outcome.result.fingerprint if outcome.result is not None else None
            ),
            "error_code": outcome.error.code.value if outcome.error is not None else None,
        }
    )
