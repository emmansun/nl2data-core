"""Validated bounds for the shared Redis memory provider.

The configuration carries only behavior bounds - never connection strings
or credentials.  A connection ``url`` (when the host manages its own
client) is passed to the provider constructor and is never part of the
model, so a dumped configuration cannot leak Redis endpoints or secrets.
Every bound is validated at construction so an empty namespace, an
invalid TTL/capacity/timeout value, or an unsafe key component fails
before any client is built.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

#: Provider namespace: bounded identifier so every derived key stays small.
_NAMESPACE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,63}$"

#: Hard limits for the validated bounds below.
_MAX_TTL_SECONDS = 3_153_600  # 365 days (matches the record model bound)
_MAX_RECORDS = 1_000_000
_MAX_CANDIDATES = 10_000
_MAX_BATCH_SIZE = 10_000
_MAX_RETENTION_SECONDS = 3_153_600
_MAX_CONNECT_TIMEOUT_SECONDS = 30.0
_MAX_COMMAND_TIMEOUT_SECONDS = 60.0


class RedisMemoryConfig(BaseModel):
    """Immutable bounded configuration for one Redis memory provider.

    ``namespace`` is required and must be unique per application and
    environment: it prefixes every record/index/id key so two deployments
    sharing one Redis service never observe each other's records.  All
    other fields bound the provider's work so a pathological index or a
    large session can never produce unbounded reads or writes.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    namespace: str = Field(pattern=_NAMESPACE_PATTERN)
    #: Provider-level TTL cap; records with a longer TTL are rejected.
    max_ttl_seconds: int = Field(default=_MAX_TTL_SECONDS, ge=1, le=_MAX_TTL_SECONDS)
    #: Bounded record capacity per session scope index (best-effort atomic).
    max_records: int = Field(default=10_000, ge=1, le=_MAX_RECORDS)
    #: Maximum candidate record ids loaded by one recall.
    max_candidates: int = Field(default=1_000, ge=1, le=_MAX_CANDIDATES)
    #: Candidate scan batch hint for one recall.
    recall_batch_size: int = Field(default=100, ge=1, le=_MAX_BATCH_SIZE)
    #: Index keys and members scanned by one compaction pass.
    compaction_batch_size: int = Field(default=500, ge=1, le=_MAX_BATCH_SIZE)
    #: How long an expired record id stays reserved before it can be reused.
    expired_id_retention_seconds: int = Field(
        default=3_600, ge=1, le=_MAX_RETENTION_SECONDS
    )
    #: Bounded connect timeout for the lazy client (seconds).
    connect_timeout_seconds: float = Field(
        default=2.0, ge=0.1, le=_MAX_CONNECT_TIMEOUT_SECONDS
    )
    #: Bounded per-command timeout for the lazy client (seconds).
    command_timeout_seconds: float = Field(
        default=2.0, ge=0.1, le=_MAX_COMMAND_TIMEOUT_SECONDS
    )
