"""Optional real-MongoDB metadata discovery profile (production hardening).

Runs the Mongo metadata discoverer against a real MongoDB service with an
isolated database: bounded dotted-path observations, collection/field
allowlists, incomplete-schema semantics, activation policy interplay,
concurrent discovery, sensitive-name/value redaction, and safe
unavailable/unauthorized classification.  When the driver is missing, the
URI is not configured, or the service is unreachable the outcome is
skipped - never a pass.  Every run uses a unique database name with
best-effort cleanup.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import pytest

from nl2data_core.adapters.mongodb.metadata import MongoMetadataDiscoverer
from nl2data_core.adapters.mongodb.pymongo_executor import PyMongoExecutor
from nl2data_core.metadata import (
    DiscoveryAuthorization,
    DiscoveryOutcomeCategory,
    MetadataDiscoveryConfig,
    MetadataObjectKind,
    MetadataTrustLevel,
    MetadataUnauthorizedError,
    MetadataUnavailableError,
    ProductionDiscoveryConfig,
    SnapshotActivationPolicy,
    check_snapshot_activation,
    run_production_discovery,
)

#: Service location; override with NL2DATA_MONGO_URI for CI/dev services.
MONGO_URI = os.environ.get("NL2DATA_MONGO_URI", "mongodb://127.0.0.1:27017")

TENANT = "sha256:" + "a" * 64
IDENTITY = "sha256:" + "b" * 64

CUSTOMERS = (
    {
        "_id": 1,
        "name": "Ada",
        "email": "ada@example.com",
        "billing": {"card_last4": "1234", "limit": 5000},
    },
    {
        "_id": 2,
        "name": "Grace",
        "email": "grace@example.com",
        "billing": {"card_last4": "9876", "limit": 2000},
    },
)

ORDERS = (
    {"_id": 10, "customer_id": 1, "amount": 99.5, "status": "shipped"},
    {"_id": 11, "customer_id": 2, "amount": 12.0, "status": "pending"},
)


def _require_driver() -> None:
    """Skip cleanly when the optional driver is absent (skipped outcome)."""
    if not PyMongoExecutor.driver_available():
        pytest.skip(
            "pymongo is not installed; the real mongodb discovery profile is skipped"
        )


@pytest.fixture(scope="module")
def mongo_source() -> Iterator[dict[str, Any]]:
    """An isolated database with seed documents; skipped when unavailable."""
    _require_driver()
    try:
        import pymongo

        client = pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=2_000)
        client.admin.command("ping")
    except Exception:
        pytest.skip(
            "mongodb service is unavailable; the real mongodb discovery profile "
            "is skipped"
        )
    database_name = f"disco_{uuid4().hex[:10]}"
    database = client[database_name]
    database["customers"].insert_many(list(CUSTOMERS))
    database["orders"].insert_many(list(ORDERS))
    executor = PyMongoExecutor(MONGO_URI, database_name)
    try:
        yield {"executor": executor, "database": database_name}
    finally:
        with contextlib.suppress(Exception):
            client.drop_database(database_name)
        executor.close()
        client.close()


def make_discoverer(
    source: dict[str, Any],
    *,
    allowed_collections: frozenset[str],
    executor: PyMongoExecutor | None = None,
) -> MongoMetadataDiscoverer:
    return MongoMetadataDiscoverer(
        handle=executor if executor is not None else source["executor"],
        source_id="mongodb",
        allowed_collections=allowed_collections,
    )


def make_production_config(
    *,
    allowed_objects: frozenset[str],
    sensitive_name_markers: frozenset[str] = frozenset(),
) -> ProductionDiscoveryConfig:
    return ProductionDiscoveryConfig(
        authorization=DiscoveryAuthorization(
            source_id="mongodb",
            tenant_scope_fingerprint=TENANT,
            discovery_identity_fingerprint=IDENTITY,
            description="real mongodb discovery profile",
        ),
        bounds=MetadataDiscoveryConfig(allowed_objects=allowed_objects),
        sensitive_name_markers=sensitive_name_markers,
    )


def run_discovery(discoverer: MongoMetadataDiscoverer, config: MetadataDiscoveryConfig):
    return asyncio.run(discoverer.discover(config))


class TestDriverBoundary:
    def test_driver_absence_is_skipped(self) -> None:
        """Without the optional driver the outcome is 'skipped', never a pass."""
        _require_driver()


class TestDottedPathDiscovery:
    def test_bounded_paths_and_incomplete_schema(self, mongo_source) -> None:
        discoverer = make_discoverer(
            mongo_source, allowed_collections=frozenset({"customers", "orders"})
        )
        snapshot = run_discovery(discoverer, MetadataDiscoveryConfig())
        assert snapshot.source.source_id == "mongodb"
        assert snapshot.source.catalog_fingerprint.startswith("sha256:")

        by_id = {obj.object_id: obj for obj in snapshot.objects}
        assert set(by_id) == {"customers", "orders"}
        customers = by_id["customers"]
        assert customers.kind is MetadataObjectKind.COLLECTION
        assert customers.observed_incomplete is True
        assert customers.trust_level is MetadataTrustLevel.OBSERVED
        paths = {field.field_id for field in customers.fields}
        assert {"name", "email", "billing.card_last4", "billing.limit"} <= paths
        assert "_id" not in paths
        assert all(field.data_type == "document" for field in customers.fields)
        assert snapshot.freshness.bounded_samples is True

    def test_fingerprint_is_deterministic(self, mongo_source) -> None:
        discoverer = make_discoverer(
            mongo_source, allowed_collections=frozenset({"customers", "orders"})
        )
        first = run_discovery(discoverer, MetadataDiscoveryConfig())
        second = run_discovery(discoverer, MetadataDiscoveryConfig())
        assert second.fingerprint == first.fingerprint

    def test_allowlist_narrows_collections_and_paths(self, mongo_source) -> None:
        discoverer = make_discoverer(
            mongo_source, allowed_collections=frozenset({"customers", "orders"})
        )
        narrowed = run_discovery(
            discoverer,
            MetadataDiscoveryConfig(
                allowed_objects=frozenset({"orders"}),
                allowed_fields=frozenset({"amount", "status"}),
            ),
        )
        assert [obj.object_id for obj in narrowed.objects] == ["orders"]
        assert {field.field_id for field in narrowed.objects[0].fields} == {
            "amount",
            "status",
        }

    def test_empty_allowlist_fails_closed(self, mongo_source) -> None:
        discoverer = make_discoverer(
            mongo_source, allowed_collections=frozenset()
        )
        with pytest.raises(MetadataUnauthorizedError):
            run_discovery(discoverer, MetadataDiscoveryConfig())


class TestIncompleteSchemaActivation:
    def test_observed_schema_never_activates_by_default(self, mongo_source) -> None:
        """Incomplete snapshots are evidence, not activatable schema."""
        discoverer = make_discoverer(
            mongo_source, allowed_collections=frozenset({"customers"})
        )
        snapshot = run_discovery(discoverer, MetadataDiscoveryConfig())
        check = check_snapshot_activation(snapshot, SnapshotActivationPolicy())
        assert check.allowed is False
        assert "snapshot_partial" in check.issue_codes()
        # An explicit host policy may accept the observed incompleteness.
        permissive = check_snapshot_activation(
            snapshot, SnapshotActivationPolicy(allow_partial=True)
        )
        assert permissive.allowed is True


class TestBoundsAndClassification:
    def test_concurrent_discoveries_are_independent(self, mongo_source) -> None:
        discoverer = make_discoverer(
            mongo_source, allowed_collections=frozenset({"customers", "orders"})
        )

        async def both() -> tuple[Any, Any]:
            config = MetadataDiscoveryConfig()
            return await asyncio.gather(
                discoverer.discover(config), discoverer.discover(config)
            )

        first, second = asyncio.run(both())
        assert first.fingerprint == second.fingerprint

    def test_unreachable_service_classifies_safely(self, mongo_source) -> None:
        unreachable = PyMongoExecutor("mongodb://127.0.0.1:59999", "disco_missing")
        discoverer = make_discoverer(
            mongo_source,
            allowed_collections=frozenset({"customers"}),
            executor=unreachable,
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
        assert result.outcome.snapshot_fingerprint is None


class TestRedaction:
    def test_sensitive_names_are_counted_never_named(self, mongo_source) -> None:
        discoverer = make_discoverer(
            mongo_source, allowed_collections=frozenset({"customers", "orders"})
        )
        config = make_production_config(
            allowed_objects=frozenset({"customers", "orders"}),
            sensitive_name_markers=frozenset({"card_last4"}),
        )
        result = asyncio.run(run_production_discovery(discoverer, config))
        assert result.outcome.outcome is DiscoveryOutcomeCategory.SUCCEEDED
        assert result.outcome.redacted_sensitive_fields == 1
        payload = json.dumps(result.outcome.safe_payload())
        assert "card_last4" not in payload
        assert "1234" not in payload
        assert "ada@example.com" not in payload
