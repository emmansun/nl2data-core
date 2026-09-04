"""Unit tests for the metadata snapshot contract.

Covers construction bounds, immutability, canonical serialization and
fingerprint stability, trust levels, freshness, and the normalized
metadata discovery error family.
"""

from __future__ import annotations

import json
import re

import pytest
from pydantic import ValidationError

from nl2data.errors import ErrorCategory, ErrorCode
from nl2data_core.adapters.sql.discovery import _sql_type
from nl2data_core.canonical import strict_sha256_fingerprint
from nl2data_core.metadata import (
    METADATA_SCHEMA_VERSION,
    MetadataBoundsExceededError,
    MetadataConfidence,
    MetadataConstraint,
    MetadataConstraintKind,
    MetadataDiscoveryConfig,
    MetadataDiscoveryError,
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
    MetadataUnauthorizedError,
    MetadataUnavailableError,
)

_FINGERPRINT = re.compile(r"^sha256:[0-9a-f]{64}$")


def fp(byte: str) -> str:
    """A valid ``sha256:<hex>`` fingerprint filled with one repeated byte."""
    return "sha256:" + byte * 32


def make_snapshot(**overrides) -> MetadataSnapshot:
    values = {
        "snapshot_id": "snap-1",
        "source": MetadataSourceReference(
            source_id="sales",
            catalog_fingerprint=fp("ab"),
            description="Logical sales source",
        ),
        "objects": (
            MetadataObject(
                object_id="orders",
                kind=MetadataObjectKind.TABLE,
                name="orders",
                fields=(
                    MetadataField(
                        field_id="order_id",
                        object_id="orders",
                        path="order_id",
                        data_type="INTEGER",
                        nullable=False,
                        trust_level=MetadataTrustLevel.DECLARED,
                    ),
                    MetadataField(
                        field_id="amount",
                        object_id="orders",
                        path="amount",
                        data_type="REAL",
                        nullable=True,
                        trust_level=MetadataTrustLevel.DECLARED,
                    ),
                ),
                constraints=(
                    MetadataConstraint(
                        constraint_id="orders_pk",
                        kind=MetadataConstraintKind.PRIMARY_KEY,
                        fields=frozenset({"order_id"}),
                        trust_level=MetadataTrustLevel.DECLARED,
                    ),
                ),
                statistics=(
                    MetadataStatistic(
                        statistic_id="orders_row_count",
                        kind=MetadataStatisticKind.ROW_COUNT,
                        scope_object_id="orders",
                        value=42.0,
                        trust_level=MetadataTrustLevel.DECLARED,
                    ),
                ),
                trust_level=MetadataTrustLevel.DECLARED,
            ),
        ),
        "relationships": (
            MetadataRelationship(
                relationship_id="orders_customers_via_customer_id",
                kind=MetadataRelationshipKind.FOREIGN_KEY,
                source_object_id="orders",
                target_object_id="customers",
                source_fields=frozenset({"customer_id"}),
                target_fields=frozenset({"id"}),
                trust_level=MetadataTrustLevel.DECLARED,
            ),
        ),
        "freshness": MetadataFreshness(
            bounded_objects=False,
            bounded_fields=False,
            sample_limit=10,
        ),
        "provenance": MetadataProvenance(
            discovered_by_fingerprint=fp("11"),
            method="test",
            evidence=(
                MetadataEvidence(
                    evidence_id="ev-1",
                    kind="object",
                    reference=fp("22"),
                    description="observation",
                ),
            ),
        ),
    }
    values.update(overrides)
    return MetadataSnapshot(**values)


def make_object(object_id: str, fields: tuple[MetadataField, ...]) -> MetadataObject:
    return MetadataObject(
        object_id=object_id,
        kind=MetadataObjectKind.TABLE,
        name=object_id,
        fields=fields,
    )


