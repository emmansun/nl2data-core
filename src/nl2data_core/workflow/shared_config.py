"""Validated bounds for the shared PostgreSQL workflow state store.

The configuration carries only behavior bounds - never connection strings
or credentials.  A DSN (when the host manages its own pool) is passed to the
store constructor and is never part of the model, so a dumped configuration
cannot leak PostgreSQL endpoints or secrets.  Every bound is validated at
construction so an empty schema namespace, an unsafe pool/timeout/TTL value,
or an impossible renewal margin fails before any client is built.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .postgres_schema import SUPPORTED_SCHEMA_VERSION

#: Schema namespace: bounded identifier so every derived table name stays safe.
_SCHEMA_PATTERN = r"^[A-Za-z][A-Za-z0-9_]{0,63}$"

#: Hard limits for the validated bounds below.
_MAX_POOL_SIZE = 64
_MAX_CONNECT_TIMEOUT_SECONDS = 30.0
_MAX_COMMAND_TIMEOUT_SECONDS = 120.0
_MAX_ACQUIRE_TIMEOUT_SECONDS = 60.0
_MAX_CLEANUP_BATCH = 10_000
_MAX_LEASE_TTL_SECONDS = 86_400.0  # one day
_MAX_RENEWAL_MARGIN_SECONDS = 3_600.0
_MAX_CLOCK_TOLERANCE_SECONDS = 60.0


class SharedStoreConfig(BaseModel):
    """Immutable bounded configuration for one shared state store.

    ``namespace`` is required and must be unique per application and
    environment: it names the PostgreSQL schema that owns every workflow,
    idempotency, and lease table, so two deployments sharing one database
    service never observe each other's records.  All other fields bound the
    store's work so a pathological pool, timeout, cleanup pass, or lease
    timing can never produce unbounded behavior.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: PostgreSQL schema owning all shared records (never a raw tenant id).
    namespace: str = Field(pattern=_SCHEMA_PATTERN)
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
    #: Maximum schema version this store operates on; newer schemas reject.
    schema_version: int = Field(
        default=SUPPORTED_SCHEMA_VERSION, ge=1, le=SUPPORTED_SCHEMA_VERSION
    )
    #: Maximum records removed by one bounded cleanup pass.
    cleanup_batch_size: int = Field(default=500, ge=1, le=_MAX_CLEANUP_BATCH)
    #: Default lease TTL applied when an acquire call omits ``ttl_seconds``.
    lease_ttl_seconds: float = Field(default=120.0, ge=1.0, le=_MAX_LEASE_TTL_SECONDS)
    #: Renewal is attempted when less than this margin remains on the lease.
    lease_renewal_margin_seconds: float = Field(
        default=20.0, ge=0.1, le=_MAX_RENEWAL_MARGIN_SECONDS
    )
    #: Conservative clock-skew tolerance applied to every server-time check.
    clock_tolerance_seconds: float = Field(
        default=2.0, ge=0.0, le=_MAX_CLOCK_TOLERANCE_SECONDS
    )

    @model_validator(mode="after")
    def _consistent_lease_timing(self) -> SharedStoreConfig:
        if self.lease_renewal_margin_seconds >= self.lease_ttl_seconds:
            raise ValueError(
                "lease renewal margin must be smaller than the lease TTL"
            )
        return self
