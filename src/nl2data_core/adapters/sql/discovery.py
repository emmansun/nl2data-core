"""Bounded SQL metadata discovery over the provider-neutral contract.

The SQL discoverer inspects an authorized read-only database - tables,
views, columns/types, primary/unique/foreign keys, and protected row-count
statistics - and maps everything into the common ``MetadataSnapshot``
contract.  The ``sqlite`` dialect opens a read-only local file; the
``postgresql`` dialect opens a read-only transaction over an authorized
DSN (lazily imported ``psycopg``) and introspects ``information_schema``/
``pg_catalog`` with bounded connect/statement timeouts.  Discovery honors
the object/field allowlist (fail closed), the bounded configuration
(objects, fields, samples, statistics, timeout), and normalizes every
failure into the safe ``MetadataDiscoveryError`` family: credentials,
DSNs, native exceptions, and raw rows never cross the boundary.
"""

from __future__ import annotations

import asyncio
import re
import sqlite3
import time
import warnings
from importlib import import_module
from pathlib import Path
from typing import Any

from nl2data_core.canonical import sha256_fingerprint
from nl2data_core.metadata.models import (
    MetadataConstraint,
    MetadataConstraintKind,
    MetadataEvidence,
    MetadataField,
    MetadataFreshness,
    MetadataObject,
    MetadataObjectKind,
    MetadataProvenance,
    MetadataRelationship,
    MetadataRelationshipKind,
    MetadataSnapshot,
    MetadataSourceReference,
    MetadataStatistic,
    MetadataStatisticKind,
    MetadataTrustLevel,
)
from nl2data_core.metadata.protocol import (
    MetadataDiscoveryCapability,
    MetadataDiscoveryConfig,
    MetadataDiscoveryError,
    MetadataUnauthorizedError,
    MetadataUnavailableError,
)

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$")

#: SQLite objects that are never discovered.
_SYSTEM_PREFIXES = ("sqlite_",)

#: Bounded connect timeout applied before the call-time discovery timeout.
_POSTGRES_CONNECT_TIMEOUT = 5.0

#: SQLSTATE classes treated as authorization denials during introspection.
_AUTH_SQLSTATES = frozenset({"28P01", "42501", "42502"})


def _quote_ident(name: str) -> str:
    """Quote a catalog identifier for SQL construction (names are bounded)."""
    return '"' + name.replace('"', '""') + '"'


def _raise_postgres_error(error: BaseException, message: str) -> None:
    """Normalize a PostgreSQL failure into the safe discovery error family.

    SQLSTATE-based authorization denials become :class:`MetadataUnauthorizedError`;
    connection-level failures become retryable :class:`MetadataUnavailableError`;
    everything else becomes a bounded :class:`MetadataDiscoveryError`.  Only the
    class name and SQLSTATE are inspected - driver text and DSNs never cross
    the boundary.
    """
    cause_type = error.__class__.__name__
    sqlstate = getattr(error, "sqlstate", None)
    if sqlstate in _AUTH_SQLSTATES or cause_type in {
        "InsufficientPrivilege",
        "InvalidPassword",
        "PasswordMismatch",
    }:
        raise MetadataUnauthorizedError(
            message, details={"cause_type": cause_type}
        ) from error
    if cause_type in {"OperationalError", "InterfaceError", "ConnectionError"}:
        raise MetadataUnavailableError(
            message, details={"cause_type": cause_type}
        ) from error
    raise MetadataDiscoveryError(message, details={"cause_type": cause_type}) from error


def _sql_type(declared: str) -> str:
    """Normalize a declared SQL type into a bounded canonical name."""
    base = declared.split("(", 1)[0].strip().upper()
    aliases = {
        "CHARACTER VARYING": "VARCHAR",
        "CHAR VARYING": "VARCHAR",
        "CHARACTER": "CHAR",
        "DOUBLE PRECISION": "DOUBLE",
        "TIMESTAMP WITHOUT TIME ZONE": "TIMESTAMP",
        "TIMESTAMP WITH TIME ZONE": "TIMESTAMPTZ",
    }
    base = aliases.get(base, base)
    return base[:64] or "UNKNOWN"


