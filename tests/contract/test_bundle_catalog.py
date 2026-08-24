"""Contract tests for the Semantic Bundle catalog lifecycle.

Covers publish validation, lookup and version listing, duplicate-version
conflict, atomic activation with dependency fingerprint checks, active
snapshot semantics, and rollback that restores a prior active version
without ever mutating a published artifact.
"""

from __future__ import annotations

from nl2data_core.bundles import (
    BundleDependency,
    BundleProvenance,
    BundleQualityStatus,
    InMemorySemanticBundleCatalog,
    SemanticModelBundle,
    SemanticSourceReference,
)
from nl2data_core.views import (
    SemanticDescriptor,
    SemanticEntityDescriptor,
    SemanticFieldDescriptor,
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
                entity_id="order",
                label="Order",
                fields=(
                    make_field("order_id", data_type="string"),
                    make_field("amount"),
                    make_field("region", data_type="string"),
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


def make_bundle(**overrides) -> SemanticModelBundle:
    values = {
        "bundle_id": "sales_model",
        "model_version": "1.0.0",
        "descriptor": make_descriptor(),
        "sources": (make_source(),),
        "provenance": BundleProvenance(
            owner_reference="team-analytics",
            quality=BundleQualityStatus.VALIDATED,
        ),
    }
    values.update(overrides)
    return SemanticModelBundle(**values)


def make_catalog() -> InMemorySemanticBundleCatalog:
    return InMemorySemanticBundleCatalog()


class TestPublish:
    def test_publish_validated_bundle(self) -> None:
        catalog = make_catalog()
        bundle = make_bundle()
        outcome = catalog.publish(bundle)
        assert outcome.success
        assert outcome.kind == "published"
        assert outcome.bundle is bundle
        assert catalog.get(bundle.bundle_id, bundle.model_version) is bundle

    def test_publish_rejects_draft_bundles(self) -> None:
        catalog = make_catalog()
        draft = make_bundle(
            provenance=BundleProvenance(
                owner_reference="team-analytics", quality=BundleQualityStatus.DRAFT
            )
        )
        outcome = catalog.publish(draft)
        assert outcome.kind == "rejected"
        assert "quality_not_met" in outcome.issue_codes()
        assert catalog.get(draft.bundle_id, draft.model_version) is None

    def test_publish_rejects_incomplete_bundles(self) -> None:
        catalog = make_catalog()
        incomplete = make_bundle(sources=())
        outcome = catalog.publish(incomplete)
        assert outcome.kind == "rejected"
        assert "missing_sources" in outcome.issue_codes()

    def test_publish_rejects_unsupported_schema_versions(self) -> None:
        catalog = InMemorySemanticBundleCatalog(supported_schema_versions=(2,))
        outcome = catalog.publish(make_bundle())
        assert outcome.kind == "rejected"
        assert "incompatible_schema" in outcome.issue_codes()

    def test_duplicate_version_is_a_conflict(self) -> None:
        catalog = make_catalog()
        bundle = make_bundle()
        assert catalog.publish(bundle).kind == "published"
        outcome = catalog.publish(bundle)
        assert outcome.kind == "conflict"
        assert "version_exists" in outcome.issue_codes()
        assert len(catalog.versions(bundle.bundle_id)) == 1

    def test_same_bundle_new_version_is_publishable(self) -> None:
        catalog = make_catalog()
        v1 = make_bundle(model_version="1.0.0")
        v2 = make_bundle(model_version="2.0.0")
        assert catalog.publish(v1).kind == "published"
        assert catalog.publish(v2).kind == "published"


class TestLookupAndVersions:
    def test_get_unknown_bundle_is_none(self) -> None:
        assert make_catalog().get("missing", "1.0.0") is None

    def test_versions_returns_every_published_version(self) -> None:
        catalog = make_catalog()
        v1 = make_bundle(model_version="1.0.0")
        v2 = make_bundle(model_version="2.0.0")
        catalog.publish(v1)
        catalog.publish(v2)
        versions = catalog.versions("sales_model")
        assert versions == (v1, v2)
        assert isinstance(versions, tuple)

    def test_active_is_none_before_activation(self) -> None:
        catalog = make_catalog()
        catalog.publish(make_bundle())
        assert catalog.active("sales_model") is None


class TestActivation:
    def test_activate_publishes_the_active_snapshot(self) -> None:
        catalog = make_catalog()
        bundle = make_bundle()
        catalog.publish(bundle)
        outcome = catalog.activate(bundle.bundle_id, bundle.model_version)
        assert outcome.success
        assert outcome.kind == "activated"
        assert catalog.active(bundle.bundle_id) is bundle

    def test_activate_unknown_version_is_not_found(self) -> None:
        catalog = make_catalog()
        catalog.publish(make_bundle())
        outcome = catalog.activate("sales_model", "9.9.9")
        assert outcome.kind == "not_found"
        assert "bundle_not_found" in outcome.issue_codes()

    def test_activation_requires_published_dependencies(self) -> None:
        catalog = make_catalog()
        dependent = make_bundle(
            bundle_id="dependent_model",
            dependencies=(
                BundleDependency(
                    dependency_id="dep-base",
                    bundle_id="base_model",
                    version="1.0.0",
                    fingerprint=fp("22"),
                ),
            ),
        )
        catalog.publish(dependent)
        outcome = catalog.activate(dependent.bundle_id, dependent.model_version)
        assert outcome.kind == "rejected"
        assert "dependency_unavailable" in outcome.issue_codes()

    def test_activation_rejects_stale_dependency_fingerprints(self) -> None:
        catalog = make_catalog()
        base = make_bundle(bundle_id="base_model")
        catalog.publish(base)
        dependent = make_bundle(
            bundle_id="dependent_model",
            dependencies=(
                BundleDependency(
                    dependency_id="dep-base",
                    bundle_id="base_model",
                    version=base.model_version,
                    fingerprint=fp("00"),
                ),
            ),
        )
        catalog.publish(dependent)
        outcome = catalog.activate(dependent.bundle_id, dependent.model_version)
        assert outcome.kind == "rejected"
        assert "dependency_unavailable" in outcome.issue_codes()

    def test_activation_accepts_matching_dependency_fingerprints(self) -> None:
        catalog = make_catalog()
        base = make_bundle(bundle_id="base_model")
        catalog.publish(base)
        dependent = make_bundle(
            bundle_id="dependent_model",
            dependencies=(
                BundleDependency(
                    dependency_id="dep-base",
                    bundle_id="base_model",
                    version=base.model_version,
                    fingerprint=base.fingerprint,
                ),
            ),
        )
        catalog.publish(dependent)
        outcome = catalog.activate(dependent.bundle_id, dependent.model_version)
        assert outcome.success
        assert catalog.active(dependent.bundle_id) is dependent

    def test_failed_activation_leaves_the_active_pointer_unchanged(self) -> None:
        catalog = make_catalog()
        v1 = make_bundle(model_version="1.0.0")
        catalog.publish(v1)
        assert catalog.activate("sales_model", "1.0.0").success

        broken = make_bundle(
            model_version="2.0.0",
            dependencies=(
                BundleDependency(
                    dependency_id="dep-ghost",
                    bundle_id="ghost_model",
                    version="1.0.0",
                    fingerprint=fp("22"),
                ),
            ),
        )
        catalog.publish(broken)
        outcome = catalog.activate("sales_model", "2.0.0")
        assert outcome.kind == "rejected"
        assert catalog.active("sales_model") is v1


class TestRollback:
    def test_rollback_restores_the_previous_active_version(self) -> None:
        catalog = make_catalog()
        v1 = make_bundle(model_version="1.0.0")
        v2 = make_bundle(model_version="2.0.0")
        catalog.publish(v1)
        catalog.publish(v2)
        assert catalog.activate("sales_model", "1.0.0").success
        assert catalog.activate("sales_model", "2.0.0").success

        outcome = catalog.rollback("sales_model")
        assert outcome.success
        assert outcome.kind == "rolled_back"
        assert outcome.bundle is v1
        assert catalog.active("sales_model") is v1

    def test_rollback_without_active_version_is_not_found(self) -> None:
        catalog = make_catalog()
        catalog.publish(make_bundle())
        outcome = catalog.rollback("sales_model")
        assert outcome.kind == "not_found"
        assert "bundle_not_active" in outcome.issue_codes()

    def test_rollback_without_history_is_no_history(self) -> None:
        catalog = make_catalog()
        bundle = make_bundle()
        catalog.publish(bundle)
        assert catalog.activate("sales_model", "1.0.0").success
        outcome = catalog.rollback("sales_model")
        assert outcome.kind == "no_history"
        assert "no_rollback_history" in outcome.issue_codes()
        assert catalog.active("sales_model") is bundle

    def test_rollback_never_mutates_published_artifacts(self) -> None:
        catalog = make_catalog()
        v1 = make_bundle(model_version="1.0.0")
        v2 = make_bundle(model_version="2.0.0")
        catalog.publish(v1)
        catalog.publish(v2)
        catalog.activate("sales_model", "1.0.0")
        catalog.activate("sales_model", "2.0.0")
        catalog.rollback("sales_model")

        #: Both versions remain published with their original fingerprints.
        versions = catalog.versions("sales_model")
        assert versions == (v1, v2)
        assert versions[0].fingerprint == v1.fingerprint
        assert versions[1].fingerprint == v2.fingerprint
        assert catalog.get("sales_model", "2.0.0") is v2


class TestOutcomes:
    def test_success_outcomes_carry_the_bundle_only(self) -> None:
        catalog = make_catalog()
        bundle = make_bundle()
        outcome = catalog.publish(bundle)
        assert outcome.bundle is bundle
        assert outcome.issues == ()
        payload = outcome.safe_payload()
        assert payload["bundle"] == {
            "bundle_id": bundle.bundle_id,
            "fingerprint": bundle.fingerprint,
        }

    def test_failure_outcomes_carry_issues_only(self) -> None:
        catalog = make_catalog()
        draft = make_bundle(
            provenance=BundleProvenance(
                owner_reference="team-analytics", quality=BundleQualityStatus.DRAFT
            )
        )
        outcome = catalog.publish(draft)
        assert outcome.bundle is None
        assert outcome.issues
        assert "quality_not_met" in outcome.issue_codes()
        assert outcome.safe_payload()["bundle"] is None
