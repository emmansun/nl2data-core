"""Bounded SQL metadata discovery over the provider-neutral contract.

The SQL discoverer inspects an authorized read-only database - tables,
views, columns/types, primary/unique/foreign keys, and protected row-count
statistics - and maps everything into the common ``MetadataSnapshot``
contract.  Discovery honors the object/field allowlist (fail closed), the
bounded configuration (objects, fields, samples, statistics, timeout), and
normalizes every failure into the safe ``MetadataDiscoveryError`` family:
credentials, DSNs, native exceptions, and raw rows never cross the
boundary.
"""

from __future__ import annotations

import asyncio
import re
import sqlite3
import time
from pathlib import Path

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


def _sql_type(declared: str) -> str:
    """Normalize a declared SQL type into a bounded canonical name."""
    base = declared.split("(", 1)[0].strip().upper()
    return base[:64] or "UNKNOWN"


class SqlMetadataDiscoverer:
    """Read-only SQL metadata discovery over one bounded database.

    ``allowed_objects`` is the source-level authorization allowlist: an
    empty set denies every object (fail closed).  The call-time
    :class:`MetadataDiscoveryConfig` may narrow the allowlist further and
    bounds objects, fields, samples, statistics, and the command timeout.
    """

    def __init__(
        self,
        *,
        dialect: str = "sqlite",
        db_path: Path | None = None,
        allowed_objects: frozenset[str] = frozenset(),
        allowed_fields: frozenset[str] = frozenset(),
    ) -> None:
        self._dialect = dialect
        self._db_path = db_path
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
            description="bounded sqlite catalog introspection",
        )

    async def discover(self, config: MetadataDiscoveryConfig) -> MetadataSnapshot:
        """Discover a bounded canonical snapshot of the configured source."""
        if self._dialect != "sqlite":
            raise MetadataDiscoveryError(
                f"discovery is not implemented for dialect '{self._dialect}'",
                details={"dialect": self._dialect},
            )
        if self._db_path is None:
            raise MetadataUnavailableError(
                "discovery requires a configured database path",
                details={"cause_type": "MissingDatabasePath"},
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
        if len(catalog_objects) > len(selected):
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
                source_id="sqlite",
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
