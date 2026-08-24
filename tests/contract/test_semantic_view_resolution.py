"""Contract tests for Semantic View resolution (DDS-019).

Covers the positive resolution contract, include/exclude member rules,
operation/aggregation/relationship/result-shape restrictions, capability and
model/catalog version checks, missing-member fail-closed behavior, and the
IR-to-resolved-view binding enforced by ``validate_ir``.
"""

from __future__ import annotations

import pytest

from nl2data_core.ai.models import StructuredIntent
from nl2data_core.ai.plan_builder import build_ir_from_intent
from nl2data_core.planning.ir.models import IRResultShape, IRViewReference
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
            field_aliases={"amount": "total_amount"},
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


def resolved(registry: ViewRegistry, context: ResolutionContext | None = None):
    outcome = registry.resolve("analytics_sales", context or make_context())
    assert outcome.resolved, outcome.safe_payload()
    assert outcome.projection is not None
    return outcome.projection


class TestPositiveResolution:
    def test_view_resolves_to_an_authorized_projection(self) -> None:
        projection = resolved(make_registry())
        assert projection.view_id == "analytics_sales"
        assert projection.view_version == 1
        assert projection.source_id == "sales"
        assert projection.fingerprint.startswith("sha256:")
        assert projection.purpose == "analytics"

    def test_projection_contains_only_permitted_members(self) -> None:
        projection = resolved(make_registry())
        assert projection.root_entity_ids == frozenset({"order"})
        assert projection.field_ids == frozenset(
            {"order_id", "amount", "region", "status", "created_at"}
        )
        assert "email" not in projection.field_ids
        assert "customer" not in projection.root_entity_ids

    def test_projection_carries_safe_versioned_provenance(self) -> None:
        projection = resolved(make_registry())
        #: The provenance binds the projection to the computed descriptor
        #: fingerprint and resolver version, never raw identity claims.
        assert projection.provenance.descriptor_fingerprint == make_descriptor().fingerprint
        assert projection.provenance.resolver_version == 1
        assert projection.catalog_fingerprint == fp("ab")

    def test_authorized_view_is_derivable_from_projection(self) -> None:
        projection = resolved(make_registry())
        view = AuthorizedView.from_projection(projection)
        assert view.view_bound
        assert view.view_id == "analytics_sales"
        assert view.view_version == 1
        assert view.view_fingerprint == projection.fingerprint
        assert view.field_ids == projection.field_ids
        assert view.contains_field("amount")
        assert not view.contains_field("email")

    def test_registry_exposes_registered_views(self) -> None:
        registry = make_registry()
        assert not registry.is_empty
        assert "analytics_sales" in registry.view_ids()
        assert registry.view("analytics_sales") is not None
        assert registry.descriptor("sales_catalog") is not None
        assert registry.view("unknown") is None


class TestIncludeExcludeRules:
    def test_include_entities_limits_projection(self) -> None:
        view = make_view(
            restrictions=ViewMemberRestrictions(include_entities=frozenset({"customer"}))
        )
        projection = resolved(make_registry(**view.model_dump()))
        assert projection.root_entity_ids == frozenset({"customer"})

    def test_exclude_entities_removes_entities(self) -> None:
        view = make_view(
            restrictions=ViewMemberRestrictions(exclude_entities=frozenset({"order"}))
        )
        projection = resolved(make_registry(**view.model_dump()))
        assert projection.root_entity_ids == frozenset({"customer"})

    def test_include_fields_limits_fields(self) -> None:
        view = make_view(
            restrictions=ViewMemberRestrictions(
                include_entities=frozenset({"order"}),
                include_fields=frozenset({"order_id", "amount"}),
            )
        )
        projection = resolved(make_registry(**view.model_dump()))
        assert projection.field_ids == frozenset({"order_id", "amount"})

    def test_exclude_fields_removes_fields(self) -> None:
        view = make_view(
            restrictions=ViewMemberRestrictions(
                include_entities=frozenset({"order"}),
                exclude_fields=frozenset({"status", "created_at"}),
            )
        )
        projection = resolved(make_registry(**view.model_dump()))
        assert "status" not in projection.field_ids
        assert "created_at" not in projection.field_ids
        assert "amount" in projection.field_ids

    def test_field_aliases_are_applied(self) -> None:
        projection = resolved(make_registry())
        order = next(entity for entity in projection.entities if entity.entity_id == "order")
        amount = next(field for field in order.fields if field.field_id == "amount")
        assert amount.alias == "total_amount"

    def test_aliased_field_keeps_safe_description(self) -> None:
        projection = resolved(make_registry())
        order = next(entity for entity in projection.entities if entity.entity_id == "order")
        amount = next(field for field in order.fields if field.field_id == "amount")
        assert amount.label == "Amount"
        assert amount.description == "Semantic field amount"


