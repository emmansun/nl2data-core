"""Unit tests for Semantic Model Bundle contract models.

Covers construction bounds, safe-content rejection, canonical fingerprints
(including insertion-order stability), trust metadata consistency, loader
schema and fingerprint checks, structured validation issues, and the
immutability of every bundle model.
"""

from __future__ import annotations

import json

import pytest
import yaml
from pydantic import ValidationError

from nl2data_core.bundles import (
    BUNDLE_SCHEMA_VERSION,
    BundleCompatibility,
    BundleDependency,
    BundleProvenance,
    BundleQualityStatus,
    CanonicalBundleLoader,
    SemanticGrain,
    SemanticMeasure,
    SemanticModelBundle,
    SemanticSourceReference,
    SemanticTrustKind,
    SemanticTrustMarker,
    validate_bundle,
)
from nl2data_core.views import (
    SemanticDescriptor,
    SemanticEntityDescriptor,
    SemanticFieldDescriptor,
    SemanticRelationshipDescriptor,
)


def fp(byte: str) -> str:
    """A valid ``sha256:<hex>`` fingerprint filled with one repeated byte."""
    return "sha256:" + byte * 32


def make_field(field_id: str = "amount", **overrides) -> SemanticFieldDescriptor:
    values = {
        "field_id": field_id,
        "label": field_id.replace("_", " ").title(),
        "description": f"Semantic field {field_id}",
        "data_type": "decimal" if field_id == "amount" else "string",
        "allowed_aggregations": (
            frozenset({"sum", "avg", "min", "max"}) if field_id == "amount" else frozenset()
        ),
    }
    values.update(overrides)
    return SemanticFieldDescriptor(**values)


def make_descriptor(**overrides) -> SemanticDescriptor:
    values = {
        "descriptor_id": "sales_catalog",
        "version": 1,
        "source_id": "sales",
        "catalog_fingerprint": fp("ab"),
        "entities": (
            SemanticEntityDescriptor(
                entity_id="customer",
                label="Customer",
                fields=(
                    make_field("customer_id", data_type="string"),
                    make_field("name", data_type="string"),
                    make_field("email", data_type="string"),
                ),
            ),
            SemanticEntityDescriptor(
                entity_id="order",
                label="Order",
                fields=(
                    make_field("order_id", data_type="string"),
                    make_field("amount"),
                    make_field("region", data_type="string"),
                    make_field("status", data_type="string"),
                    make_field("created_at", data_type="datetime"),
                ),
                relationships=(
                    SemanticRelationshipDescriptor(
                        relationship_id="customer_orders",
                        source_entity_id="customer",
                        target_entity_id="order",
                        label="Orders of the customer",
                    ),
                ),
            ),
        ),
    }
    values.update(overrides)
    return SemanticDescriptor(**values)


def make_source(**overrides) -> SemanticSourceReference:
    values = {
        "reference_id": "src-sales",
        "source_id": "sales",
        "catalog_fingerprint": fp("ab"),
        "description": "Logical sales source reference",
    }
    values.update(overrides)
    return SemanticSourceReference(**values)


def make_measure(**overrides) -> SemanticMeasure:
    values = {
        "measure_id": "total_amount",
        "field_id": "amount",
        "aggregation": "sum",
        "label": "Total amount",
        "description": "Sum of order amounts",
    }
    values.update(overrides)
    return SemanticMeasure(**values)


def make_grain(**overrides) -> SemanticGrain:
    values = {
        "grain_id": "order_region",
        "entity_id": "order",
        "attributes": frozenset({"region", "status"}),
        "description": "Orders grouped by region",
    }
    values.update(overrides)
    return SemanticGrain(**values)


