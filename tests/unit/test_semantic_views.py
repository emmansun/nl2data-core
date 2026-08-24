"""Unit tests for Semantic View model bounds, fingerprints, and serialization.

Covers the immutable bounded contracts (DDS-019): identifier and collection
bounds, frozen model enforcement, deterministic canonical fingerprints across
mapping insertion orders, deep immutability of restriction mappings, and safe
serialization that never exposes credentials, physical bindings, or hidden
policy rules.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from nl2data_core.canonical import canonical_json
from nl2data_core.planning.models import AggregationKind
from nl2data_core.views import (
    ResolutionContext,
    ResolutionIssue,
    ResolutionOutcome,
    ResolvedViewEntity,
    ResolvedViewField,
    ResolvedViewProjection,
    SemanticDescriptor,
    SemanticEntityDescriptor,
    SemanticFieldDescriptor,
    SemanticRelationshipDescriptor,
    SemanticViewDefinition,
    ViewMemberRestrictions,
    ViewProvenance,
    denied,
    unavailable,
)


def fp(byte: str) -> str:
    """A valid ``sha256:<hex>`` fingerprint filled with one repeated byte."""
    return "sha256:" + byte * 32


def make_field(field_id: str = "amount", **overrides) -> SemanticFieldDescriptor:
    values = {
        "field_id": field_id,
        "label": "Order amount",
        "description": "Monetary value of one order",
        "data_type": "decimal",
        "allowed_aggregations": frozenset({"sum", "avg", "min", "max"}),
    }
    values.update(overrides)
    return SemanticFieldDescriptor(**values)


def make_entity(entity_id: str = "order", **overrides) -> SemanticEntityDescriptor:
    values = {
        "entity_id": entity_id,
        "label": "Order",
        "description": "A customer order",
        "fields": (
            make_field("order_id", label="Order id", data_type="string"),
            make_field("amount", data_type="decimal"),
            make_field("region", label="Region", data_type="string"),
            make_field("status", label="Status", data_type="string"),
            make_field("created_at", label="Created at", data_type="datetime"),
        ),
        "relationships": (
            SemanticRelationshipDescriptor(
                relationship_id="customer_orders",
                source_entity_id="customer",
                target_entity_id="order",
                label="Orders of the customer",
            ),
        ),
    }
    values.update(overrides)
    return SemanticEntityDescriptor(**values)


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
                    make_field("customer_id", label="Customer id", data_type="string"),
                    make_field("name", label="Customer name", data_type="string"),
                    make_field("email", label="Email", data_type="string"),
                ),
            ),
            make_entity(),
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
            allowed_operations=frozenset({"select", "aggregate"}),
            field_aggregation_restrictions={"amount": frozenset({"sum", "avg", "min", "max"})},
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


class TestModelBounds:
    def test_identifier_patterns_are_enforced(self) -> None:
        with pytest.raises(ValidationError):
            make_field(field_id="bad id!")
        with pytest.raises(ValidationError):
            make_field(data_type="not a type")
        with pytest.raises(ValidationError):
            make_entity(entity_id="")  # must start with an alphanumeric

    def test_text_bounds_are_enforced(self) -> None:
        with pytest.raises(ValidationError):
            make_field(label="")  # label must not be empty
        with pytest.raises(ValidationError):
            make_field(description="x" * 1025)  # description bound

    def test_descriptions_reject_connection_and_query_material(self) -> None:
        with pytest.raises(ValidationError):
            make_field(description="mongodb://user:secret@host")
        with pytest.raises(ValidationError):
            make_view(description="SELECT password FROM users")

    def test_collection_bounds_are_enforced(self) -> None:
        with pytest.raises(ValidationError):
            make_view(allowed_purposes=frozenset({f"p{i}" for i in range(65)}))
        with pytest.raises(ValidationError):
            make_view(
                restrictions=ViewMemberRestrictions(
                    result_shape_constraints=tuple(f"shape-{i}" for i in range(17))
                )
            )

    def test_duplicate_entity_ids_are_rejected(self) -> None:
        entity = SemanticEntityDescriptor(
            entity_id="order", label="Order", fields=(make_field("amount"),)
        )
        with pytest.raises(ValidationError):
            SemanticDescriptor(
                descriptor_id="dup",
                version=1,
                source_id="sales",
                entities=(entity, entity),
            )

    def test_field_and_relationship_ids_are_unique_across_entities(self) -> None:
        shared_field = make_field("shared", data_type="string")
        with pytest.raises(ValidationError):
            SemanticDescriptor(
                descriptor_id="duplicate-fields",
                version=1,
                source_id="sales",
                entities=(
                    SemanticEntityDescriptor(entity_id="a", label="A", fields=(shared_field,)),
                    SemanticEntityDescriptor(entity_id="b", label="B", fields=(shared_field,)),
                ),
            )

    def test_relationship_endpoints_must_exist_in_the_descriptor(self) -> None:
        with pytest.raises(ValidationError):
            SemanticDescriptor(
                descriptor_id="broken-relationship",
                version=1,
                source_id="sales",
                entities=(
                    SemanticEntityDescriptor(
                        entity_id="order",
                        label="Order",
                        fields=(make_field("order_id"),),
                        relationships=(
                            SemanticRelationshipDescriptor(
                                relationship_id="missing-target",
                                source_entity_id="order",
                                target_entity_id="missing",
                                label="Broken link",
                            ),
                        ),
                    ),
                ),
            )
        shared_relationship = SemanticRelationshipDescriptor(
            relationship_id="shared-link",
            source_entity_id="a",
            target_entity_id="b",
            label="Shared link",
        )
        with pytest.raises(ValidationError):
            SemanticDescriptor(
                descriptor_id="duplicate-relationships",
                version=1,
                source_id="sales",
                entities=(
                    SemanticEntityDescriptor(
                        entity_id="a", label="A", fields=(make_field("a_id"),),
                        relationships=(shared_relationship,),
                    ),
                    SemanticEntityDescriptor(
                        entity_id="b", label="B", fields=(make_field("b_id"),),
                        relationships=(shared_relationship,),
                    ),
                ),
            )

    def test_duplicate_field_ids_are_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_entity(fields=(make_field("amount"), make_field("amount")))

    def test_duplicate_relationship_ids_are_rejected(self) -> None:
        relationship = SemanticRelationshipDescriptor(
            relationship_id="r1",
            source_entity_id="customer",
            target_entity_id="order",
            label="Orders",
        )
        with pytest.raises(ValidationError):
            SemanticEntityDescriptor(
                entity_id="order",
                label="Order",
                fields=(make_field("amount"),),
                relationships=(relationship, relationship),
            )

    def test_unknown_result_shapes_are_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ViewMemberRestrictions(result_shape_constraints=("pivot",))

    def test_duplicate_result_shapes_are_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ViewMemberRestrictions(result_shape_constraints=("rows", "rows"))

    def test_purpose_capability_and_flag_identifiers_are_bounded(self) -> None:
        with pytest.raises(ValidationError):
            make_view(allowed_purposes=frozenset({"analytics!"}))
        with pytest.raises(ValidationError):
            make_view(required_capabilities=frozenset({"sql!"}))
        with pytest.raises(ValidationError):
            make_view(required_feature_flags=frozenset({"flag with space"}))
        with pytest.raises(ValidationError):
            make_context(adapter_capabilities=frozenset({"sql;drop"}))
        with pytest.raises(ValidationError):
            make_context(feature_flags=frozenset({"flag with space"}))

    def test_principal_bindings_require_sha256_fingerprints(self) -> None:
        with pytest.raises(ValidationError):
            make_view(bound_principal_authorization_fingerprints=frozenset({"alice"}))

    def test_versions_and_fingerprints_are_bounded(self) -> None:
        with pytest.raises(ValidationError):
            make_view(version=0)
        with pytest.raises(ValidationError):
            make_descriptor(version=1_000_001)
        with pytest.raises(ValidationError):
            make_view(
                provenance=ViewProvenance(
                    descriptor_fingerprint="not-a-fingerprint", resolver_version=1
                )
            )

    def test_models_are_frozen(self) -> None:
        descriptor = make_descriptor()
        with pytest.raises(ValidationError):
            descriptor.source_id = "other"  # type: ignore[misc]
        view = make_view()
        with pytest.raises(ValidationError):
            view.description = "mutated"  # type: ignore[misc]

    def test_extra_fields_are_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SemanticFieldDescriptor(
                field_id="amount", label="Amount", data_type="decimal", physical_name="total"
            )
        with pytest.raises(ValidationError):
            make_view(hidden_policy_rule="never")  # type: ignore[call-arg]
        with pytest.raises(ValidationError):
            make_context(connection_string="mongodb://user:secret@host")  # type: ignore[call-arg]


class TestCanonicalFingerprints:
    def test_fingerprints_are_sha256_prefixed(self) -> None:
        assert make_descriptor().fingerprint.startswith("sha256:")
        assert len(make_descriptor().fingerprint) == 7 + 64
        assert make_view().fingerprint.startswith("sha256:")

    def test_descriptor_fingerprint_is_deterministic_across_mapping_order(self) -> None:
        payload = {
            "descriptor_id": "catalog",
            "version": 1,
            "source_id": "sales",
            "catalog_fingerprint": fp("ab"),
            "entities": [
                {
                    "entity_id": "order",
                    "label": "Order",
                    "description": "",
                    "relationships": [],
                    "fields": [
                        {
                            "field_id": "order_id",
                            "label": "Order id",
                            "description": "",
                            "data_type": "string",
                            "allowed_aggregations": [],
                        },
                        {
                            "field_id": "amount",
                            "label": "Amount",
                            "description": "",
                            "data_type": "decimal",
                            "allowed_aggregations": ["sum", "avg"],
                        },
                    ],
                }
            ],
        }
        #: The same logical payload with different mapping insertion orders
        #: must produce the identical canonical payload and fingerprint.
        reversed_payload = {
            key: payload[key] for key in reversed(list(payload))
        }
        descriptor_a = SemanticDescriptor.model_validate(payload)
        descriptor_b = SemanticDescriptor.model_validate(reversed_payload)
        assert descriptor_a.fingerprint == descriptor_b.fingerprint
        assert canonical_json(descriptor_a.canonical_payload()) == canonical_json(
            descriptor_b.canonical_payload()
        )

    def test_view_fingerprint_is_deterministic_across_frozenset_order(self) -> None:
        view_a = make_view(
            allowed_purposes=frozenset({"analytics", "ops"}),
            required_capabilities=frozenset({"aggregation", "sql"}),
        )
        view_b = make_view(
            allowed_purposes=frozenset({"ops", "analytics"}),
            required_capabilities=frozenset({"sql", "aggregation"}),
        )
        assert view_a.fingerprint == view_b.fingerprint

    def test_equivalent_projection_payloads_fingerprint_identically(self) -> None:
        common = dict(
            view_id="analytics_sales",
            view_version=1,
            descriptor_id="sales_catalog",
            source_id="sales",
            root_entity_ids=frozenset({"order", "customer"}),
            field_ids=frozenset({"amount", "order_id", "region"}),
            allowed_operations=frozenset({"aggregate", "select"}),
            allowed_relationships=frozenset({"customer_orders"}),
            tenant_scope_fingerprint=fp("ee"),
            provenance=ViewProvenance(descriptor_fingerprint=fp("ab"), resolver_version=1),
        )
        projection_a = ResolvedViewProjection(**common)
        projection_b = ResolvedViewProjection(
            **{
                **common,
                "root_entity_ids": frozenset({"customer", "order"}),
                "field_ids": frozenset({"region", "order_id", "amount"}),
                "allowed_operations": frozenset({"select", "aggregate"}),
            }
        )
        assert projection_a.fingerprint == projection_b.fingerprint
        assert projection_a.safe_payload() == projection_b.safe_payload()

    def test_fingerprint_changes_when_any_payload_input_changes(self) -> None:
        base = ResolvedViewProjection(
            view_id="v",
            view_version=1,
            descriptor_id="d",
            source_id="s",
            provenance=ViewProvenance(descriptor_fingerprint=fp("ab"), resolver_version=1),
        )
        changed = ResolvedViewProjection(
            view_id="v",
            view_version=2,
            descriptor_id="d",
            source_id="s",
            provenance=ViewProvenance(descriptor_fingerprint=fp("ab"), resolver_version=1),
        )
        assert base.fingerprint != changed.fingerprint


class TestProjectionImmutability:
    def test_projection_models_are_frozen(self) -> None:
        projection = ResolvedViewProjection(
            view_id="v",
            view_version=1,
            descriptor_id="d",
            source_id="s",
            provenance=ViewProvenance(descriptor_fingerprint=fp("ab"), resolver_version=1),
        )
        with pytest.raises(ValidationError):
            projection.description = "mutated"  # type: ignore[misc]

    def test_projection_rejects_ambiguous_or_inconsistent_members(self) -> None:
        field = make_field("shared", data_type="string")
        entities = (
            SemanticEntityDescriptor(entity_id="a", label="A", fields=(field,)),
            SemanticEntityDescriptor(entity_id="b", label="B", fields=(field,)),
        )
        resolved_entities = tuple(
            ResolvedViewEntity(
                entity_id=entity.entity_id,
                label=entity.label,
                fields=tuple(
                    ResolvedViewField(
                        field_id=item.field_id,
                        label=item.label,
                        data_type=item.data_type,
                    )
                    for item in entity.fields
                ),
            )
            for entity in entities
        )
        with pytest.raises(ValidationError):
            ResolvedViewProjection(
                view_id="v",
                view_version=1,
                descriptor_id="d",
                source_id="s",
                root_entity_ids=frozenset({"a", "b"}),
                field_ids=frozenset({"shared"}),
                entities=resolved_entities,
                provenance=ViewProvenance(descriptor_fingerprint=fp("ab"), resolver_version=1),
            )

    def test_field_alias_mapping_is_deeply_immutable(self) -> None:
        restrictions = ViewMemberRestrictions(field_aliases={"amount": "total_amount"})
        with pytest.raises(TypeError):
            restrictions.field_aliases["amount"] = "other"  # type: ignore[index]
        with pytest.raises(TypeError):
            restrictions.field_aliases["new"] = "alias"  # type: ignore[index]
        with pytest.raises(TypeError):
            restrictions.field_aliases.pop("amount")  # type: ignore[attr-defined]

    def test_aggregation_restriction_mapping_is_deeply_immutable(self) -> None:
        restrictions = ViewMemberRestrictions(
            field_aggregation_restrictions={"amount": frozenset({"sum"})}
        )
        with pytest.raises(TypeError):
            restrictions.field_aggregation_restrictions["amount"] = frozenset({"avg"})  # type: ignore[index]
        with pytest.raises(TypeError):
            restrictions.field_aggregation_restrictions.clear()  # type: ignore[attr-defined]

    def test_restriction_frozensets_never_leak_mutable_views(self) -> None:
        restrictions = ViewMemberRestrictions(
            include_fields=frozenset({"amount"}), allowed_operations=frozenset({"select"})
        )
        assert restrictions.include_fields == frozenset({"amount"})
        assert restrictions.allowed_operations == frozenset({"select"})


class TestSafeSerialization:
    def test_descriptor_payload_excludes_physical_metadata(self) -> None:
        payload = make_descriptor().canonical_payload()
        serialized = canonical_json(payload)
        assert "physical" not in serialized
        assert "connection" not in serialized
        assert "credential" not in serialized

    def test_view_payload_excludes_hidden_policy_rules(self) -> None:
        payload = make_view().canonical_payload()
        serialized = canonical_json(payload)
        assert "rule" not in serialized
        assert "deny" not in serialized
        #: Only safe fingerprint references to policy, never policy internals.
        assert payload["bound_policy_fingerprint"] == fp("cd")

    def test_projection_safe_payload_contains_no_credentials_or_bindings(self) -> None:
        projection = ResolvedViewProjection(
            view_id="analytics_sales",
            view_version=1,
            descriptor_id="sales_catalog",
            source_id="sales",
            description="Analytics surface",
            root_entity_ids=frozenset({"order"}),
            field_ids=frozenset({"amount", "region"}),
            entities=(),
            allowed_operations=frozenset({"select"}),
            catalog_fingerprint=fp("ab"),
            tenant_scope_fingerprint=fp("ee"),
            provenance=ViewProvenance(descriptor_fingerprint=fp("ab"), resolver_version=1),
        )
        serialized = canonical_json(projection.safe_payload())
        for forbidden in ("password", "secret", "credential", "connection", "physical", "binding"):
            assert forbidden not in serialized
        assert projection.safe_payload()["fingerprint"] == projection.fingerprint

    def test_resolution_context_never_exposes_hint_values(self) -> None:
        context = make_context(
            client_hints={"tenant_id": "acme", "principal": "alice", "role": "admin"}
        )
        payload = context.safe_payload()
        serialized = canonical_json(payload)
        assert "acme" not in serialized
        assert "alice" not in serialized
        assert "admin" not in serialized
        assert "tenant_id" in payload["client_hint_keys"]

    def test_client_hints_are_bounded_and_immutable(self) -> None:
        context = make_context(client_hints={"tenant_id": "acme"})
        with pytest.raises(TypeError):
            context.client_hints["tenant_id"] = "other"
        with pytest.raises(ValidationError):
            make_context(client_hints={"hint": "x" * 257})

    def test_issue_safe_payload_is_bounded(self) -> None:
        issue = ResolutionIssue(
            code="purpose_denied", message="the requested purpose is not allowed", member_id=None
        )
        payload = issue.safe_payload()
        assert payload == {
            "code": "purpose_denied",
            "message": "the requested purpose is not allowed",
            "member_id": None,
        }

    def test_outcome_safe_payload_never_carries_a_projection_when_denied(self) -> None:
        outcome = denied("tenant_scope_missing", "resolution requires a trusted tenant scope")
        payload = outcome.safe_payload()
        assert payload["kind"] == "denied"
        assert payload["projection"] is None
        assert len(payload["issues"]) == 1

    def test_unavailable_outcome_safe_payload_is_bounded(self) -> None:
        outcome = unavailable(
            "capability_unsupported",
            "adapter capabilities do not satisfy the view requirements",
            member_id="aggregation",
        )
        payload = outcome.safe_payload()
        assert payload["kind"] == "unavailable"
        assert payload["issues"][0]["member_id"] == "aggregation"

    def test_outcome_consistency_is_enforced(self) -> None:
        with pytest.raises(ValidationError):
            ResolutionOutcome(kind="resolved", projection=None, issues=())
        with pytest.raises(ValidationError):
            ResolutionOutcome(kind="denied", projection=None, issues=())
        with pytest.raises(ValidationError):
            ResolutionOutcome(kind="denied", issues=())  # no issue

    def test_aggregation_kinds_are_literal_bounded(self) -> None:
        with pytest.raises(ValidationError):
            make_field(allowed_aggregations=frozenset({"median"}))
        field = make_field()
        assert isinstance(field.allowed_aggregations, frozenset)
        assert "sum" in field.allowed_aggregations
        assert all(kind in AggregationKind.__args__ for kind in field.allowed_aggregations)
