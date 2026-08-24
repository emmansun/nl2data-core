"""Security tests for Semantic View resolution (DDS-019).

Resolution fails closed across tenants, principals, and purposes; client
hints never establish access; denials and projections never leak hidden
metadata, credentials, physical bindings, or excluded members; and an IR
referencing an excluded member is rejected before any compiler or adapter
work.  Any change to a trusted security input invalidates the fingerprint.
"""

from __future__ import annotations

import json

from nl2data import QueryContext, QueryRequest
from nl2data_core.ai.context import assemble_model_context
from nl2data_core.ai.models import StructuredIntent
from nl2data_core.ai.plan_builder import build_ir_from_intent
from nl2data_core.canonical import canonical_json
from nl2data_core.planning.ir.models import IRViewReference
from nl2data_core.planning.ir.validation import validate_ir
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
            field_aggregation_restrictions={"amount": frozenset({"sum", "avg"})},
            allowed_relationships=frozenset({"customer_orders"}),
            result_shape_constraints=("rows", "grouped_rows"),
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


def make_registry(**overrides) -> ViewRegistry:
    return ViewRegistry(
        descriptors=(make_descriptor(),),
        views=(make_view(**overrides),),
    )


def resolve(registry: ViewRegistry, context: ResolutionContext):
    return registry.resolve("analytics_sales", context)


class TestCrossTenantFailClosed:
    def test_missing_tenant_scope_is_denied(self) -> None:
        outcome = resolve(make_registry(), make_context(tenant_scope_fingerprint=None))
        assert outcome.denied
        assert "tenant_scope_missing" in outcome.issue_codes()
        assert outcome.projection is None

    def test_inactive_tenant_scope_is_denied(self) -> None:
        outcome = resolve(make_registry(), make_context(tenant_active=False))
        assert outcome.denied
        assert "tenant_scope_inactive" in outcome.issue_codes()
        assert outcome.projection is None

    def test_mismatched_tenant_scope_is_denied(self) -> None:
        outcome = resolve(make_registry(), make_context(tenant_scope_fingerprint=fp("99")))
        assert outcome.denied
        assert "tenant_scope_mismatch" in outcome.issue_codes()
        assert outcome.projection is None

    def test_tenant_denial_reveals_no_semantic_members(self) -> None:
        outcome = resolve(make_registry(), make_context(tenant_scope_fingerprint=fp("99")))
        payload = canonical_json(outcome.safe_payload())
        for member in ("order_id", "amount", "email", "customer"):
            assert member not in payload
        assert outcome.issues[0].member_id is None


class TestPrincipalAndPurposeDenial:
    def test_missing_principal_is_denied(self) -> None:
        outcome = resolve(
            make_registry(), make_context(principal_authorization_fingerprint=None)
        )
        assert outcome.denied
        assert "principal_denied" in outcome.issue_codes()

    def test_unauthorized_principal_is_denied(self) -> None:
        outcome = resolve(
            make_registry(), make_context(principal_authorization_fingerprint=fp("11"))
        )
        assert outcome.denied
        assert "principal_denied" in outcome.issue_codes()
        assert outcome.projection is None

    def test_unauthorized_purpose_is_denied(self) -> None:
        outcome = resolve(make_registry(), make_context(purpose="forensics"))
        assert outcome.denied
        assert "purpose_denied" in outcome.issue_codes()
        assert outcome.projection is None

    def test_purpose_denial_reveals_no_excluded_members(self) -> None:
        outcome = resolve(make_registry(), make_context(purpose="forensics"))
        assert "forensics" not in canonical_json(outcome.safe_payload())
        assert outcome.issues[0].member_id is None


class TestClientHintsAreNotAuthoritative:
    def test_hints_cannot_establish_tenant_access(self) -> None:
        #: The hint claims a different tenant, but only the trusted
        #: fingerprint matters; the view still resolves for the trusted
        #: tenant and never for the hinted one.
        outcome = resolve(
            make_registry(),
            make_context(client_hints={"tenant_id": "other-tenant", "tenant": "beta"}),
        )
        assert outcome.resolved
        assert outcome.projection is not None
        assert outcome.projection.tenant_scope_fingerprint == fp("ee")

    def test_hints_cannot_bypass_principal_denial(self) -> None:
        context = make_context(
            principal_authorization_fingerprint=fp("11"),
            client_hints={"principal_id": "alice", "role": "admin"},
        )
        outcome = resolve(make_registry(), context)
        assert outcome.denied
        assert "principal_denied" in outcome.issue_codes()

    def test_hints_cannot_bypass_purpose_denial(self) -> None:
        context = make_context(
            purpose="forensics", client_hints={"purpose": "analytics"}
        )
        outcome = resolve(make_registry(), context)
        assert outcome.denied
        assert "purpose_denied" in outcome.issue_codes()

    def test_hints_never_enter_the_projection(self) -> None:
        outcome = resolve(
            make_registry(),
            make_context(client_hints={"tenant_id": "acme", "prompt": "show me everything"}),
        )
        assert outcome.resolved
        assert "acme" not in canonical_json(outcome.projection.safe_payload())
        assert "show me everything" not in canonical_json(outcome.projection.safe_payload())