class TestSnapshotContract:
    def test_fingerprint_is_computed_and_canonical(self) -> None:
        snapshot = make_snapshot()
        assert _FINGERPRINT.fullmatch(snapshot.fingerprint) is not None
        assert snapshot.fingerprint == strict_sha256_fingerprint(snapshot.canonical_payload())

    def test_fingerprint_is_insertion_order_stable(self) -> None:
        snapshot = make_snapshot()
        other = MetadataObject(
            object_id="customers",
            kind=MetadataObjectKind.TABLE,
            name="customers",
            fields=(
                MetadataField(
                    field_id="id",
                    object_id="customers",
                    path="id",
                    data_type="INTEGER",
                    nullable=False,
                    trust_level=MetadataTrustLevel.DECLARED,
                ),
            ),
        )
        base = make_snapshot(
            objects=(other, snapshot.objects[0]),
            freshness=snapshot.freshness,
            provenance=snapshot.provenance,
        )
        reordered = make_snapshot(
            objects=(snapshot.objects[0], other),
            freshness=snapshot.freshness,
            provenance=snapshot.provenance,
        )
        assert reordered.fingerprint == base.fingerprint

    def test_nested_metadata_order_does_not_change_fingerprint(self) -> None:
        snapshot = make_snapshot()
        original = snapshot.objects[0]
        reordered_object = original.model_copy(
            update={
                "fields": tuple(reversed(original.fields)),
                "constraints": tuple(reversed(original.constraints)),
                "statistics": tuple(reversed(original.statistics)),
            }
        )
        evidence = tuple(reversed(snapshot.provenance.evidence))
        reordered = MetadataSnapshot(
            snapshot_id=snapshot.snapshot_id,
            source=snapshot.source,
            objects=(reordered_object,),
            relationships=snapshot.relationships,
            freshness=snapshot.freshness,
            provenance=snapshot.provenance.model_copy(update={"evidence": evidence}),
        )
        assert reordered.fingerprint == snapshot.fingerprint

    def test_serialize_canonical_is_sorted_json_without_fingerprint(self) -> None:
        snapshot = make_snapshot()
        payload = json.loads(snapshot.serialize_canonical())
        assert list(payload) == sorted(payload)
        assert payload["schema_version"] == METADATA_SCHEMA_VERSION
        assert "fingerprint" not in payload
        safe = snapshot.safe_payload()
        assert safe["fingerprint"] == snapshot.fingerprint

    def test_models_are_immutable_and_reject_extra_fields(self) -> None:
        snapshot = make_snapshot()
        with pytest.raises(ValidationError):
            make_snapshot(unknown_field="nope")
        with pytest.raises(ValidationError):
            MetadataFreshness(sample_limit=0)
        with pytest.raises(ValidationError):
            snapshot.snapshot_id = "other"  # type: ignore[misc]

    def test_schema_version_is_the_supported_literal(self) -> None:
        snapshot = make_snapshot()
        assert snapshot.schema_version == 1
        assert snapshot.schema_version == METADATA_SCHEMA_VERSION
        with pytest.raises(ValidationError):
            make_snapshot(schema_version=2)

    def test_duplicate_object_ids_are_rejected(self) -> None:
        obj = MetadataObject(
            object_id="orders",
            kind=MetadataObjectKind.TABLE,
            name="orders",
        )
        with pytest.raises(ValidationError):
            make_snapshot(objects=(obj, obj))

    def test_bounds_reject_bad_identifiers(self) -> None:
        with pytest.raises(ValidationError):
            make_snapshot(snapshot_id="has space")
        with pytest.raises(ValidationError):
            make_snapshot(
                source=MetadataSourceReference(
                    source_id="bad id",
                    catalog_fingerprint=fp("ab"),
                )
            )
        with pytest.raises(ValidationError):
            MetadataObject(
                object_id="orders",
                kind=MetadataObjectKind.TABLE,
                name="orders",
                fields=(
                    MetadataField(
                        field_id="not valid!",
                        object_id="orders",
                        path="not valid!",
                        data_type="TEXT",
                    ),
                ),
            )

    def test_statistics_and_constraints_round_trip(self) -> None:
        snapshot = make_snapshot()
        obj = snapshot.object("orders")
        assert obj is not None
        assert obj.field_ids() == frozenset({"order_id", "amount"})
        assert obj.constraints[0].kind is MetadataConstraintKind.PRIMARY_KEY
        assert obj.statistics[0].value == 42.0
        assert snapshot.field_path("amount") == "amount"
        assert snapshot.field("missing") is None

    def test_freshness_records_bound_flags(self) -> None:
        snapshot = make_snapshot(
            freshness=MetadataFreshness(
                bounded_objects=True,
                bounded_fields=True,
                bounded_samples=True,
                sample_limit=5,
            )
        )
        assert snapshot.freshness.bounded_objects is True
        assert snapshot.freshness.bounded_fields is True
        assert snapshot.freshness.bounded_samples is True
        assert snapshot.freshness.sample_limit == 5