def make_bundle(**overrides) -> SemanticModelBundle:
    values = {
        "bundle_id": "sales_model",
        "model_version": "1.0.0",
        "descriptor": make_descriptor(),
        "measures": (make_measure(),),
        "grains": (make_grain(),),
        "sources": (make_source(),),
        "trust_markers": (
            SemanticTrustMarker(
                marker_id="m-amount",
                fact_id="amount",
                kind=SemanticTrustKind.AUTHORED,
                note="Authored by the data platform team",
            ),
        ),
        "compatibility": BundleCompatibility(
            compatible_catalog_fingerprints=frozenset({fp("ab")}),
            notes="Compatible with the reference catalog",
        ),
        "provenance": BundleProvenance(
            owner_reference="team-analytics",
            created_by_fingerprint=fp("11"),
            quality=BundleQualityStatus.VALIDATED,
        ),
    }
    values.update(overrides)
    return SemanticModelBundle(**values)


class TestBundleContract:
    def test_bundle_wraps_the_existing_descriptor(self) -> None:
        descriptor = make_descriptor()
        bundle = make_bundle(descriptor=descriptor)
        assert bundle.descriptor is descriptor
        assert bundle.entity_ids() == frozenset({"customer", "order"})
        assert bundle.field_ids() == frozenset(
            {"customer_id", "name", "email", "order_id", "amount", "region", "status", "created_at"}
        )
        assert bundle.relationship_ids() == frozenset({"customer_orders"})

    def test_schema_version_is_the_supported_literal(self) -> None:
        bundle = make_bundle()
        assert bundle.schema_version == 1
        assert bundle.schema_version == BUNDLE_SCHEMA_VERSION
        with pytest.raises(ValidationError):
            make_bundle(schema_version=2)

    def test_bundle_identifiers_are_bounded(self) -> None:
        with pytest.raises(ValidationError):
            make_bundle(bundle_id="has space")
        with pytest.raises(ValidationError):
            make_bundle(model_version="")

    def test_measure_aggregation_is_typed(self) -> None:
        with pytest.raises(ValidationError):
            make_measure(aggregation="median")


class TestFingerprints:
    def test_fingerprint_matches_the_canonical_payload(self) -> None:
        bundle = make_bundle()
        from nl2data_core.canonical import sha256_fingerprint

        assert bundle.fingerprint == sha256_fingerprint(bundle.canonical_payload())
        assert bundle.fingerprint.startswith("sha256:")

    def test_fingerprint_is_stable_across_insertion_order(self) -> None:
        bundle = make_bundle()
        payload = bundle.model_dump(mode="json")
        reversed_payload = dict(reversed(list(payload.items())))
        again = SemanticModelBundle.model_validate(reversed_payload)
        assert again.fingerprint == bundle.fingerprint

    def test_business_version_change_does_not_change_the_fingerprint(self) -> None:
        v1 = make_bundle(model_version="1.0.0")
        v2 = make_bundle(model_version="2.0.0")
        assert v1.fingerprint == v2.fingerprint
        assert v1.bundle_id == v2.bundle_id

    def test_provenance_and_trust_markers_do_not_change_the_fingerprint(self) -> None:
        baseline = make_bundle()
        changed = make_bundle(
            trust_markers=(
                SemanticTrustMarker(
                    marker_id="m-amount",
                    fact_id="amount",
                    kind=SemanticTrustKind.INFERRED,
                    approved=True,
                    note="Approved discovery evidence",
                ),
            ),
            provenance=BundleProvenance(
                owner_reference="team-semantic",
                created_by_fingerprint=fp("22"),
                quality=BundleQualityStatus.APPROVED,
            ),
        )
        assert changed.fingerprint == baseline.fingerprint
        assert changed.file_payload() != baseline.file_payload()

    def test_content_change_changes_the_fingerprint(self) -> None:
        with_measure = make_bundle()
        without_measure = make_bundle(measures=())
        assert with_measure.fingerprint != without_measure.fingerprint

    def test_serialized_canonical_payload_excludes_the_fingerprint(self) -> None:
        payload = json.loads(make_bundle().serialize_canonical())
        assert "fingerprint" not in payload
        assert payload["schema_version"] == 1
        assert "provenance" not in make_bundle().canonical_payload()
        assert "trust_markers" not in make_bundle().canonical_payload()

    def test_canonical_round_trip_via_loader(self) -> None:
        bundle = make_bundle()
        result = CanonicalBundleLoader().load(bundle.serialize_canonical())
        assert result.loaded
        assert result.bundle is not None
        assert result.bundle.fingerprint == bundle.fingerprint
        assert result.bundle.bundle_id == bundle.bundle_id
        assert result.bundle.canonical_payload() == bundle.canonical_payload()

    def test_yaml_key_order_comments_and_formatting_do_not_change_fingerprint(
        self,
    ) -> None:
        envelope = make_bundle().file_payload()
        compact = yaml.safe_dump(envelope, sort_keys=True)
        presented = "# release candidate\n" + yaml.safe_dump(
            dict(reversed(list(envelope.items()))),
            sort_keys=False,
            indent=4,
        )
        first = SemanticModelBundle.model_validate(yaml.safe_load(compact))
        second = SemanticModelBundle.model_validate(yaml.safe_load(presented))
        assert first.fingerprint == second.fingerprint


