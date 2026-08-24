"""SQL-specific models: dialect profiles, parsed artifacts, and guard results.

These models never cross the public boundary; they are the specialization
layer behind the generic ``QueryAdapter`` contract.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from nl2data_core.canonical import sha256_fingerprint

_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"
_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"


class SQLDialect(StrEnum):
    """Dialects with an adapter capability profile."""

    SQLITE = "sqlite"
    POSTGRES = "postgres"


class SQLDialectProfile(BaseModel):
    """Capability profile of one SQL dialect."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dialect: str = Field(min_length=1, max_length=32)
    supports_cte: bool = True
    supports_grouping: bool = True
    supports_union: bool = True
    parameter_placeholder: str = Field(min_length=1, max_length=4)
    read_only_supported: bool = True
    max_limit: int = Field(default=1_000_000, ge=1, le=1_000_000_000)


DIALECT_PROFILES: dict[str, SQLDialectProfile] = {
    SQLDialect.SQLITE.value: SQLDialectProfile(
        dialect="sqlite",
        parameter_placeholder="?",
        read_only_supported=True,
    ),
    SQLDialect.POSTGRES.value: SQLDialectProfile(
        dialect="postgres",
        parameter_placeholder="%s",
        read_only_supported=True,
    ),
}


class SQLParsedArtifact(BaseModel):
    """A parsed SQL artifact with authoritative AST-derived facts."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    sql_text: str = Field(min_length=1, max_length=1_000_000)
    dialect: str = Field(min_length=1, max_length=32)
    statement_type: str = Field(min_length=1, max_length=32)
    tables: tuple[str, ...] = Field(default_factory=tuple, max_length=1_000)
    columns: tuple[str, ...] = Field(default_factory=tuple, max_length=10_000)
    has_limit: bool = False
    limit_value: int | None = Field(default=None, ge=1, le=1_000_000_000)
    uses_cte: bool = False
    uses_star: bool = False


class SQLGuardResult(BaseModel):
    """Structured result of the read-only/single-statement guard.

    ``obligations_verified`` are the mandatory filter obligations the
    statement demonstrably enforces (semantic fingerprint space);
    ``bounded_rows`` is the bounded row count the executor will apply.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    accepted: bool
    reasons: tuple[str, ...] = Field(default_factory=tuple, max_length=32)
    fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    obligations_verified: frozenset[str] = Field(default_factory=frozenset)
    bounded_rows: int | None = Field(default=None, ge=1, le=1_000_000_000)

    @property
    def rejected(self) -> bool:
        return not self.accepted


def sql_artifact_fingerprint(sql_text: str, dialect: str) -> str:
    """Stable fingerprint of a SQL artifact; identical text yields identical digests."""
    return sha256_fingerprint({"sql": sql_text, "dialect": dialect})


def sql_guard_fingerprint(parsed: SQLParsedArtifact, policy_hash: str) -> str:
    """Stable fingerprint of validated facts and the applied guard policy."""
    return sha256_fingerprint(
        {
            "artifact": parsed.fingerprint,
            "statement_type": parsed.statement_type,
            "tables": sorted(parsed.tables),
            "columns": sorted(parsed.columns),
            "limit": parsed.limit_value,
            "policy": policy_hash,
        }
    )