class SqlMetadataDiscoverer:
    """Read-only SQL metadata discovery over one bounded database.

    ``allowed_objects`` is the source-level authorization allowlist: an
    empty set denies every object (fail closed).  The call-time
    :class:`MetadataDiscoveryConfig` may narrow the allowlist further and
    bounds objects, fields, samples, statistics, and the command timeout.
    ``sqlite`` requires ``db_path``; ``postgresql`` requires a read-only
    ``dsn`` (and optionally a bounded ``schema``).
    """

    def __init__(
        self,
        *,
        dialect: str = "sqlite",
        db_path: Path | None = None,
        dsn: str | None = None,
        schema: str | None = None,
        source_id: str | None = None,
        allowed_objects: frozenset[str] = frozenset(),
        allowed_fields: frozenset[str] = frozenset(),
    ) -> None:
        self._dialect = dialect
        self._db_path = db_path
        self._dsn = dsn
        self._schema = schema
        self._source_id = source_id
        self._allowed_objects = allowed_objects
        self._allowed_fields = allowed_fields

    def capability(self) -> MetadataDiscoveryCapability:
        """Declare the discovery bounds this backend supports."""
        return MetadataDiscoveryCapability(
            backend=f"sql:{self._dialect}",
            supported=True,
            max_objects=1_024,
            max_fields_per_object=16_384,
            supports_statistics=True,
            supports_sampling=False,
            description=(
                "bounded sqlite catalog introspection"
                if self._dialect == "sqlite"
                else "bounded postgresql catalog introspection"
            ),
        )

    async def discover(self, config: MetadataDiscoveryConfig) -> MetadataSnapshot:
        """Discover a bounded canonical snapshot of the configured source."""
        if self._dialect not in {"sqlite", "postgresql"}:
            raise MetadataDiscoveryError(
                f"discovery is not implemented for dialect '{self._dialect}'",
                details={"dialect": self._dialect},
            )
        if self._dialect == "sqlite" and self._db_path is None:
            raise MetadataUnavailableError(
                "discovery requires a configured database path",
                details={"cause_type": "MissingDatabasePath"},
            )
        if self._dialect == "postgresql" and self._dsn is None:
            raise MetadataUnavailableError(
                "postgresql discovery requires a configured read-only DSN",
                details={"cause_type": "MissingDSN"},
            )
        if self._dialect == "postgresql":
            warnings.warn(
                "nl2data_core.adapters.sql.SqlMetadataDiscoverer(dialect='postgresql') "
                "is deprecated; migrate to nl2data_postgres.PostgresMetadataDiscoverer.",
                DeprecationWarning,
                stacklevel=2,
            )
        effective_objects = (
            self._allowed_objects & config.allowed_objects
            if config.allowed_objects
            else self._allowed_objects
        )
        if not effective_objects:
            raise MetadataUnauthorizedError(
                "no objects are authorized for metadata discovery",
                details={"authorized_objects": str(len(self._allowed_objects))},
            )
        return await asyncio.to_thread(
            self._discover_sync, config, effective_objects
        )

    def _discover_sync(
        self, config: MetadataDiscoveryConfig, allowed_objects: frozenset[str]
    ) -> MetadataSnapshot:
        if self._dialect == "sqlite":
            return self._discover_sqlite_sync(config, allowed_objects)
        return self._discover_postgres_sync(config, allowed_objects)

    def _discover_sqlite_sync(
        self, config: MetadataDiscoveryConfig, allowed_objects: frozenset[str]
    ) -> MetadataSnapshot:
        try:
            connection = sqlite3.connect(
                f"file:{self._db_path}?mode=ro", uri=True, timeout=config.timeout_seconds
            )
        except sqlite3.Error as error:
            raise MetadataUnavailableError(
                "could not open the read-only database for discovery",
                details={"cause_type": type(error).__name__},
            ) from error

        try:
            with connection:
                connection.execute("PRAGMA query_only = ON")
                deadline = time.monotonic() + config.timeout_seconds
                connection.set_progress_handler(
                    lambda: 1 if time.monotonic() >= deadline else 0,
                    1_000,
                )
                return self._introspect(connection, config, allowed_objects, deadline)
        except MetadataDiscoveryError:
            raise
        except sqlite3.Error as error:
            raise MetadataDiscoveryError(
                "SQL metadata discovery failed",
                details={"cause_type": type(error).__name__},
            ) from error
        finally:
            connection.close()

        # The timeout check above raises; this keeps type checkers honest.
        raise AssertionError("unreachable")

    def _discover_postgres_sync(
        self, config: MetadataDiscoveryConfig, allowed_objects: frozenset[str]
    ) -> MetadataSnapshot:
        """Read-only PostgreSQL catalog discovery with bounded timeouts.

        The connection is opened with a bounded connect timeout, forced into
        a read-only transaction before any catalog read, and every statement
        is bounded by ``statement_timeout`` plus a client-side deadline, so
        discovery can never write and never run unbounded work.
        """
        try:
            driver = import_module("psycopg")
        except ImportError as error:
            raise MetadataUnavailableError(
                "the psycopg driver is not installed; postgresql discovery "
                "is unavailable",
                details={"cause_type": "ImportError"},
            ) from error
        try:
            connection = driver.connect(
                self._dsn,
                connect_timeout=min(
                    config.timeout_seconds, _POSTGRES_CONNECT_TIMEOUT
                ),
            )
        except Exception as error:
            raise MetadataUnavailableError(
                "could not connect for postgresql discovery",
                details={"cause_type": type(error).__name__},
            ) from error
        try:
            timeout_ms = max(1, int(config.timeout_seconds * 1000))
            deadline = time.monotonic() + config.timeout_seconds
            with connection.transaction():
                connection.execute("SET TRANSACTION READ ONLY")
                connection.execute(f"SET statement_timeout = {timeout_ms}")
                return self._introspect_postgres(
                    connection, config, allowed_objects, deadline
                )
        except MetadataDiscoveryError:
            raise
        except Exception as error:
            _raise_postgres_error(error, "postgresql metadata discovery failed")
            raise AssertionError("unreachable") from error
        finally:
            connection.close()

    def _introspect_postgres(
        self,
        connection: Any,
        config: MetadataDiscoveryConfig,
        allowed_objects: frozenset[str],
        deadline: float,
    ) -> MetadataSnapshot:
        """Map the bounded PostgreSQL catalog into a canonical snapshot.

        Introspection covers tables/views, column types/nullability, primary
        and unique constraints, foreign-key relationships, and protected
        row-count statistics - all filtered through the allowlist and the
        bounded configuration.
        """
        schema = self._schema or "public"
        objects: list[MetadataObject] = []
        relationships: list[MetadataRelationship] = []
        evidence: list[MetadataEvidence] = []
        bounded_objects = False
        bounded_fields = False
        bounded_samples = False
        statistic_count = 0

        rows = connection.execute(
            "SELECT table_name, table_type FROM information_schema.tables "
            "WHERE table_schema = %s AND table_type IN ('BASE TABLE', 'VIEW') "
            "ORDER BY table_name",
            (schema,),
        ).fetchall()
        catalog_objects = [
            (str(name), str(kind))
            for name, kind in rows
            if _IDENTIFIER_PATTERN.fullmatch(str(name)) is not None
        ]
        selected = [
            (name, kind) for name, kind in catalog_objects if name in allowed_objects
        ]
        if len(selected) > config.max_objects:
            selected = selected[: config.max_objects]
            bounded_objects = True

        for name, kind in selected:
            if time.monotonic() >= deadline:
                raise MetadataDiscoveryError(
                    "metadata discovery exceeded the authorized timeout",
                    details={"timeout_seconds": str(config.timeout_seconds)},
                )
            object_kind = (
                MetadataObjectKind.VIEW if kind == "VIEW" else MetadataObjectKind.TABLE
            )
            columns = connection.execute(
                "SELECT column_name, data_type, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_schema = %s AND table_name = %s "
                "ORDER BY ordinal_position",
                (schema, name),
            ).fetchall()
            if len(columns) > config.max_fields_per_object:
                columns = columns[: config.max_fields_per_object]
                bounded_fields = True

            fields: list[MetadataField] = []
            for column in columns:
                column_name = str(column[0])
                if _IDENTIFIER_PATTERN.fullmatch(column_name) is None:
                    bounded_fields = True
                    continue
                if config.allowed_fields and column_name not in config.allowed_fields:
                    continue
                if self._allowed_fields and column_name not in self._allowed_fields:
                    continue
                fields.append(
                    MetadataField(
                        field_id=column_name,
                        object_id=name,
                        path=column_name,
                        data_type=_sql_type(str(column[1] or "UNKNOWN")),
                        nullable=str(column[2]).lower() != "no",
                        trust_level=MetadataTrustLevel.DECLARED,
                    )
                )
            field_evidence = MetadataEvidence(
                evidence_id=f"sql-obj-{name}",
                kind="object",
                reference=sha256_fingerprint(
                    {"object": name, "fields": sorted(field.field_id for field in fields)}
                ),
                description="postgresql catalog object observation",
            )
            evidence.append(field_evidence)

            constraints: list[MetadataConstraint] = []
            pk_rows = connection.execute(
                "SELECT kcu.column_name "
                "FROM information_schema.table_constraints tc "
                "JOIN information_schema.key_column_usage kcu "
                "  ON kcu.constraint_name = tc.constraint_name "
                " AND kcu.table_schema = tc.table_schema "
                "WHERE tc.table_schema = %s AND tc.table_name = %s "
                "  AND tc.constraint_type = 'PRIMARY KEY' "
                "ORDER BY kcu.ordinal_position",
                (schema, name),
            ).fetchall()
            primary_fields = [str(row[0]) for row in pk_rows if row[0] is not None]
            if primary_fields:
                constraints.append(
                    MetadataConstraint(
                        constraint_id=f"{name}_pk",
                        kind=MetadataConstraintKind.PRIMARY_KEY,
                        fields=frozenset(primary_fields),
                        trust_level=MetadataTrustLevel.DECLARED,
                    )
                )
            unique_by_name: dict[str, list[str]] = {}
            for constraint_name, column_name in connection.execute(
                "SELECT tc.constraint_name, kcu.column_name "
                "FROM information_schema.table_constraints tc "
                "JOIN information_schema.key_column_usage kcu "
                "  ON kcu.constraint_name = tc.constraint_name "
                " AND kcu.table_schema = tc.table_schema "
                "WHERE tc.table_schema = %s AND tc.table_name = %s "
                "  AND tc.constraint_type = 'UNIQUE' "
                "ORDER BY tc.constraint_name, kcu.ordinal_position",
                (schema, name),
            ).fetchall():
                unique_by_name.setdefault(str(constraint_name), []).append(
                    str(column_name)
                )
            field_ids = {field.field_id for field in fields}
            for constraint_name, unique_columns in unique_by_name.items():
                bounded_columns = [
                    column for column in unique_columns if column in field_ids
                ]
                if bounded_columns:
                    constraints.append(
                        MetadataConstraint(
                            constraint_id=f"{name}_{constraint_name}",
                            kind=MetadataConstraintKind.UNIQUE,
                            fields=frozenset(bounded_columns),
                            trust_level=MetadataTrustLevel.DECLARED,
                        )
                    )

            statistics: list[MetadataStatistic] = []
            if config.include_statistics and statistic_count < config.max_statistics:
                count_row = connection.execute(
                    f"SELECT COUNT(*) FROM {_quote_ident(schema)}.{_quote_ident(name)}"
                ).fetchone()
                if count_row is not None:
                    statistics.append(
                        MetadataStatistic(
                            statistic_id=f"{name}_row_count",
                            kind=MetadataStatisticKind.ROW_COUNT,
                            scope_object_id=name,
                            value=float(count_row[0]),
                            trust_level=MetadataTrustLevel.DECLARED,
                        )
                    )
                    statistic_count += 1

            objects.append(
                MetadataObject(
                    object_id=name,
                    kind=object_kind,
                    name=name,
                    fields=tuple(fields),
                    constraints=tuple(constraints),
                    statistics=tuple(statistics),
                    trust_level=MetadataTrustLevel.DECLARED,
                    observed_incomplete=False,
                )
            )

            # -- foreign keys become relationships ----------------------------
            for _constraint_name, from_column, to_column, target_table in (
                connection.execute(
                    "SELECT c.conname, a.attname AS from_column, "
                    "  fa.attname AS to_column, f.relname AS target_table "
                    "FROM pg_constraint c "
                    "JOIN pg_class t ON t.oid = c.conrelid "
                    "JOIN pg_namespace n ON n.oid = t.relnamespace "
                    "CROSS JOIN LATERAL unnest(c.conkey, c.confkey) "
                    "  WITH ORDINALITY AS u(fk_attnum, ref_attnum, ord) "
                    "JOIN pg_attribute a "
                    "  ON a.attrelid = c.conrelid AND a.attnum = u.fk_attnum "
                    "JOIN pg_attribute fa "
                    "  ON fa.attrelid = c.confrelid AND fa.attnum = u.ref_attnum "
                    "JOIN pg_class f ON f.oid = c.confrelid "
                    "WHERE c.contype = 'f' AND n.nspname = %s AND t.relname = %s "
                    "ORDER BY c.conname, u.ord",
                    (schema, name),
                ).fetchall()
            ):
                if (
                    str(target_table) not in allowed_objects
                    or str(from_column) not in field_ids
                ):
                    continue
                relationship_id = f"{name}_{str(target_table)}_via_{str(from_column)}"
                relationships.append(
                    MetadataRelationship(
                        relationship_id=relationship_id,
                        kind=MetadataRelationshipKind.FOREIGN_KEY,
                        source_object_id=name,
                        target_object_id=str(target_table),
                        source_fields=frozenset({str(from_column)}),
                        target_fields=frozenset({str(to_column)}),
                        trust_level=MetadataTrustLevel.DECLARED,
                    )
                )
                constraints.append(
                    MetadataConstraint(
                        constraint_id=f"{relationship_id}_fk",
                        kind=MetadataConstraintKind.FOREIGN_KEY,
                        fields=frozenset({str(from_column)}),
                        trust_level=MetadataTrustLevel.DECLARED,
                    )
                )

        source_digest = sha256_fingerprint(
            {
                "objects": sorted(obj.object_id for obj in objects),
                "fingerprints": sorted(
                    evidence_item.reference for evidence_item in evidence
                ),
            }
        )
        return MetadataSnapshot(
            snapshot_id=f"sql-{source_digest[-16:]}",
            source=MetadataSourceReference(
                source_id=self._source_id or "postgresql",
                catalog_fingerprint=source_digest,
                description="read-only postgresql catalog",
            ),
            objects=tuple(objects),
            relationships=tuple(relationships),
            freshness=MetadataFreshness(
                bounded_objects=bounded_objects,
                bounded_fields=bounded_fields,
                bounded_samples=bounded_samples,
                sample_limit=config.max_samples,
            ),
            provenance=MetadataProvenance(
                discovered_by_fingerprint=sha256_fingerprint(
                    {"backend": f"sql:{self._dialect}", "schema": schema}
                ),
                method="postgresql_introspection",
                evidence=tuple(evidence),
            ),
        )

    def _introspect(
        self,
        connection: sqlite3.Connection,
        config: MetadataDiscoveryConfig,
        allowed_objects: frozenset[str],
        deadline: float,
    ) -> MetadataSnapshot:
        objects: list[MetadataObject] = []
        relationships: list[MetadataRelationship] = []
        evidence: list[MetadataEvidence] = []
        bounded_objects = False
        bounded_fields = False
        bounded_samples = False
        statistic_count = 0

        rows = connection.execute(
            "SELECT name, type FROM sqlite_master "
            "WHERE type IN ('table', 'view') ORDER BY name"
        ).fetchall()
        catalog_objects = [
            (str(name), str(kind))
            for name, kind in rows
            if not str(name).startswith(_SYSTEM_PREFIXES)
        ]
        selected = [
            (name, kind)
            for name, kind in catalog_objects
            if name in allowed_objects
        ]
        if len(selected) > config.max_objects:
            selected = selected[: config.max_objects]
            bounded_objects = True

        for name, kind in selected:
            if time.monotonic() >= deadline:
                raise MetadataDiscoveryError(
                    "metadata discovery exceeded the authorized timeout",
                    details={"timeout_seconds": str(config.timeout_seconds)},
                )
            object_kind = (
                MetadataObjectKind.VIEW if kind == "view" else MetadataObjectKind.TABLE
            )
            columns = connection.execute(f'PRAGMA table_info("{name}")').fetchall()
            if len(columns) > config.max_fields_per_object:
                columns = columns[: config.max_fields_per_object]
                bounded_fields = True

            fields: list[MetadataField] = []
            primary_fields: list[str] = []
            for column in columns:
                column_name = str(column[1])
                if _IDENTIFIER_PATTERN.fullmatch(column_name) is None:
                    bounded_fields = True
                    continue
                if config.allowed_fields and column_name not in config.allowed_fields:
                    continue
                if self._allowed_fields and column_name not in self._allowed_fields:
                    continue
                declared_type = str(column[2] or "UNKNOWN")
                fields.append(
                    MetadataField(
                        field_id=column_name,
                        object_id=name,
                        path=column_name,
                        data_type=_sql_type(declared_type),
                        nullable=int(column[3] or 0) == 0,
                        trust_level=MetadataTrustLevel.DECLARED,
                    )
                )
                if int(column[5] or 0) > 0:
                    primary_fields.append(column_name)
            field_evidence = MetadataEvidence(
                evidence_id=f"sql-obj-{name}",
                kind="object",
                reference=sha256_fingerprint(
                    {"object": name, "fields": sorted(field.field_id for field in fields)}
                ),
                description="sqlite catalog object observation",
            )
            evidence.append(field_evidence)

            constraints: list[MetadataConstraint] = []
            if primary_fields:
                constraints.append(
                    MetadataConstraint(
                        constraint_id=f"{name}_pk",
                        kind=MetadataConstraintKind.PRIMARY_KEY,
                        fields=frozenset(primary_fields),
                        trust_level=MetadataTrustLevel.DECLARED,
                    )
                )
            for index_row in connection.execute(
                f'PRAGMA index_list("{name}")'
            ).fetchall():
                index_name = str(index_row[1])
                if not int(index_row[2]):
                    continue
                index_columns = [
                    str(row[2])
                    for row in connection.execute(f'PRAGMA index_info("{index_name}")')
                    if row[2] is not None
                ]
                bounded_columns = [
                    column
                    for column in index_columns
                    if column in {field.field_id for field in fields}
                ]
                if bounded_columns:
                    constraints.append(
                        MetadataConstraint(
                            constraint_id=f"{name}_{index_name}",
                            kind=MetadataConstraintKind.UNIQUE,
                            fields=frozenset(bounded_columns),
                            trust_level=MetadataTrustLevel.DECLARED,
                        )
                    )

            statistics: list[MetadataStatistic] = []
            if config.include_statistics and statistic_count < config.max_statistics:
                count_row = connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()
                if count_row is not None:
                    statistics.append(
                        MetadataStatistic(
                            statistic_id=f"{name}_row_count",
                            kind=MetadataStatisticKind.ROW_COUNT,
                            scope_object_id=name,
                            value=float(count_row[0]),
                            trust_level=MetadataTrustLevel.DECLARED,
                        )
                    )
                    statistic_count += 1

            objects.append(
                MetadataObject(
                    object_id=name,
                    kind=object_kind,
                    name=name,
                    fields=tuple(fields),
                    constraints=tuple(constraints),
                    statistics=tuple(statistics),
                    trust_level=MetadataTrustLevel.DECLARED,
                    observed_incomplete=False,
                )
            )

            # -- foreign keys become relationships ----------------------------
            for fk_row in connection.execute(
                f'PRAGMA foreign_key_list("{name}")'
            ).fetchall():
                if len(fk_row) < 8 or fk_row[2] is None:
                    continue
                target_table = str(fk_row[2])
                from_column = str(fk_row[3])
                to_column = str(fk_row[4]) if fk_row[4] is not None else from_column
                if (
                    target_table not in allowed_objects
                    or from_column not in {field.field_id for field in fields}
                ):
                    continue
                relationship_id = f"{name}_{target_table}_via_{from_column}"
                relationships.append(
                    MetadataRelationship(
                        relationship_id=relationship_id,
                        kind=MetadataRelationshipKind.FOREIGN_KEY,
                        source_object_id=name,
                        target_object_id=target_table,
                        source_fields=frozenset({from_column}),
                        target_fields=frozenset({to_column}),
                        trust_level=MetadataTrustLevel.DECLARED,
                    )
                )
                constraints.append(
                    MetadataConstraint(
                        constraint_id=f"{relationship_id}_fk",
                        kind=MetadataConstraintKind.FOREIGN_KEY,
                        fields=frozenset({from_column}),
                        trust_level=MetadataTrustLevel.DECLARED,
                    )
                )

        source_digest = sha256_fingerprint(
            {
                "objects": sorted(obj.object_id for obj in objects),
                "fingerprints": sorted(
                    evidence_item.reference for evidence_item in evidence
                ),
            }
        )
        return MetadataSnapshot(
            snapshot_id=f"sql-{source_digest[-16:]}",
            source=MetadataSourceReference(
                source_id=self._source_id or "sqlite",
                catalog_fingerprint=source_digest,
                description="read-only sqlite catalog",
            ),
            objects=tuple(objects),
            relationships=tuple(relationships),
            freshness=MetadataFreshness(
                bounded_objects=bounded_objects,
                bounded_fields=bounded_fields,
                bounded_samples=bounded_samples,
                sample_limit=config.max_samples,
            ),
            provenance=MetadataProvenance(
                discovered_by_fingerprint=sha256_fingerprint(
                    {"backend": f"sql:{self._dialect}"}
                ),
                method="sqlite_introspection",
                evidence=tuple(evidence),
            ),
        )