class TestSafeContent:
    def test_credential_marker_is_rejected_in_measure_description(self) -> None:
        with pytest.raises(ValidationError):
            make_measure(description="uses password=hunter2 to connect")

    def test_connection_material_is_rejected_in_grain_description(self) -> None:
        with pytest.raises(ValidationError):
            make_grain(description="bound to mongodb://host:27017")

    def test_executable_sql_text_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_source(description="runs select * from orders")

    def test_owner_reference_is_safe_text(self) -> None:
        with pytest.raises(ValidationError):
            BundleProvenance(owner_reference="owner password=secret")

    def test_safe_metadata_constructs(self) -> None:
        bundle = make_bundle()
        assert bundle.provenance.owner_reference == "team-analytics"
        assert bundle.measures[0].description == "Sum of order amounts"


class TestBounds:
    def test_grain_count_is_bounded(self) -> None:
        grains = tuple(make_grain(grain_id=f"g{i}") for i in range(257))
        with pytest.raises(ValidationError):
            make_bundle(grains=grains)

    def test_grain_attributes_are_bounded(self) -> None:
        attributes = frozenset(f"attr_{i}" for i in range(4097))
        with pytest.raises(ValidationError):
            make_grain(attributes=attributes)

    def test_description_length_is_bounded(self) -> None:
        with pytest.raises(ValidationError):
            make_measure(description="x" * 1025)

    def test_compatibility_fingerprints_are_bounded(self) -> None:
        fingerprints = frozenset(fp(f"{i:02x}") for i in range(65))
        with pytest.raises(ValidationError):
            BundleCompatibility(compatible_catalog_fingerprints=fingerprints)

    def test_compatibility_fingerprints_must_be_sha256(self) -> None:
        with pytest.raises(ValidationError):
            BundleCompatibility(compatible_catalog_fingerprints=frozenset({"not-a-fp"}))

    def test_duplicate_measure_ids_are_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_bundle(measures=(make_measure(), make_measure()))

    def test_duplicate_grain_ids_are_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_bundle(grains=(make_grain(), make_grain()))

    def test_duplicate_source_reference_ids_are_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_bundle(sources=(make_source(), make_source()))

    def test_duplicate_dependency_ids_are_rejected(self) -> None:
        dependency = BundleDependency(
            dependency_id="dep-base",
            bundle_id="base_model",
            version="1.0.0",
            fingerprint=fp("22"),
        )
        with pytest.raises(ValidationError):
            make_bundle(dependencies=(dependency, dependency))

    def test_duplicate_trust_marker_ids_are_rejected(self) -> None:
        marker = SemanticTrustMarker(
            marker_id="m-amount", fact_id="amount", kind=SemanticTrustKind.AUTHORED
        )
        with pytest.raises(ValidationError):
            make_bundle(trust_markers=(marker, marker))