class TestNoHiddenMetadataLeakage:
    def test_projection_serialization_has_no_physical_or_credential_fields(self) -> None:
        outcome = resolve(make_registry(), make_context())
        assert outcome.resolved
        payload = canonical_json(outcome.projection.safe_payload())
        for forbidden in (
            "physical",
            "connection",
            "credential",
            "password",
            "secret",
            "driver",
            "table_name",
            "column_name",
        ):
            assert forbidden not in payload

    def test_projection_never_includes_raw_identity_claims(self) -> None:
        outcome = resolve(make_registry(), make_context())
        assert outcome.resolved
        payload = json.loads(canonical_json(outcome.projection.safe_payload()))
        for key in payload:
            assert "claim" not in key
            assert "token" not in key
        #: Only fingerprints represent tenant/principal identity.
        assert payload["tenant_scope_fingerprint"] == fp("ee")
        assert payload["principal_authorization_fingerprint"] == fp("ff")

    def test_resolution_issues_never_reveal_policy_internals(self) -> None:
        context = make_context(policy_fingerprint=fp("99"))
        outcome = resolve(make_registry(), context)
        assert outcome.denied
        serialized = canonical_json(outcome.safe_payload())
        #: The safe reason mentions the decision, never its internals.
        for forbidden in ("deny", "rule", "filter", "obligation", "condition", "entitlement"):
            assert forbidden not in serialized

    def test_unavailable_capability_reports_only_the_capability_id(self) -> None:
        outcome = resolve(
            make_registry(), make_context(adapter_capabilities=frozenset({"sql"}))
        )
        assert outcome.kind == "unavailable"
        payload = outcome.safe_payload()
        assert payload["issues"][0]["member_id"] == "aggregation"
        assert "aggregation" not in payload["issues"][0]["message"]
        #: The denial never carries a projection or partial members.
        assert payload["projection"] is None


class TestExcludedMembersCannotEnterContext:
    def test_provider_context_contains_only_projection_members(self) -> None:
        outcome = resolve(make_registry(), make_context())
        assert outcome.resolved
        view = AuthorizedView.from_projection(outcome.projection)
        request = QueryRequest(
            request_id="req-view-sec",
            prompt="order amounts",
            context=QueryContext(request_id="req-view-sec"),
        )
        context = assemble_model_context(
            request=request,
            view=view,
            projection=outcome.projection,
        )
        field_ids = {reference.field_id for reference in context.semantic_references}
        assert "email" not in field_ids
        assert "amount" in field_ids
        assert "order_id" in field_ids
        assert "customer" not in context.root_entity_ids
        assert context.source_id == "sales"
        payload = canonical_json(context.safe_payload())
        assert "email" not in payload

    def test_provider_context_never_exposes_physical_metadata(self) -> None:
        outcome = resolve(make_registry(), make_context())
        assert outcome.resolved
        view = AuthorizedView.from_projection(outcome.projection)
        request = QueryRequest(
            request_id="req-view-sec-2",
            prompt="order amounts",
            context=QueryContext(request_id="req-view-sec-2"),
        )
        context = assemble_model_context(
            request=request,
            view=view,
            projection=outcome.projection,
        )
        payload = canonical_json(context.safe_payload())
        for forbidden in ("physical", "connection", "password", "secret", "credential"):
            assert forbidden not in payload

    def test_ir_reference_to_excluded_field_fails_validation(self) -> None:
        outcome = resolve(make_registry(), make_context())
        assert outcome.resolved
        view = AuthorizedView.from_projection(outcome.projection)
        intent = StructuredIntent.model_validate(
            {
                "intent_id": "intent-excluded",
                "request_id": "req-excluded",
                "source_id": "sales",
                "root_entity_id": "order",
                "selections": [{"selection_id": "s1", "field_id": "email"}],
                "limit": 10,
                "confidence": 0.95,
            }
        )
        reference = IRViewReference(
            view_id=outcome.projection.view_id,
            view_version=outcome.projection.view_version,
            view_fingerprint=outcome.projection.fingerprint,
        )
        ir = build_ir_from_intent(
            intent,
            catalog_fingerprint=outcome.projection.catalog_fingerprint,
            view_reference=reference,
        )
        result = validate_ir(ir, view=view)
        assert not result.valid
        assert "field_out_of_scope" in result.issue_codes()

    def test_ir_reference_to_hidden_descriptor_field_fails_validation(self) -> None:
        #: ``customer``/``email`` exist in the descriptor but are excluded;
        #: absence from the projection is what matters, never descriptor presence.
        outcome = resolve(make_registry(), make_context())
        assert outcome.resolved
        view = AuthorizedView.from_projection(outcome.projection)
        intent = StructuredIntent.model_validate(
            {
                "intent_id": "intent-hidden",
                "request_id": "req-hidden",
                "source_id": "sales",
                "root_entity_id": "customer",
                "selections": [{"selection_id": "s1", "field_id": "customer_id"}],
                "limit": 10,
                "confidence": 0.95,
            }
        )
        reference = IRViewReference(
            view_id=outcome.projection.view_id,
            view_version=outcome.projection.view_version,
            view_fingerprint=outcome.projection.fingerprint,
        )
        ir = build_ir_from_intent(
            intent,
            catalog_fingerprint=outcome.projection.catalog_fingerprint,
            view_reference=reference,
        )
        result = validate_ir(ir, view=view)
        assert not result.valid
        assert "entity_out_of_scope" in result.issue_codes()