class TestOperationsAndAggregations:
    def test_allowed_operations_are_projected(self) -> None:
        projection = resolved(make_registry())
        assert projection.allowed_operations == frozenset({"select", "aggregate", "order"})
        assert projection.contains_operation("select")
        assert projection.contains_operation("aggregate")
        assert projection.contains_operation("order")

    def test_operations_default_to_select_only(self) -> None:
        view = make_view(
            restrictions=ViewMemberRestrictions(
                include_entities=frozenset({"order"}), allowed_operations=frozenset()
            )
        )
        projection = resolved(make_registry(**view.model_dump()))
        assert projection.allowed_operations == frozenset({"select"})

    def test_registry_rejects_views_without_select(self) -> None:
        view = make_view(
            restrictions=ViewMemberRestrictions(
                allowed_operations=frozenset({"aggregate"})
            )
        )
        try:
            ViewRegistry(descriptors=(make_descriptor(),), views=(view,))
        except ValueError as error:
            assert "must include 'select'" in str(error)
        else:  # pragma: no cover
            raise AssertionError("registry accepted a view without 'select'")

    def test_aggregation_restrictions_narrow_descriptor_aggregations(self) -> None:
        projection = resolved(make_registry())
        order = next(entity for entity in projection.entities if entity.entity_id == "order")
        amount = next(field for field in order.fields if field.field_id == "amount")
        #: The restriction narrows the descriptor's {sum, avg, min, max}.
        assert amount.allowed_aggregations == frozenset({"sum", "avg"})

    def test_aggregation_restrictions_never_grant_beyond_descriptor(self) -> None:
        view = make_view(
            restrictions=ViewMemberRestrictions(
                include_entities=frozenset({"order"}),
                field_aggregation_restrictions={"order_id": frozenset({"sum"})},
            )
        )
        projection = resolved(make_registry(**view.model_dump()))
        order = next(entity for entity in projection.entities if entity.entity_id == "order")
        order_id = next(field for field in order.fields if field.field_id == "order_id")
        #: order_id has no descriptor aggregations, so the restriction grants nothing.
        assert order_id.allowed_aggregations == frozenset()


class TestRelationshipsAndResultShapes:
    def test_allowed_relationships_are_projected(self) -> None:
        projection = resolved(make_registry())
        assert projection.allowed_relationships == frozenset({"customer_orders"})
        assert projection.contains_relationship("customer_orders")
        order = next(entity for entity in projection.entities if entity.entity_id == "order")
        assert order.relationships == frozenset({"customer_orders"})

    def test_disallowed_relationships_are_excluded(self) -> None:
        view = make_view(
            restrictions=ViewMemberRestrictions(
                include_entities=frozenset({"order"}),
                allowed_relationships=frozenset(),
            )
        )
        projection = resolved(make_registry(**view.model_dump()))
        assert projection.allowed_relationships == frozenset()
        order = next(entity for entity in projection.entities if entity.entity_id == "order")
        assert order.relationships == frozenset()

    def test_result_shape_constraints_are_projected(self) -> None:
        projection = resolved(make_registry())
        assert projection.result_shape_constraints == ("rows", "grouped_rows")

    def test_result_shape_constraints_default_to_unconstrained(self) -> None:
        view = make_view(
            restrictions=ViewMemberRestrictions(include_entities=frozenset({"order"}))
        )
        projection = resolved(make_registry(**view.model_dump()))
        assert projection.result_shape_constraints == ()