class TestTrustMetadata:
    def test_approved_markers_require_approval(self) -> None:
        with pytest.raises(ValidationError):
            SemanticTrustMarker(
                marker_id="m1", fact_id="amount", kind=SemanticTrustKind.APPROVED
            )
        marker = SemanticTrustMarker(
            marker_id="m1", fact_id="amount", kind=SemanticTrustKind.APPROVED, approved=True
        )
        assert marker.approved

    def test_authored_markers_cannot_carry_a_separate_approval(self) -> None:
        with pytest.raises(ValidationError):
            SemanticTrustMarker(
                marker_id="m1", fact_id="amount", kind=SemanticTrustKind.AUTHORED, approved=True
            )
        marker = SemanticTrustMarker(
            marker_id="m1", fact_id="amount", kind=SemanticTrustKind.AUTHORED
        )
        assert not marker.approved

    def test_inferred_markers_may_carry_approval(self) -> None:
        unapproved = SemanticTrustMarker(
            marker_id="m1", fact_id="amount", kind=SemanticTrustKind.INFERRED
        )
        approved = SemanticTrustMarker(
            marker_id="m2",
            fact_id="customer_orders",
            kind=SemanticTrustKind.INFERRED,
            approved=True,
        )
        assert not unapproved.approved
        assert approved.approved

    def test_trust_marker_referencing_unknown_fact_is_invalid(self) -> None:
        bundle = make_bundle(
            trust_markers=(
                SemanticTrustMarker(
                    marker_id="m-ghost", fact_id="ghost", kind=SemanticTrustKind.INFERRED
                ),
            )
        )
        result = validate_bundle(bundle)
        assert not result.valid
        assert "unknown_fact" in result.issue_codes()


class TestProvenanceAndQuality:
    def test_draft_bundles_are_rejected(self) -> None:
        bundle = make_bundle(
            provenance=BundleProvenance(
                owner_reference="team-analytics", quality=BundleQualityStatus.DRAFT
            )
        )
        result = validate_bundle(bundle)
        assert not result.valid
        assert "quality_not_met" in result.issue_codes()

    def test_validated_and_approved_bundles_are_valid(self) -> None:
        assert validate_bundle(make_bundle()).valid
        approved = make_bundle(
            provenance=BundleProvenance(
                owner_reference="team-analytics", quality=BundleQualityStatus.APPROVED
            )
        )
        assert validate_bundle(approved).valid


class TestStructuralValidation:
    def test_source_identity_must_match_descriptor(self) -> None:
        bundle = make_bundle(sources=(make_source(source_id="inventory"),))
        result = validate_bundle(bundle)
        assert not result.valid
        assert "source_identity_mismatch" in result.issue_codes()

    def test_declared_catalog_compatibility_must_match_descriptor(self) -> None:
        bundle = make_bundle(
            compatibility=BundleCompatibility(
                compatible_catalog_fingerprints=frozenset({fp("00")})
            )
        )
        result = validate_bundle(bundle)
        assert not result.valid
        assert "catalog_incompatible" in result.issue_codes()

    def test_bundle_cannot_depend_on_itself(self) -> None:
        bundle = make_bundle(
            dependencies=(
                BundleDependency(
                    dependency_id="dep-self",
                    bundle_id="sales_model",
                    version="1.0.0",
                    fingerprint=fp("00"),
                ),
            )
        )
        result = validate_bundle(bundle)
        assert not result.valid
        assert "self_dependency" in result.issue_codes()

    def test_missing_sources_is_invalid(self) -> None:
        result = validate_bundle(make_bundle(sources=()))
        assert not result.valid
        assert "missing_sources" in result.issue_codes()

    def test_unknown_measure_field_is_invalid(self) -> None:
        bundle = make_bundle(measures=(make_measure(field_id="ghost"),))
        result = validate_bundle(bundle)
        assert not result.valid
        assert "unknown_field" in result.issue_codes()
        assert result.issues[0].member_id == "ghost"

    def test_aggregation_beyond_descriptor_is_invalid(self) -> None:
        bundle = make_bundle(measures=(make_measure(aggregation="count"),))
        result = validate_bundle(bundle)
        assert not result.valid
        assert "aggregation_not_allowed" in result.issue_codes()

    def test_unknown_grain_entity_is_invalid(self) -> None:
        bundle = make_bundle(grains=(make_grain(entity_id="ghost"),))
        result = validate_bundle(bundle)
        assert not result.valid
        assert "unknown_entity" in result.issue_codes()

    def test_unknown_grain_attribute_is_invalid(self) -> None:
        bundle = make_bundle(grains=(make_grain(attributes=frozenset({"ghost"})),))
        result = validate_bundle(bundle)
        assert not result.valid
        assert "unknown_attribute" in result.issue_codes()

    def test_unsupported_schema_version_is_invalid(self) -> None:
        result = validate_bundle(make_bundle(), supported_schema_versions=(2,))
        assert not result.valid
        assert "incompatible_schema" in result.issue_codes()

    def test_issues_are_structured_and_safe(self) -> None:
        bundle = make_bundle(
            sources=(),
            provenance=BundleProvenance(
                owner_reference="team-analytics", quality=BundleQualityStatus.DRAFT
            ),
        )
        result = validate_bundle(bundle)
        assert not result.valid
        assert len(result.issues) == 2
        for issue in result.issues:
            assert issue.code
            assert issue.message
            payload = issue.safe_payload()
            assert payload["code"] == issue.code
            assert "credential" not in json.dumps(payload)

    def test_issue_truncation_reports_the_total_count(self) -> None:
        measures = tuple(
            make_measure(measure_id=f"measure-{index}", field_id=f"ghost-{index}")
            for index in range(65)
        )
        result = validate_bundle(make_bundle(measures=measures))
        assert len(result.issues) == 64
        assert result.issue_count == 65
        assert result.truncated


