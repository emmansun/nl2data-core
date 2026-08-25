"""Optional real-PostgreSQL metadata discovery profile (production hardening).

Runs the SQL metadata discoverer against a real PostgreSQL service with an
isolated schema: table/view/type/key/relationship/statistics assertions,
object/field allowlists, least-privilege read-only identities, bounded
timeouts, concurrent discovery, sensitive-name/value redaction, and safe
unavailable/unauthorized classification.  When the driver is missing, the
DSN is not configured, or the service is unreachable the outcome is
skipped - never a pass.  Every run uses a unique schema namespace and
role identities with best-effort cleanup.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from collections.abc import Iterator
from importlib import import_module
from importlib.util import find_spec
from typing import Any
from uuid import uuid4

import pytest

from nl2data_core.adapters.sql.discovery import SqlMetadataDiscoverer
from nl2data_core.metadata import (
    DiscoveryAuthorization,
    DiscoveryOutcomeCategory,
    MetadataConstraintKind,
    MetadataDiscoveryConfig,
    MetadataObjectKind,
    MetadataRelationshipKind,
    MetadataStatisticKind,
    MetadataUnauthorizedError,
    MetadataUnavailableError,
    ProductionDiscoveryConfig,
    run_production_discovery,
)

#: Service location; override with NL2DATA_POSTGRES_DSN for CI/dev services.
DSN = os.environ.get(
    "NL2DATA_POSTGRES_DSN", "postgres://postgres:postgres@127.0.0.1:5432/postgres"
)

TENANT = "sha256:" + "a" * 64
IDENTITY = "sha256:" + "b" * 64


def _driver_available() -> bool:
    """Whether the optional ``psycopg`` driver is installed."""
    return find_spec("psycopg") is not None


def _connect(dsn: str) -> Any:
    driver = import_module("psycopg")
    return driver.connect(dsn, connect_timeout=2.0)


def _require_driver() -> None:
    """Skip cleanly when the optional driver is absent (skipped outcome)."""
    if not _driver_available():
        pytest.skip(
            "psycopg is not installed; the real postgres discovery profile is skipped"
        )


@pytest.fixture(scope="module")
def postgres_source() -> Iterator[dict[str, Any]]:
    """An isolated schema plus least-privilege identities; skipped when unavailable."""
    _require_driver()
    try:
        connection = _connect(DSN)
    except Exception:
        pytest.skip(
            "postgres service is unavailable; the real postgres discovery profile "
            "is skipped"
        )
    namespace = f"disco_{uuid4().hex[:10]}"
    reader_role = f"disco_reader_{uuid4().hex[:6]}"
    denied_role = f"disco_denied_{uuid4().hex[:6]}"
    password = f"pw_{uuid4().hex[:12]}"
    try:
        with connection:
            connection.execute(f'CREATE SCHEMA "{namespace}"')
            connection.execute(
                f'CREATE TABLE "{namespace}".customers ('
                "id BIGINT PRIMARY KEY, email VARCHAR(255) NOT NULL, "
                "region TEXT, created_at TIMESTAMPTZ)"
            )
            connection.execute(
                f'CREATE TABLE "{namespace}".orders ('
                "id BIGINT PRIMARY KEY, "
                f'customer_id BIGINT NOT NULL REFERENCES "{namespace}".customers(id), '
                "amount NUMERIC(12,2) NOT NULL, status TEXT, "
                "CONSTRAINT orders_customer_status_unique UNIQUE (customer_id, status))"
            )
            connection.execute(
                f'INSERT INTO "{namespace}".customers VALUES '
                "(1, 'ada@example.com', 'EMEA', now()), "
                "(2, 'grace@example.com', 'APAC', now())"
            )
            connection.execute(
                f'INSERT INTO "{namespace}".orders VALUES '
                "(10, 1, 99.50, 'shipped'), (11, 2, 12.00, 'pending')"
            )
            connection.execute(
                f'CREATE VIEW "{namespace}".customer_regions AS '
                f"SELECT region, COUNT(*) AS customer_count "
                f'FROM "{namespace}".customers GROUP BY region'
            )
            for role_name in (reader_role, denied_role):
                connection.execute(
                    f'CREATE ROLE "{role_name}" LOGIN PASSWORD %s', (password,)
                )
                connection.execute(
                    f'GRANT USAGE ON SCHEMA "{namespace}" TO "{role_name}"'
                )
            connection.execute(
                f'GRANT SELECT ON ALL TABLES IN SCHEMA "{namespace}" '
                f'TO "{reader_role}"'
            )
        conninfo = import_module("psycopg").conninfo
        yield {
            "dsn": DSN,
            "schema": namespace,
            "reader_dsn": conninfo.make_conninfo(DSN, user=reader_role, password=password),
            "denied_dsn": conninfo.make_conninfo(DSN, user=denied_role, password=password),
        }
    finally:
        with contextlib.suppress(Exception), _connect(DSN) as cleanup:
            cleanup.execute(f'DROP SCHEMA IF EXISTS "{namespace}" CASCADE')
            cleanup.execute(f'DROP OWNED BY "{reader_role}"')
            cleanup.execute(f'DROP ROLE IF EXISTS "{reader_role}"')
            cleanup.execute(f'DROP OWNED BY "{denied_role}"')
            cleanup.execute(f'DROP ROLE IF EXISTS "{denied_role}"')
            cleanup.commit()


def make_discoverer(
    source: dict[str, Any],
    *,
    allowed_objects: frozenset[str],
    dsn: str | None = None,
) -> SqlMetadataDiscoverer:
    """A PostgreSQL discoverer bound to the isolated fixture schema."""
    return SqlMetadataDiscoverer(
        dialect="postgresql",
        dsn=dsn if dsn is not None else source["dsn"],
        schema=source["schema"],
        allowed_objects=allowed_objects,
    )


def make_production_config(
    *,
    allowed_objects: frozenset[str],
    sensitive_name_markers: frozenset[str] = frozenset(),
) -> ProductionDiscoveryConfig:
    return ProductionDiscoveryConfig(
        authorization=DiscoveryAuthorization(
            source_id="postgresql",
            tenant_scope_fingerprint=TENANT,
            discovery_identity_fingerprint=IDENTITY,
            description="real postgres discovery profile",
        ),
        bounds=MetadataDiscoveryConfig(allowed_objects=allowed_objects),
        sensitive_name_markers=sensitive_name_markers,
    )


def run_discovery(discoverer: SqlMetadataDiscoverer, config: MetadataDiscoveryConfig):
    return asyncio.run(discoverer.discover(config))


class TestDriverBoundary:
    def test_driver_absence_is_skipped(self) -> None:
        """Without the optional driver the outcome is 'skipped', never a pass."""
        _require_driver()


class TestCatalogStructure:
    def test_types_keys_relationships_and_statistics(self, postgres_source) -> None:
        discoverer = make_discoverer(
            postgres_source,
            allowed_objects=frozenset({"customers", "orders", "customer_regions"}),
        )
        snapshot = run_discovery(discoverer, MetadataDiscoveryConfig())
        assert snapshot.source.source_id == "postgresql"
        assert snapshot.source.catalog_fingerprint.startswith("sha256:")

        by_id = {obj.object_id: obj for obj in snapshot.objects}
        assert set(by_id) == {"customers", "orders", "customer_regions"}

        customers = by_id["customers"]
        assert customers.kind is MetadataObjectKind.TABLE
        customer_fields = {field.field_id: field for field in customers.fields}
        assert set(customer_fields) == {"id", "email", "region", "created_at"}
        assert customer_fields["email"].data_type == "VARCHAR"
        assert customer_fields["email"].nullable is False
        assert customer_fields["region"].data_type == "TEXT"
        primary_keys = [
            constraint
            for constraint in customers.constraints
            if constraint.kind is MetadataConstraintKind.PRIMARY_KEY
        ]
        assert primary_keys and primary_keys[0].fields == frozenset({"id"})

        orders = by_id["orders"]
        order_fields = {field.field_id: field for field in orders.fields}
        assert order_fields["amount"].data_type == "NUMERIC"
        unique = [
            constraint
            for constraint in orders.constraints
            if constraint.kind is MetadataConstraintKind.UNIQUE
        ]
        assert unique and unique[0].fields == frozenset({"customer_id", "status"})

        foreign_keys = [
            relationship
            for relationship in snapshot.relationships
            if relationship.kind is MetadataRelationshipKind.FOREIGN_KEY
        ]
        assert len(foreign_keys) == 1
        assert foreign_keys[0].source_object_id == "orders"
        assert foreign_keys[0].target_object_id == "customers"
        assert foreign_keys[0].source_fields == frozenset({"customer_id"})
        assert foreign_keys[0].target_fields == frozenset({"id"})

        assert by_id["customer_regions"].kind is MetadataObjectKind.VIEW

        statistics = {statistic.statistic_id: statistic for statistic in orders.statistics}
        assert statistics["orders_row_count"].kind is MetadataStatisticKind.ROW_COUNT
        assert statistics["orders_row_count"].value == 2.0
        customer_statistics = {
            statistic.statistic_id: statistic for statistic in customers.statistics
        }
        assert customer_statistics["customers_row_count"].value == 2.0

    def test_fingerprint_is_deterministic(self, postgres_source) -> None:
        discoverer = make_discoverer(
            postgres_source, allowed_objects=frozenset({"customers", "orders"})
        )
        first = run_discovery(discoverer, MetadataDiscoveryConfig())
        second = run_discovery(discoverer, MetadataDiscoveryConfig())
        assert second.fingerprint == first.fingerprint
        assert second.source.catalog_fingerprint == first.source.catalog_fingerprint

    def test_allowlist_narrows_objects_and_fields(self, postgres_source) -> None:
        discoverer = make_discoverer(
            postgres_source, allowed_objects=frozenset({"customers", "orders"})
        )
        narrowed = run_discovery(
            discoverer,
            MetadataDiscoveryConfig(
                allowed_objects=frozenset({"orders"}),
                allowed_fields=frozenset({"id", "amount"}),
            ),
        )
        assert [obj.object_id for obj in narrowed.objects] == ["orders"]
        assert {field.field_id for field in narrowed.objects[0].fields} == {
            "id",
            "amount",
        }
        # Unknown allowlist members are excluded, never fabricated.
        unknown = run_discovery(
            discoverer,
            MetadataDiscoveryConfig(
                allowed_objects=frozenset({"customers", "missing_table"})
            ),
        )
        assert [obj.object_id for obj in unknown.objects] == ["customers"]

    def test_empty_allowlist_fails_closed(self, postgres_source) -> None:
        discoverer = SqlMetadataDiscoverer(
            dialect="postgresql",
            dsn=postgres_source["dsn"],
            schema=postgres_source["schema"],
            allowed_objects=frozenset(),
        )
        with pytest.raises(MetadataUnauthorizedError):
            run_discovery(discoverer, MetadataDiscoveryConfig())


class TestIdentitiesAndPermissions:
    def test_least_privilege_read_only_identity_succeeds(self, postgres_source) -> None:
        """A SELECT-only role is sufficient: discovery never writes."""
        discoverer = make_discoverer(
            postgres_source,
            allowed_objects=frozenset({"customers"}),
            dsn=postgres_source["reader_dsn"],
        )
        snapshot = run_discovery(discoverer, MetadataDiscoveryConfig())
        assert [obj.object_id for obj in snapshot.objects] == ["customers"]

    def test_privilege_aware_catalog_visibility(self, postgres_source) -> None:
        """Schema USAGE without SELECT sees no tables: privileges bound the catalog."""
        discoverer = make_discoverer(
            postgres_source,
            allowed_objects=frozenset({"customers"}),
            dsn=postgres_source["denied_dsn"],
        )
        snapshot = run_discovery(
            discoverer, MetadataDiscoveryConfig(include_statistics=False)
        )
        assert snapshot.objects == ()

    def test_invalid_identity_fails_closed_as_unavailable(self, postgres_source) -> None:
        conninfo = import_module("psycopg").conninfo
        bad_dsn = conninfo.make_conninfo(DSN, password="wrong-password-xyz")
        discoverer = make_discoverer(
            postgres_source,
            allowed_objects=frozenset({"customers"}),
            dsn=bad_dsn,
        )
        with pytest.raises(MetadataUnavailableError):
            run_discovery(discoverer, MetadataDiscoveryConfig())
        result = asyncio.run(
            run_production_discovery(
                discoverer, make_production_config(allowed_objects=frozenset({"customers"}))
            )
        )
        assert result.outcome.outcome is DiscoveryOutcomeCategory.UNAVAILABLE
        assert result.outcome.error_category == "unavailable"
        assert result.snapshot is None


class TestBoundsAndConcurrency:
    def test_bounded_timeout_is_honored(self, postgres_source) -> None:
        discoverer = make_discoverer(
            postgres_source, allowed_objects=frozenset({"customers", "orders"})
        )
        snapshot = run_discovery(
            discoverer, MetadataDiscoveryConfig(timeout_seconds=5.0)
        )
        assert len(snapshot.objects) == 2

    def test_concurrent_discoveries_are_independent(self, postgres_source) -> None:
        discoverer = make_discoverer(
            postgres_source, allowed_objects=frozenset({"customers", "orders"})
        )

        async def both() -> tuple[Any, Any]:
            config = MetadataDiscoveryConfig()
            return await asyncio.gather(
                discoverer.discover(config), discoverer.discover(config)
            )

        first, second = asyncio.run(both())
        assert first.fingerprint == second.fingerprint
        assert first.source.catalog_fingerprint == second.source.catalog_fingerprint

    def test_unreachable_service_classifies_safely(self, postgres_source) -> None:
        unreachable = SqlMetadataDiscoverer(
            dialect="postgresql",
            dsn="postgres://postgres:postgres@127.0.0.1:1/postgres",
            schema="public",
            allowed_objects=frozenset({"customers"}),
        )
        with pytest.raises(MetadataUnavailableError):
            run_discovery(unreachable, MetadataDiscoveryConfig())
        result = asyncio.run(
            run_production_discovery(
                unreachable, make_production_config(allowed_objects=frozenset({"customers"}))
            )
        )
        assert result.outcome.outcome is DiscoveryOutcomeCategory.UNAVAILABLE
        assert result.outcome.error_category == "unavailable"
        assert result.outcome.snapshot_fingerprint is None

    def test_missing_dsn_fails_closed(self) -> None:
        discoverer = SqlMetadataDiscoverer(
            dialect="postgresql", allowed_objects=frozenset({"customers"})
        )
        with pytest.raises(MetadataUnavailableError):
            run_discovery(discoverer, MetadataDiscoveryConfig())


class TestRedaction:
    def test_sensitive_names_are_counted_never_named(self, postgres_source) -> None:
        discoverer = make_discoverer(
            postgres_source, allowed_objects=frozenset({"customers", "orders"})
        )
        config = make_production_config(
            allowed_objects=frozenset({"customers", "orders"}),
            sensitive_name_markers=frozenset({"email"}),
        )
        result = asyncio.run(run_production_discovery(discoverer, config))
        assert result.outcome.outcome is DiscoveryOutcomeCategory.SUCCEEDED
        assert result.outcome.redacted_sensitive_fields == 1
        assert result.outcome.redacted_sensitive_objects == 0
        payload = json.dumps(result.outcome.safe_payload())
        assert "email" not in payload
        assert "ada@example.com" not in payload
        assert "postgres://" not in payload and "password" not in payload
