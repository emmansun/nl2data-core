"""Security tests for Semantic Model Bundles.

Inference is metadata - never authority: trust markers, bundle contents,
and client hints never grant View visibility or execution authority, which
only trusted View/governance resolution can grant.  Credential and physical
content is rejected at construction and never reaches catalog outcomes,
resolved projections, or provider context, and bundle identity participates
in evidence fingerprints with fail-closed scope checks.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from nl2data import QueryContext, QueryRequest
from nl2data_core.ai.context import assemble_model_context
from nl2data_core.bundles import (
    BundleProvenance,
    BundleQualityStatus,
    InMemorySemanticBundleCatalog,
    SemanticModelBundle,
    SemanticSourceReference,
    SemanticTrustKind,
    SemanticTrustMarker,
)
from nl2data_core.canonical import canonical_json
from nl2data_core.planning.validation import AuthorizedView
from nl2data_core.views import (
    ResolutionContext,
    SemanticDescriptor,
    SemanticEntityDescriptor,
    SemanticFieldDescriptor,
    SemanticRelationshipDescriptor,
    SemanticViewDefinition,
    ViewMemberRestrictions,
    ViewProvenance,
    ViewRegistry,
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


def make_view(**overrides) -> SemanticViewDefinition:
    values = {
        "view_id": "analytics_sales",
        "version": 1,
        "descriptor_id": "sales_catalog",
        "description": "Analytics surface over the sales catalog",
        "allowed_purposes": frozenset({"analytics"}),
        "restrictions": ViewMemberRestrictions(
            include_entities=frozenset({"order"}),
            exclude_fields=frozenset({"email"}),
            allowed_operations=frozenset({"select", "aggregate", "order"}),
            allowed_relationships=frozenset({"customer_orders"}),
        ),
        "bound_policy_fingerprint": fp("cd"),
        "bound_tenant_scope_fingerprint": fp("ee"),
        "bound_principal_authorization_fingerprints": frozenset({fp("ff")}),
        "required_capabilities": frozenset({"sql", "aggregation"}),
        "required_feature_flags": frozenset({"semantic_v2"}),
        "model_version": "model-1",
        "provenance": ViewProvenance(
            descriptor_fingerprint=fp("ab"),
            policy_decision_fingerprint=fp("cd"),
            resolver_version=1,
        ),
    }
    values.update(overrides)
    return SemanticViewDefinition(**values)


def make_context(**overrides) -> ResolutionContext:
    values = {
        "tenant_scope_fingerprint": fp("ee"),
        "tenant_active": True,
        "principal_authorization_fingerprint": fp("ff"),
        "purpose": "analytics",
        "policy_fingerprint": fp("cd"),
        "catalog_fingerprint": fp("ab"),
        "model_version": "model-1",
        "adapter_capabilities": frozenset({"sql", "aggregation"}),
        "feature_flags": frozenset({"semantic_v2"}),
    }
    values.update(overrides)
    return ResolutionContext(**values)


def make_bundle(**overrides) -> SemanticModelBundle:
    values = {
        "bundle_id": "sales_model",
        "model_version": "1.0.0",
        "descriptor": make_descriptor(),
        "sources": (
            SemanticSourceReference(
                reference_id="src-sales", source_id="sales", catalog_fingerprint=fp("ab")
            ),
        ),
        "trust_markers": (
            SemanticTrustMarker(
                marker_id="m-amount",
                fact_id="amount",
                kind=SemanticTrustKind.INFERRED,
                note="Discovered by automated analysis; not authoritative",
            ),
        ),
        "provenance": BundleProvenance(
            owner_reference="team-analytics",
            quality=BundleQualityStatus.VALIDATED,
        ),
    }
    values.update(overrides)
    return SemanticModelBundle(**values)


def make_bundle_registry(bundle: SemanticModelBundle) -> ViewRegistry:
    return ViewRegistry(
        descriptors=(bundle.descriptor,),
        views=(make_view(),),
        bundle=bundle,
    )


def resolve_bundle(bundle: SemanticModelBundle, context: ResolutionContext | None = None):
    registry = make_bundle_registry(bundle)
    return registry.resolve(
        "analytics_sales",
        context or make_context(bundle_fingerprint=bundle.fingerprint),
    )


class TestInferenceIsNotAuthority:
    def test_inferred_markers_never_grant_resolution(self) -> None:
        bundle = make_bundle(
            trust_markers=(
                SemanticTrustMarker(
                    marker_id="m-amount",
                    fact_id="amount",
                    kind=SemanticTrustKind.INFERRED,
                ),
                SemanticTrustMarker(
                    marker_id="m-orders",
                    fact_id="customer_orders",
                    kind=SemanticTrustKind.INFERRED,
                ),
            )
        )
        outcome = resolve_bundle(
            bundle, make_context(bundle_fingerprint=bundle.fingerprint)
        )
        assert outcome.resolved

    def test_inferred_metadata_does_not_bypass_principal_denial(self) -> None:
        bundle = make_bundle(
            trust_markers=(
                SemanticTrustMarker(
                    marker_id="m-amount",
                    fact_id="amount",
                    kind=SemanticTrustKind.INFERRED,
                    approved=True,
                ),
            )
        )
        context = make_context(
            principal_authorization_fingerprint=fp("11"),
            bundle_fingerprint=bundle.fingerprint,
            client_hints={"principal_id": "alice", "role": "admin"},
        )
        outcome = resolve_bundle(bundle, context)
        assert outcome.denied
        assert "principal_denied" in outcome.issue_codes()
        assert outcome.projection is None

    def test_trust_markers_never_enter_the_projection(self) -> None:
        bundle = make_bundle(
            trust_markers=(
                SemanticTrustMarker(
                    marker_id="m-amount",
                    fact_id="amount",
                    kind=SemanticTrustKind.INFERRED,
                    note="Discovered by automated analysis",
                ),
            )
        )
        outcome = resolve_bundle(bundle)
        assert outcome.resolved
        payload = canonical_json(outcome.projection.safe_payload())
        for forbidden in ("trust_markers", "inferred", "note", "marker"):
            assert forbidden not in payload

    def test_bundle_contents_do_not_change_the_authorized_surface(self) -> None:
        sparse = make_bundle(trust_markers=())
        dense = make_bundle(
            trust_markers=(
                SemanticTrustMarker(
                    marker_id="m-amount",
                    fact_id="amount",
                    kind=SemanticTrustKind.APPROVED,
                    approved=True,
                ),
            )
        )
        outcome_sparse = resolve_bundle(sparse)
        outcome_dense = resolve_bundle(dense)
        assert outcome_sparse.resolved and outcome_dense.resolved
        assert outcome_sparse.projection.field_ids == outcome_dense.projection.field_ids
        assert outcome_sparse.projection.root_entity_ids == outcome_dense.projection.root_entity_ids


class TestCredentialAndPhysicalExclusion:
    def test_credential_content_is_rejected_at_construction(self) -> None:
        with pytest.raises(ValidationError):
            make_bundle(
                sources=(
                    SemanticSourceReference(
                        reference_id="src-bad",
                        source_id="sales",
                        description="connects with password=hunter2",
                    ),
                )
            )

    def test_connection_material_is_rejected_at_construction(self) -> None:
        with pytest.raises(ValidationError):
            make_bundle(
                provenance=BundleProvenance(
                    owner_reference="owner mongodb://host:27017",
                    quality=BundleQualityStatus.VALIDATED,
                )
            )

    def test_unknown_physical_fields_are_rejected(self) -> None:
        payload = make_bundle().model_dump(mode="json")
        payload["connection_string"] = "postgres://user:pass@host/db"
        with pytest.raises(ValidationError):
            SemanticModelBundle.model_validate(payload)

    def test_catalog_outcomes_expose_only_id_and_fingerprint(self) -> None:
        catalog = InMemorySemanticBundleCatalog()
        bundle = make_bundle()
        outcome = catalog.publish(bundle)
        assert outcome.success
        payload = json.dumps(outcome.safe_payload())
        for forbidden in (
            "owner",
            "description",
            "credential",
            "password",
            "physical",
            "connection",
            "driver",
            "table_name",
        ):
            assert forbidden not in payload
        assert json.loads(payload)["bundle"]["bundle_id"] == "sales_model"

    def test_bundle_safe_payload_has_no_physical_or_credential_material(self) -> None:
        bundle = make_bundle()
        payload = json.loads(canonical_json(bundle.safe_payload()))
        for key in payload:
            assert "credential" not in key
            assert "password" not in key
            assert "physical" not in key
            assert "connection" not in key
            assert "driver" not in key
            assert "table_name" not in key
            assert "claim" not in key

    def test_bundle_backed_projection_never_leaks_bundle_internals(self) -> None:
        outcome = resolve_bundle(make_bundle())
        assert outcome.resolved
        payload = canonical_json(outcome.projection.safe_payload())
        for forbidden in (
            "measures",
            "grains",
            "sources",
            "dependencies",
            "trust_markers",
            "owner_reference",
            "notes",
            "physical",
            "credential",
            "password",
        ):
            assert forbidden not in payload

    def test_provider_context_never_exposes_bundle_or_physical_metadata(self) -> None:
        bundle = make_bundle()
        outcome = resolve_bundle(bundle)
        assert outcome.resolved
        view = AuthorizedView.from_projection(outcome.projection)
        request = QueryRequest(
            request_id="req-bundle-sec",
            prompt="order amounts",
            context=QueryContext(request_id="req-bundle-sec"),
        )
        context = assemble_model_context(
            request=request,
            view=view,
            projection=outcome.projection,
        )
        payload = canonical_json(context.safe_payload())
        for forbidden in (
            "measures",
            "grains",
            "trust_markers",
            "owner_reference",
            "physical",
            "connection",
            "password",
            "secret",
            "credential",
        ):
            assert forbidden not in payload


class TestBundleScopeFailsClosed:
    def test_missing_bundle_fingerprint_is_unavailable(self) -> None:
        bundle = make_bundle()
        outcome = resolve_bundle(bundle, make_context(bundle_fingerprint=None))
        assert outcome.kind == "unavailable"
        assert "bundle_scope_missing" in outcome.issue_codes()
        assert outcome.projection is None

    def test_stale_bundle_fingerprint_is_unavailable(self) -> None:
        bundle = make_bundle()
        outcome = resolve_bundle(bundle, make_context(bundle_fingerprint=fp("00")))
        assert outcome.kind == "unavailable"
        assert "bundle_stale" in outcome.issue_codes()
        assert outcome.projection is None

    def test_bundle_denial_reveals_no_bundle_internals(self) -> None:
        bundle = make_bundle()
        outcome = resolve_bundle(bundle, make_context(bundle_fingerprint=fp("00")))
        payload = canonical_json(outcome.safe_payload())
        for forbidden in ("measures", "grains", "sources", "owner_reference", "trust_markers"):
            assert forbidden not in payload