class TestLoader:
    def test_malformed_payload_is_invalid(self) -> None:
        result = CanonicalBundleLoader().load("not json at all")
        assert result.kind == "invalid"
        assert "malformed_payload" in result.issue_codes()
        assert result.bundle is None

    def test_non_object_payload_is_invalid(self) -> None:
        result = CanonicalBundleLoader().load("[1, 2, 3]")
        assert result.kind == "invalid"
        assert "malformed_payload" in result.issue_codes()

    def test_unsupported_schema_version_is_incompatible(self) -> None:
        data = json.loads(make_bundle().serialize_canonical())
        data["schema_version"] = 2
        result = CanonicalBundleLoader().load(json.dumps(data))
        assert result.kind == "incompatible_schema"
        assert "incompatible_schema" in result.issue_codes()
        assert result.bundle is None

    def test_fingerprint_mismatch_is_invalid(self) -> None:
        data = json.loads(make_bundle().serialize_canonical())
        data["fingerprint"] = fp("00")
        result = CanonicalBundleLoader().load(json.dumps(data))
        assert result.kind == "invalid"
        assert "fingerprint_mismatch" in result.issue_codes()

    def test_invalid_payload_issues_are_structured(self) -> None:
        data = json.loads(make_bundle().serialize_canonical())
        data["measures"] = [{"measure_id": "incomplete"}]
        result = CanonicalBundleLoader().load(json.dumps(data))
        assert result.kind == "invalid"
        assert "invalid_payload" in result.issue_codes()
        assert result.bundle is None

    def test_failed_loads_never_carry_a_bundle(self) -> None:
        loader = CanonicalBundleLoader()
        for payload in ("{", "[]", json.dumps({"schema_version": 99})):
            result = loader.load(payload)
            assert result.bundle is None
            assert not result.loaded


class TestImmutability:
    def test_bundle_is_frozen(self) -> None:
        bundle = make_bundle()
        with pytest.raises((TypeError, ValidationError)):
            bundle.bundle_id = "other_id"

    def test_bundle_members_are_frozen(self) -> None:
        bundle = make_bundle()
        with pytest.raises((TypeError, ValidationError)):
            bundle.measures[0].label = "Changed"

    def test_bundle_collections_are_immutable_tuples(self) -> None:
        bundle = make_bundle()
        assert isinstance(bundle.measures, tuple)
        assert isinstance(bundle.sources, tuple)
        assert isinstance(bundle.trust_markers, tuple)