class TestTrustLevels:
    def test_trust_levels_are_declared_observed_inferred(self) -> None:
        assert MetadataTrustLevel.DECLARED.value == "declared"
        assert MetadataTrustLevel.OBSERVED.value == "observed"
        assert MetadataTrustLevel.INFERRED.value == "inferred"

    def test_field_default_trust_is_observed(self) -> None:
        field = MetadataField(
            field_id="amount",
            object_id="orders",
            path="amount",
            data_type="REAL",
        )
        assert field.trust_level is MetadataTrustLevel.OBSERVED

    def test_confidence_is_bounded(self) -> None:
        confidence = MetadataConfidence(
            value=0.85,
            method="identifier_pattern",
            evidence_ids=frozenset({"ev-1"}),
        )
        assert confidence.value == 0.85
        with pytest.raises(ValidationError):
            MetadataConfidence(value=1.5, method="x")
        with pytest.raises(ValidationError):
            MetadataConfidence(value=-0.1, method="x")
        with pytest.raises(ValidationError):
            MetadataConfidence(value=0.5, method="x", evidence_ids=frozenset({"bad id"}))


class TestDiscoveryConfig:
    def test_sql_type_aliases_are_canonicalized(self) -> None:
        assert _sql_type("character varying(255)") == "VARCHAR"
        assert _sql_type("double precision") == "DOUBLE"

    def test_config_bounds_are_enforced(self) -> None:
        config = MetadataDiscoveryConfig()
        assert config.max_objects == 256
        assert config.timeout_seconds == 30.0
        with pytest.raises(ValidationError):
            MetadataDiscoveryConfig(max_objects=0)
        with pytest.raises(ValidationError):
            MetadataDiscoveryConfig(max_concurrency=99)
        with pytest.raises(ValidationError):
            MetadataDiscoveryConfig(timeout_seconds=0.0)
        with pytest.raises(ValidationError):
            MetadataDiscoveryConfig(allowed_objects=frozenset({"bad object!"}))

    def test_config_is_frozen(self) -> None:
        config = MetadataDiscoveryConfig()
        with pytest.raises(ValidationError):
            config.max_objects = 1  # type: ignore[misc]


class TestErrorNormalization:
    def test_error_codes_and_categories(self) -> None:
        cases = (
            (
                MetadataDiscoveryError("failed"),
                ErrorCategory.ADAPTER,
                ErrorCode.METADATA_DISCOVERY_FAILED,
                False,
            ),
            (
                MetadataUnavailableError("unavailable"),
                ErrorCategory.ADAPTER,
                ErrorCode.METADATA_UNAVAILABLE,
                True,
            ),
            (
                MetadataUnauthorizedError("denied"),
                ErrorCategory.GOVERNANCE,
                ErrorCode.METADATA_UNAUTHORIZED,
                False,
            ),
            (
                MetadataBoundsExceededError("bounded"),
                ErrorCategory.ADAPTER,
                ErrorCode.METADATA_BOUNDS_EXCEEDED,
                False,
            ),
        )
        for error, category, code, retryable in cases:
            assert error.category is category
            assert error.code is code
            assert error.retryable is retryable

    def test_error_details_are_safe_and_bounded(self) -> None:
        error = MetadataUnavailableError(
            "backend unavailable",
            details={"cause_type": "OperationalError", "attempt": "3"},
        )
        safe = error.safe_details()
        assert safe["cause_type"] == "OperationalError"
        assert error.message == "backend unavailable"
        assert error.retryable is True