class TestCapabilityAndVersionChecks:
    def test_missing_capability_fails_closed(self) -> None:
        registry = make_registry()
        outcome = registry.resolve(
            "analytics_sales",
            make_context(adapter_capabilities=frozenset({"sql"})),
        )
        assert outcome.kind == "unavailable"
        assert "capability_unsupported" in outcome.issue_codes()
        assert outcome.projection is None

    def test_missing_feature_flag_fails_closed(self) -> None:
        registry = make_registry()
        outcome = registry.resolve(
            "analytics_sales",
            make_context(feature_flags=frozenset()),
        )
        assert outcome.kind == "unavailable"
        assert "feature_flag_disabled" in outcome.issue_codes()

    def test_model_version_mismatch_is_unavailable(self) -> None:
        registry = make_registry()
        outcome = registry.resolve("analytics_sales", make_context(model_version="model-2"))
        assert outcome.kind == "unavailable"
        assert "model_stale" in outcome.issue_codes()

    def test_catalog_version_mismatch_is_unavailable(self) -> None:
        registry = make_registry()
        outcome = registry.resolve(
            "analytics_sales", make_context(catalog_fingerprint=fp("99"))
        )
        assert outcome.kind == "unavailable"
        assert "catalog_stale" in outcome.issue_codes()

    def test_policy_mismatch_is_denied(self) -> None:
        registry = make_registry()
        outcome = registry.resolve("analytics_sales", make_context(policy_fingerprint=fp("99")))
        assert outcome.kind == "denied"
        assert "policy_mismatch" in outcome.issue_codes()

    def test_unknown_view_is_unavailable(self) -> None:
        registry = make_registry()
        outcome = registry.resolve("missing_view", make_context())
        assert outcome.kind == "unavailable"
        assert "view_not_found" in outcome.issue_codes()

    def test_unknown_descriptor_is_rejected_at_registration(self) -> None:
        view = make_view(descriptor_id="unknown_descriptor")
        with pytest.raises(ValueError) as excinfo:
            ViewRegistry(descriptors=(make_descriptor(),), views=(view,))
        assert "unknown descriptor" in str(excinfo.value)


class TestMissingMembersFailClosed:
    def test_unknown_include_entity_is_a_missing_member(self) -> None:
        view = make_view(
            restrictions=ViewMemberRestrictions(include_entities=frozenset({"ghost"}))
        )
        registry = ViewRegistry(descriptors=(make_descriptor(),), views=(view,))
        outcome = registry.resolve("analytics_sales", make_context())
        assert outcome.kind == "unavailable"
        assert "missing_member" in outcome.issue_codes()
        assert outcome.issues[0].member_id == "ghost"

    def test_unknown_exclude_field_is_a_missing_member(self) -> None:
        view = make_view(
            restrictions=ViewMemberRestrictions(exclude_fields=frozenset({"ghost"}))
        )
        registry = ViewRegistry(descriptors=(make_descriptor(),), views=(view,))
        outcome = registry.resolve("analytics_sales", make_context())
        assert outcome.kind == "unavailable"
        assert outcome.issues[0].member_id == "ghost"

    def test_unknown_relationship_is_a_missing_member(self) -> None:
        view = make_view(
            restrictions=ViewMemberRestrictions(
                allowed_relationships=frozenset({"ghost_link"})
            )
        )
        registry = ViewRegistry(descriptors=(make_descriptor(),), views=(view,))
        outcome = registry.resolve("analytics_sales", make_context())
        assert outcome.kind == "unavailable"
        assert outcome.issues[0].member_id == "ghost_link"

    def test_no_entities_remaining_is_bounded_invalid(self) -> None:
        view = make_view(
            restrictions=ViewMemberRestrictions(
                include_entities=frozenset({"order"}), exclude_entities=frozenset({"order"})
            )
        )
        registry = ViewRegistry(descriptors=(make_descriptor(),), views=(view,))
        outcome = registry.resolve("analytics_sales", make_context())
        assert outcome.kind == "unavailable"
        assert "view_bounds_exceeded" in outcome.issue_codes()

    def test_no_fields_remaining_is_bounded_invalid(self) -> None:
        view = make_view(
            restrictions=ViewMemberRestrictions(
                include_entities=frozenset({"order"}),
                include_fields=frozenset(),
                exclude_fields=frozenset({"order_id", "amount", "region", "status", "created_at"}),
            )
        )
        registry = ViewRegistry(descriptors=(make_descriptor(),), views=(view,))
        outcome = registry.resolve("analytics_sales", make_context())
        assert outcome.kind == "unavailable"
        assert "view_bounds_exceeded" in outcome.issue_codes()