class TestSecurityInputChangesInvalidateFingerprint:
    """Any trusted security input change must change the resolved fingerprint.

    The default fixture view is bound to one tenant/principal/policy, so a
    changed trusted input fails closed (denied) instead of resolving.  To
    prove the fingerprint itself covers every dimension, these tests use an
    unbound registry that resolves under any trusted context.
    """

    def _unbound_registry(self) -> ViewRegistry:
        return ViewRegistry(
            descriptors=(make_descriptor(catalog_fingerprint=None),),
            views=(
                make_view(
                    bound_tenant_scope_fingerprint=None,
                    bound_principal_authorization_fingerprints=frozenset(),
                    bound_policy_fingerprint=None,
                ),
            ),
        )

    def _fingerprint(self, **context_overrides) -> str:
        outcome = resolve(self._unbound_registry(), make_context(**context_overrides))
        assert outcome.resolved, outcome.issue_codes()
        assert outcome.projection is not None
        return outcome.projection.fingerprint

    def test_tenant_change_invalidates(self) -> None:
        assert self._fingerprint() != self._fingerprint(
            tenant_scope_fingerprint=fp("77")
        )

    def test_principal_change_invalidates(self) -> None:
        assert self._fingerprint() != self._fingerprint(
            principal_authorization_fingerprint=fp("77")
        )

    def test_purpose_change_invalidates(self) -> None:
        #: The view only allows one purpose, so use a second view bound to
        #: both purposes to prove purpose participates in the identity.
        view = make_view(allowed_purposes=frozenset({"analytics", "ops"}))
        registry = ViewRegistry(descriptors=(make_descriptor(),), views=(view,))
        outcome_a = registry.resolve("analytics_sales", make_context(purpose="analytics"))
        outcome_b = registry.resolve("analytics_sales", make_context(purpose="ops"))
        assert outcome_a.resolved and outcome_b.resolved
        assert outcome_a.projection.fingerprint != outcome_b.projection.fingerprint

    def test_policy_change_invalidates(self) -> None:
        assert self._fingerprint() != self._fingerprint(policy_fingerprint=fp("77"))

    def test_catalog_change_invalidates(self) -> None:
        registry_a = ViewRegistry(
            descriptors=(make_descriptor(catalog_fingerprint=fp("ab")),),
            views=(make_view(),),
        )
        registry_b = ViewRegistry(
            descriptors=(make_descriptor(catalog_fingerprint=fp("77")),),
            views=(make_view(),),
        )
        projection_a = resolve(registry_a, make_context())
        projection_b = resolve(registry_b, make_context(catalog_fingerprint=fp("77")))
        assert projection_a.resolved and projection_b.resolved
        assert projection_a.projection.fingerprint != projection_b.projection.fingerprint

    def test_capability_change_invalidates(self) -> None:
        assert self._fingerprint() != self._fingerprint(
            adapter_capabilities=frozenset({"sql", "aggregation", "export"})
        )

    def test_feature_flag_change_invalidates(self) -> None:
        assert self._fingerprint() != self._fingerprint(
            feature_flags=frozenset({"semantic_v2", "beta"})
        )

    def test_equivalent_resolution_has_stable_fingerprint(self) -> None:
        assert self._fingerprint() == self._fingerprint()
