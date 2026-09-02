"""Validated bounds for the optional PostgreSQL semantic catalog.

The configuration carries only behavior bounds and safe secret *references* -
never connection strings, DSNs, or credentials.  The host injects the actual
DSN into the catalog constructor from its own secret management; a dumped or
logged configuration therefore cannot leak PostgreSQL endpoints or secrets.
Every bound is validated at construction so an unsafe namespace, pool,
timeout, retention, envelope limit, or schema version fails before any client
is built, and an unsupported schema version fails closed.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .schema import SUPPORTED_SCHEMA_VERSION

#: Schema namespace: bounded identifier so every derived table name stays safe.
_SCHEMA_PATTERN = r"^[A-Za-z][A-Za-z0-9_]{0,63}$"

#: Secret reference: a bounded host-side name (environment variable, vault
#: key, ...) - never the secret value itself.
_SECRET_REF_PATTERN = r"^[A-Za-z0-9_][A-Za-z0-9_\-\.]{0,127}$"

#: Hard limits for the validated bounds below.
_MAX_POOL_SIZE = 64
_MAX_CONNECT_TIMEOUT_SECONDS = 30.0
_MAX_COMMAND_TIMEOUT_SECONDS = 120.0
_MAX_ACQUIRE_TIMEOUT_SECONDS = 60.0
_MAX_SNAPSHOT_RETENTION_SECONDS = 31_536_000.0  # one year
_MAX_EVENT_RETENTION_SECONDS = 31_536_000.0  # one year
_MAX_CLEANUP_BATCH = 10_000
_MAX_MAX_ENVELOPE_BYTES = 16 * 1024 * 1024
_MIN_MAX_ENVELOPE_BYTES = 4 * 1024
_MAX_MAX_PAYLOAD_BYTES = 8 * 1024 * 1024
_MIN_MAX_PAYLOAD_BYTES = 1 * 1024
_MAX_BUNDLE_HISTORY = 10_000
_MAX_ACTIVE_POINTERS_PER_SCOPE = 1_024


class SemanticCatalogConfig(BaseModel):
    """Immutable bounded configuration for one durable catalog.

    ``namespace`` is required and must be unique per application and
    environment: it names the PostgreSQL schema that owns every catalog table,
    so two deployments sharing one database service never observe each
    other's records.  ``dsn_secret_ref`` names the host-managed secret that
    holds the DSN at construction time; the config itself never carries the
    DSN.  All other fields bound the catalog's work so a pathological pool,
    timeout, retention policy, envelope limit, or history depth can never
    produce unbounded behavior.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: PostgreSQL schema owning all catalog records (never a raw tenant id).
    namespace: str = Field(pattern=_SCHEMA_PATTERN)
    #: Host-side name of the secret holding the DSN (never the DSN itself).
    dsn_secret_ref: str | None = Field(default=None, pattern=_SECRET_REF_PATTERN)
    #: Bounded connection pool size of the lazy psycopg pool.
    pool_size: int = Field(default=5, ge=1, le=_MAX_POOL_SIZE)
    #: Bounded connect timeout for lazy pool connections (seconds).
    connect_timeout_seconds: float = Field(
        default=5.0, ge=0.1, le=_MAX_CONNECT_TIMEOUT_SECONDS
    )
    #: Bounded per-command timeout for every statement (seconds).
    command_timeout_seconds: float = Field(
        default=10.0, ge=0.1, le=_MAX_COMMAND_TIMEOUT_SECONDS
    )
    #: Bounded pool checkout timeout (seconds).
    pool_acquire_timeout_seconds: float = Field(
        default=5.0, ge=0.1, le=_MAX_ACQUIRE_TIMEOUT_SECONDS
    )
    #: Maximum schema version this catalog operates on; newer schemas reject.
    schema_version: int = Field(
        default=SUPPORTED_SCHEMA_VERSION, ge=1, le=SUPPORTED_SCHEMA_VERSION
    )
    #: Default retention applied when a snapshot registration omits one.
    snapshot_retention_seconds: float = Field(
        default=604_800.0, ge=60.0, le=_MAX_SNAPSHOT_RETENTION_SECONDS
    )
    #: Lifecycle events older than this are removed by bounded cleanup.
    event_retention_seconds: float = Field(
        default=604_800.0, ge=60.0, le=_MAX_EVENT_RETENTION_SECONDS
    )
    #: Audit-evidence entries older than this age out unless their bundle
    #: fingerprint belongs to a non-retired published version (or another
    #: protected entry references them as a predecessor).
    audit_retention_seconds: float = Field(
        default=604_800.0, ge=60.0, le=_MAX_EVENT_RETENTION_SECONDS
    )
    #: Maximum records removed by one bounded cleanup pass.
    cleanup_batch_size: int = Field(default=500, ge=1, le=_MAX_CLEANUP_BATCH)
    #: Hard upper bound for one persisted envelope (bytes).
    max_envelope_bytes: int = Field(
        default=1_048_576, ge=_MIN_MAX_ENVELOPE_BYTES, le=_MAX_MAX_ENVELOPE_BYTES
    )
    #: Hard upper bound for the canonical payload inside one envelope (bytes).
    max_payload_bytes: int = Field(
        default=524_288, ge=_MIN_MAX_PAYLOAD_BYTES, le=_MAX_MAX_PAYLOAD_BYTES
    )
    #: Maximum immutable Bundle versions retained per Bundle id.
    max_bundle_history: int = Field(default=100, ge=1, le=_MAX_BUNDLE_HISTORY)
    #: Maximum active Bundle pointers per tenant scope.
    max_active_pointers_per_scope: int = Field(
        default=256, ge=1, le=_MAX_ACTIVE_POINTERS_PER_SCOPE
    )

    @model_validator(mode="after")
    def _consistent_envelope_bounds(self) -> SemanticCatalogConfig:
        if self.max_payload_bytes > self.max_envelope_bytes:
            raise ValueError(
                "max_payload_bytes must not exceed max_envelope_bytes"
            )
        return self

    def safe_payload(self) -> dict[str, object]:
        """Diagnostics dump: bounds and references only, never secrets."""
        return {
            "namespace": self.namespace,
            "dsn_secret_ref": self.dsn_secret_ref,
            "pool_size": self.pool_size,
            "connect_timeout_seconds": self.connect_timeout_seconds,
            "command_timeout_seconds": self.command_timeout_seconds,
            "pool_acquire_timeout_seconds": self.pool_acquire_timeout_seconds,
            "schema_version": self.schema_version,
            "snapshot_retention_seconds": self.snapshot_retention_seconds,
            "event_retention_seconds": self.event_retention_seconds,
            "audit_retention_seconds": self.audit_retention_seconds,
            "cleanup_batch_size": self.cleanup_batch_size,
            "max_envelope_bytes": self.max_envelope_bytes,
            "max_payload_bytes": self.max_payload_bytes,
            "max_bundle_history": self.max_bundle_history,
            "max_active_pointers_per_scope": self.max_active_pointers_per_scope,
        }