class TestIRViewBinding:
    """``validate_ir`` enforces the resolved-view binding before compilation."""

    def _bound_ir(self, projection) -> object:
        intent = StructuredIntent.model_validate(
            {
                "intent_id": "intent-view-1",
                "request_id": "req-view-1",
                "source_id": "sales",
                "root_entity_id": "order",
                "selections": [
                    {"selection_id": "s1", "field_id": "order_id"},
                    {"selection_id": "s2", "field_id": "amount"},
                ],
                "filters": [
                    {"filter_id": "f1", "field_id": "region", "operator": "eq", "value": "emea"}
                ],
                "orderings": [{"ordering_id": "o1", "field_id": "order_id", "direction": "desc"}],
                "limit": 10,
                "confidence": 0.95,
            }
        )
        reference = IRViewReference(
            view_id=projection.view_id,
            view_version=projection.view_version,
            view_fingerprint=projection.fingerprint,
        )
        return build_ir_from_intent(
            intent,
            catalog_fingerprint=projection.catalog_fingerprint,
            view_reference=reference,
        )

    def test_bound_ir_validates_against_the_current_projection(self) -> None:
        projection = resolved(make_registry())
        view = AuthorizedView.from_projection(projection)
        result = validate_ir(self._bound_ir(projection), view=view)
        assert result.valid, result.issues

    def test_missing_view_reference_is_rejected(self) -> None:
        projection = resolved(make_registry())
        view = AuthorizedView.from_projection(projection)
        intent = StructuredIntent.model_validate(
            {
                "intent_id": "intent-view-2",
                "request_id": "req-view-2",
                "source_id": "sales",
                "root_entity_id": "order",
                "selections": [{"selection_id": "s1", "field_id": "order_id"}],
                "limit": 10,
                "confidence": 0.95,
            }
        )
        unbound = build_ir_from_intent(intent, catalog_fingerprint=projection.catalog_fingerprint)
        result = validate_ir(unbound, view=view)
        assert not result.valid
        assert "missing_view_reference" in result.issue_codes()

    def test_stale_view_fingerprint_is_rejected(self) -> None:
        projection = resolved(make_registry())
        view = AuthorizedView.from_projection(projection)
        stale = self._bound_ir(projection).model_copy(
            update={
                "provenance": self._bound_ir(projection).provenance.model_copy(
                    update={
                        "view_reference": IRViewReference(
                            view_id=projection.view_id,
                            view_version=projection.view_version,
                            view_fingerprint=fp("00"),
                        )
                    }
                )
            }
        )
        result = validate_ir(stale, view=view)
        assert not result.valid
        assert "view_reference_mismatch" in result.issue_codes()

    def test_field_outside_projection_is_rejected(self) -> None:
        projection = resolved(make_registry())
        view = AuthorizedView.from_projection(projection)
        ir = self._bound_ir(projection)
        #: ``email`` exists in the descriptor but is excluded from the view.
        smuggled = ir.model_copy(
            update={"selections": (ir.selections[0], ir.selections[0].model_copy(
                update={"selection_id": "s9", "field_id": "email"}
            ))}
        )
        result = validate_ir(smuggled, view=view)
        assert not result.valid
        assert "field_out_of_scope" in result.issue_codes()

    def test_operation_outside_projection_is_rejected(self) -> None:
        projection = resolved(make_registry())
        view = AuthorizedView.from_projection(projection)
        ir = self._bound_ir(projection)
        #: ``grouping`` maps to the ``group`` operation, outside the view.
        result = validate_ir(
            ir.model_copy(update={"required_capabilities": frozenset({"grouping"})}),
            view=view,
        )
        assert not result.valid
        assert "operation_out_of_scope" in result.issue_codes()

    def test_aggregation_outside_projection_is_rejected(self) -> None:
        projection = resolved(make_registry())
        view = AuthorizedView.from_projection(projection)
        ir = self._bound_ir(projection)
        #: The view restricts amount to {sum, avg}; max is outside.
        restricted = ir.model_copy(
            update={
                "selections": (
                    ir.selections[0],
                    ir.selections[1].model_copy(update={"aggregation": "max"}),
                )
            }
        )
        result = validate_ir(restricted, view=view)
        assert not result.valid
        assert "aggregation_out_of_scope" in result.issue_codes()

    def test_result_shape_outside_projection_is_rejected(self) -> None:
        projection = resolved(make_registry())
        view = AuthorizedView.from_projection(projection)
        ir = self._bound_ir(projection)
        result = validate_ir(
            ir.model_copy(
                update={"result_shape": IRResultShape(kind="scalar")}
            ),
            view=view,
        )
        assert not result.valid
        assert "result_shape_out_of_scope" in result.issue_codes()
