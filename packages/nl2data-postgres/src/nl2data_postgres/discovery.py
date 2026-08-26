"""PostgreSQL metadata discovery behind the provider-neutral core contract."""

from __future__ import annotations

import asyncio
import re
import time
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
)

from .client import PostgresPool
from .config import PostgresAdapterConfig

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$")


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


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


class PostgresMetadataDiscoverer:
    """Read-only PostgreSQL metadata discovery over the core contract."""

    def __init__(
        self,
        config: PostgresAdapterConfig,
        *,
        allowed_objects: frozenset[str] = frozenset(),
        allowed_fields: frozenset[str] = frozenset(),
    ) -> None:
        self._config = config
        self._allowed_objects = allowed_objects
        self._allowed_fields = allowed_fields
        self._pool = PostgresPool(config)

    def capability(self) -> MetadataDiscoveryCapability:
        """Declare the discovery bounds this backend supports."""
        return MetadataDiscoveryCapability(
            backend="sql:postgresql",
            supported=True,
            max_objects=1_024,
            max_fields_per_object=16_384,
            supports_statistics=True,
            supports_sampling=False,
            description="bounded postgresql catalog introspection",
        )

    async def discover(self, config: MetadataDiscoveryConfig) -> MetadataSnapshot:
        """Discover a bounded canonical snapshot of the configured source."""
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
            self._discover_sync, config, effective_objects, self._allowed_fields
        )

    async def close(self) -> None:
        """Release the underlying connection pool."""
        self._pool.close()


    def _discover_sync(
        self,
        config: MetadataDiscoveryConfig,
        allowed_objects: frozenset[str],
        allowed_fields: frozenset[str],
    ) -> MetadataSnapshot:
        """Synchronous PostgreSQL discovery using the discoverer's bounded pool."""
        with self._pool.connection() as connection:
            return _introspect(connection, self._config, config, allowed_objects, allowed_fields)


def _introspect(
    connection: Any,
    pg_config: PostgresAdapterConfig,
    config: MetadataDiscoveryConfig,
    allowed_objects: frozenset[str],
    allowed_fields: frozenset[str],
) -> MetadataSnapshot:
    schema = pg_config.schema_name or "public"
    objects: list[MetadataObject] = []
    relationships: list[MetadataRelationship] = []
    evidence: list[MetadataEvidence] = []
    bounded_objects = False
    bounded_fields = False
    bounded_samples = False
    statistic_count = 0
    deadline = time.monotonic() + config.timeout_seconds

    timeout_ms = max(1, int(config.timeout_seconds * 1000))
    connection.execute(f"SET statement_timeout = {timeout_ms}")

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
    selected = [(name, kind) for name, kind in catalog_objects if name in allowed_objects]
    if len(selected) > config.max_objects:
        selected = selected[: config.max_objects]
        bounded_objects = True

    for name, kind in selected:
        if time.monotonic() >= deadline:
            raise MetadataDiscoveryError(
                "metadata discovery exceeded the authorized timeout",
                details={"timeout_seconds": str(config.timeout_seconds)},
            )
        object_kind = MetadataObjectKind.VIEW if kind == "VIEW" else MetadataObjectKind.TABLE
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
            if allowed_fields and column_name not in allowed_fields:
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
            evidence_id=f"pg-obj-{name}",
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
            unique_by_name.setdefault(str(constraint_name), []).append(str(column_name))
        field_ids = {field.field_id for field in fields}
        for constraint_name, unique_columns in unique_by_name.items():
            bounded_columns = [column for column in unique_columns if column in field_ids]
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
            if str(target_table) not in allowed_objects or str(from_column) not in field_ids:
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
            "fingerprints": sorted(evidence_item.reference for evidence_item in evidence),
        }
    )
    return MetadataSnapshot(
        snapshot_id=f"pg-{source_digest[-16:]}",
        source=MetadataSourceReference(
            source_id=pg_config.source_id or "postgresql",
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
                {"backend": "sql:postgresql", "schema": schema}
            ),
            method="postgresql_introspection",
            evidence=tuple(evidence),
        ),
    )
