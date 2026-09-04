"""Builder API: fluent construction equivalent to YAML authoring."""

from __future__ import annotations

import inspect

import pytest

from nl2data_core.assembly.authoring import (
    AuthoringBuilderError,
    AuthoringEntity,
    AuthoringSpec,
    AuthoringVerificationPlan,
    SemanticAssemblyAuthoring,
    SemanticAssemblyAuthoringLoader,
    SemanticAssemblyBuilder,
    export_authoring,
    lower_authoring,
    validate_authoring,
)
from nl2data_core.assembly.models import AssertionType
from nl2data_core.bundles.models import BundleCompatibility
from nl2data_core.views.models import ExprNode, ValueSemantics

_FULL_YAML = """\
apiVersion: nl2data.io/semantic-assembly-authoring/v1alpha1
kind: SemanticAssembly
metadata:
  bundleId: sales
  modelVersion: "1.0.0"
  description: Sales semantic model
spec:
  source:
    sourceId: warehouse
  entities:
    - entityId: customers
      label: Customers
      fields:
        - fieldId: tenant_id
          label: Tenant
          dataType: int
        - fieldId: region
          label: Region
          dataType: str
          allowedAggregations: [none]
          valueSemantics:
            value_mapping:
              east: "E"
    - entityId: orders
      label: Orders
      fields:
        - fieldId: amount
          label: Amount
          dataType: int
          allowedAggregations: [sum, avg]
        - fieldId: customer_id
          label: Customer
          dataType: int
      relationships:
        - relationshipId: orders_customers
          targetEntityId: customers
          sourceFields: [customer_id]
          targetFields: [tenant_id]
          label: Order customer
      calculatedFields:
        - name: double_amount
          label: Double amount
          expression:
            op: mul
            left:
              op: field
              field_id: amount
            right:
              op: const
              const: 2
          outputType: int
          requires: [amount]
  measures:
    - measureId: revenue
      fieldId: amount
      aggregation: sum
      label: Revenue
  grains:
    - grainId: per_customer
      entityId: orders
      attributes: [customer_id]
  policies:
    - template: tenant-isolation
      parameters:
        entity: orders
        field: customer_id
        claim: tenant_id
  sourceReferences:
    - referenceId: primary
      sourceId: warehouse
  compatibility:
    compatible_catalog_fingerprints: []
    notes: Deterministic catalog only
  deploymentBindings:
    - bindingId: prod
      environment: production
      sourceId: warehouse
      connectionReference: env:NL2DATA_DEMO_DSN
  verificationPlan:
    verificationVersion: 1
    policyProfile: production-v1
    policyVersion: 1
    deadlines:
      caseMs: 1000
      layerMs: 2000
      suiteMs: 3000
    smokeCases:
      - caseId: smoke-orders
        query:
          irId: verify-orders
          sourceId: warehouse
          rootEntityId: orders
          selections:
            - selectionId: amount
              fieldId: amount
          limit: 10
          provenance:
            sourceId: warehouse
            rootEntityId: orders
        fixtureProfileId: sqlite-v1
        assertions:
          - assertionId: outcome
            kind: outcome
            expected: success
    semanticCases:
      - caseId: semantic-orders
        query:
          irId: verify-orders
          sourceId: warehouse
          rootEntityId: orders
          selections:
            - selectionId: amount
              fieldId: amount
          limit: 10
          provenance:
            sourceId: warehouse
            rootEntityId: orders
        fixtureProfileId: sqlite-v1
        contracts:
          - assertionId: rows
            kind: row_count_equality
            expected: 1
"""

# Differs from the DDS-020 sketch on purpose: connection references only, no
# physical names, DSNs, credentials, fingerprints, or lifecycle state.
_SPEC_SURFACE = {
    "source": "source",
    "entities": "entity",
    "measures": "measure",
    "grains": "grain",
    "policies": "policy",
    "source_references": "source_reference",
    "compatibility": "compatibility",
    "deployment_bindings": "deployment_binding",
    "verification_plan": "verification_plan",
}

_ENTITY_SURFACE = {
    "entity_id": None,
    "label": None,
    "description": None,
    "fields": "field",
    "relationships": "relationship",
    "calculated_fields": "calculated_field",
}


def _expression() -> ExprNode:
    return ExprNode(
        op="mul",
        left=ExprNode(op="field", field_id="amount"),
        right=ExprNode(op="const", const=2),
    )


def _verification_mapping() -> dict[str, object]:
    query = {
        "irId": "verify-orders",
        "sourceId": "warehouse",
        "rootEntityId": "orders",
        "selections": [{"selectionId": "amount", "fieldId": "amount"}],
        "limit": 10,
        "provenance": {"sourceId": "warehouse", "rootEntityId": "orders"},
    }
    return {
        "verificationVersion": 1,
        "policyProfile": "production-v1",
        "policyVersion": 1,
        "deadlines": {"caseMs": 1000, "layerMs": 2000, "suiteMs": 3000},
        "smokeCases": [
            {
                "caseId": "smoke-orders",
                "query": dict(query),
                "fixtureProfileId": "sqlite-v1",
                "assertions": [
                    {"assertionId": "outcome", "kind": "outcome", "expected": "success"}
                ],
            }
        ],
        "semanticCases": [
            {
                "caseId": "semantic-orders",
                "query": dict(query),
                "fixtureProfileId": "sqlite-v1",
                "contracts": [{"assertionId": "rows", "kind": "row_count_equality", "expected": 1}],
            }
        ],
    }


def _build_full_document() -> SemanticAssemblyAuthoring:
    builder = SemanticAssemblyBuilder("sales", "1.0.0", "Sales semantic model")
    builder.source("warehouse")
    (
        builder.entity("customers", "Customers")
        .field("tenant_id", "Tenant", "int")
        .field(
            "region",
            "Region",
            "str",
            allowed_aggregations=("none",),
            value_semantics=ValueSemantics(value_mapping={"east": "E"}),
        )
        .done()
        .entity("orders", "Orders")
        .field("amount", "Amount", "int", allowed_aggregations=("sum", "avg"))
        .field("customer_id", "Customer", "int")
        .relationship(
            "orders_customers",
            "customers",
            ("customer_id",),
            ("tenant_id",),
            "Order customer",
        )
        .calculated_field(
            "double_amount",
            "Double amount",
            _expression(),
            "int",
            requires=("amount",),
        )
        .done()
    )
    builder.measure("revenue", "amount", "Revenue", aggregation="sum")
    builder.grain("per_customer", "orders", attributes=("customer_id",))
    builder.policy("tenant-isolation", entity="orders", field="customer_id", claim="tenant_id")
    builder.source_reference("primary", "warehouse")
    builder.compatibility(notes="Deterministic catalog only")
    builder.deployment_binding("prod", "production", "warehouse", "env:NL2DATA_DEMO_DSN")
    builder.verification_plan(_verification_mapping())
    return builder.build()


def _loaded_full_document() -> SemanticAssemblyAuthoring:
    parsed = SemanticAssemblyAuthoringLoader().load(_FULL_YAML)
    assert parsed.loaded, [diagnostic.message for diagnostic in parsed.diagnostics]
    assert parsed.model is not None
    return parsed.model


# --- Fluent surface coverage -------------------------------------------------


def test_full_document_builds_every_schema_section() -> None:
    document = _build_full_document()
    assert document.metadata.bundle_id == "sales"
    assert document.spec.source.source_id == "warehouse"
    customers, orders = document.spec.entities
    assert [field.field_id for field in customers.fields] == ["tenant_id", "region"]
    assert customers.fields[1].value_semantics is not None
    assert orders.relationships[0].target_entity_id == "customers"
    assert orders.calculated_fields[0].name == "double_amount"
    assert document.spec.measures[0].measure_id == "revenue"
    assert document.spec.grains[0].grain_id == "per_customer"
    assert document.spec.policies[0].template == "tenant-isolation"
    assert document.spec.source_references[0].reference_id == "primary"
    assert document.spec.compatibility.notes == "Deterministic catalog only"
    assert document.spec.deployment_bindings[0].connection_reference == "env:NL2DATA_DEMO_DSN"
    assert document.spec.verification_plan is not None
    assert document.spec.verification_plan.policy_profile == "production-v1"


def test_minimal_document_builds_with_defaults() -> None:
    document = (
        SemanticAssemblyBuilder("sales", "1.0.0")
        .source("warehouse")
        .entity("orders", "Orders")
        .done()
        .build()
    )
    assert document.spec.compatibility == BundleCompatibility()
    assert document.spec.policies == ()
    assert document.spec.verification_plan is None
    assert document.metadata.description == ""


def test_verification_plan_accepts_instance_and_mapping_equivalently() -> None:
    plan = AuthoringVerificationPlan.model_validate(_verification_mapping())
    from_instance = (
        SemanticAssemblyBuilder("sales", "1.0.0")
        .source("warehouse")
        .entity("orders", "Orders")
        .field("amount", "Amount", "int")
        .done()
    )
    from_instance.verification_plan(plan)
    instance_document = from_instance.build()

    from_mapping = (
        SemanticAssemblyBuilder("sales", "1.0.0")
        .source("warehouse")
        .entity("orders", "Orders")
        .field("amount", "Amount", "int")
        .done()
    )
    from_mapping.verification_plan(_verification_mapping())
    mapping_document = from_mapping.build()

    assert instance_document.spec.verification_plan == plan
    assert mapping_document == instance_document


# --- Construction-time rejection ---------------------------------------------


def test_unsafe_description_is_rejected_at_construction() -> None:
    with pytest.raises(AuthoringBuilderError):
        SemanticAssemblyBuilder("sales", "1.0.0", "connect via postgres://db.example.com")


def test_oversized_policy_parameter_collection_is_rejected() -> None:
    builder = SemanticAssemblyBuilder("sales", "1.0.0")
    with pytest.raises(AuthoringBuilderError, match="at most 8"):
        builder.policy("tenant-isolation", **{f"param{index}": index for index in range(9)})


def test_non_scalar_policy_parameter_is_rejected() -> None:
    builder = SemanticAssemblyBuilder("sales", "1.0.0")
    with pytest.raises(AuthoringBuilderError, match="bounded scalar"):
        builder.policy("tenant-isolation", entity="orders", field="customer_id", claim={"a": 1})


def test_forbidden_verification_key_is_rejected() -> None:
    builder = SemanticAssemblyBuilder("sales", "1.0.0")
    payload = dict(_verification_mapping())
    payload["fingerprint"] = "sha256:" + "1" * 64
    with pytest.raises(AuthoringBuilderError, match="lifecycle evidence"):
        builder.verification_plan(payload)


def test_identifier_violation_is_rejected() -> None:
    builder = SemanticAssemblyBuilder("sales", "1.0.0")
    with pytest.raises(AuthoringBuilderError):
        builder.entity("orders", "Orders").field("has space", "Amount", "int")


def test_malformed_fingerprint_argument_is_rejected() -> None:
    builder = SemanticAssemblyBuilder("sales", "1.0.0")
    with pytest.raises(AuthoringBuilderError):
        builder.source("warehouse", catalog_fingerprint="sha256:not-hex")


def test_compatibility_instance_and_field_arguments_are_mutually_exclusive() -> None:
    builder = SemanticAssemblyBuilder("sales", "1.0.0")
    with pytest.raises(AuthoringBuilderError, match="instance or field arguments"):
        builder.compatibility(BundleCompatibility(), notes="notes")
    with pytest.raises(AuthoringBuilderError, match="BundleCompatibility"):
        builder.compatibility("not-a-compatibility")  # type: ignore[arg-type]


def test_failed_construction_leaves_builder_usable() -> None:
    builder = SemanticAssemblyBuilder("sales", "1.0.0").source("warehouse")
    entity = builder.entity("orders", "Orders")
    with pytest.raises(AuthoringBuilderError):
        entity.field("has space", "Amount", "int")
    document = entity.field("amount", "Amount", "int").done().build()
    assert [field.field_id for field in document.spec.entities[0].fields] == ["amount"]


# --- Structural misuse --------------------------------------------------------


def _minimal_builder() -> SemanticAssemblyBuilder:
    return SemanticAssemblyBuilder("sales", "1.0.0").source("warehouse")


def test_double_build_is_rejected() -> None:
    builder = _minimal_builder().entity("orders", "Orders").done()
    builder.build()
    with pytest.raises(AuthoringBuilderError, match="already been built"):
        builder.build()


def test_entity_scoped_call_after_done_is_rejected() -> None:
    builder = _minimal_builder()
    entity = builder.entity("orders", "Orders")
    entity.done()
    with pytest.raises(AuthoringBuilderError, match="no longer open"):
        entity.field("amount", "Amount", "int")


def test_done_twice_is_rejected() -> None:
    builder = _minimal_builder()
    entity = builder.entity("orders", "Orders")
    entity.done()
    with pytest.raises(AuthoringBuilderError, match="no longer open"):
        entity.done()


def test_second_entity_scope_must_wait_for_done() -> None:
    builder = _minimal_builder()
    builder.entity("orders", "Orders")
    with pytest.raises(AuthoringBuilderError, match="still open"):
        builder.entity("customers", "Customers")


def test_build_with_open_entity_scope_is_rejected() -> None:
    builder = _minimal_builder()
    builder.entity("orders", "Orders")
    with pytest.raises(AuthoringBuilderError, match="entity scope is still open"):
        builder.build()


def test_orphan_entity_builder_use_after_parent_build_is_rejected() -> None:
    builder = _minimal_builder()
    orphan = builder.entity("orders", "Orders")
    orphan.done()
    builder.build()
    with pytest.raises(AuthoringBuilderError, match="no longer open"):
        orphan.field("amount", "Amount", "int")


def test_top_level_mutation_after_build_is_rejected() -> None:
    builder = _minimal_builder().entity("orders", "Orders").done()
    builder.build()
    with pytest.raises(AuthoringBuilderError, match="already been built"):
        builder.source("other")


# --- Reflection parity ---------------------------------------------------------


def test_builder_surface_covers_every_spec_field() -> None:
    assert set(AuthoringSpec.model_fields) == set(_SPEC_SURFACE)
    for field_name, method_name in _SPEC_SURFACE.items():
        method = getattr(SemanticAssemblyBuilder, method_name, None)
        assert callable(method), f"spec field {field_name} has no builder method"


def test_builder_surface_covers_every_entity_field() -> None:
    assert set(AuthoringEntity.model_fields) == set(_ENTITY_SURFACE)
    entity_parameters = inspect.signature(SemanticAssemblyBuilder.entity).parameters
    from nl2data_core.assembly.authoring.builder import _AuthoringEntityBuilder

    for field_name, method_name in _ENTITY_SURFACE.items():
        if method_name is None:
            assert field_name in entity_parameters, f"entity field {field_name}"
        else:
            method = getattr(_AuthoringEntityBuilder, method_name, None)
            assert callable(method), f"entity field {field_name} has no builder method"


# --- Differential equivalence ---------------------------------------------------


def test_builder_document_equals_equivalent_yaml_document() -> None:
    assert _build_full_document() == _loaded_full_document()


def test_builder_and_yaml_produce_identical_pipeline_artifacts() -> None:
    built = _build_full_document()
    loaded = _loaded_full_document()
    built_validation = validate_authoring(built)
    loaded_validation = validate_authoring(loaded)
    assert built_validation.valid and loaded_validation.valid
    assert built_validation.summary == loaded_validation.summary
    built_lowered = lower_authoring(built, draft_id="draft-1", author_reference="author-1")
    loaded_lowered = lower_authoring(loaded, draft_id="draft-1", author_reference="author-1")
    assert built_lowered.draft is not None and loaded_lowered.draft is not None
    assert [
        (assertion.id, assertion.payload_hash()) for assertion in built_lowered.draft.assertions
    ] == [(assertion.id, assertion.payload_hash()) for assertion in loaded_lowered.draft.assertions]
    built_export = export_authoring(built)
    loaded_export = export_authoring(loaded)
    assert built_export.document is not None
    assert built_export.document == loaded_export.document


def test_call_order_independence_across_independent_sections() -> None:
    def build_first() -> SemanticAssemblyAuthoring:
        builder = SemanticAssemblyBuilder("sales", "1.0.0")
        builder.measure("revenue", "amount", "Revenue", aggregation="sum")
        builder.grain("per_customer", "orders", attributes=("customer_id",))
        builder.policy("tenant-isolation", entity="orders", field="customer_id", claim="claim")
        builder.source_reference("primary", "warehouse")
        builder.deployment_binding("prod", "production", "warehouse", "env:NL2DATA_DEMO_DSN")
        builder.source("warehouse")
        (
            builder.entity("orders", "Orders")
            .field("amount", "Amount", "int", allowed_aggregations=("sum",))
            .field("customer_id", "Customer", "int")
            .done()
        )
        return builder.build()

    def build_second() -> SemanticAssemblyAuthoring:
        builder = SemanticAssemblyBuilder("sales", "1.0.0")
        builder.source("warehouse")
        (
            builder.entity("orders", "Orders")
            .field("amount", "Amount", "int", allowed_aggregations=("sum",))
            .field("customer_id", "Customer", "int")
            .done()
        )
        builder.deployment_binding("prod", "production", "warehouse", "env:NL2DATA_DEMO_DSN")
        builder.source_reference("primary", "warehouse")
        builder.policy("tenant-isolation", entity="orders", field="customer_id", claim="claim")
        builder.grain("per_customer", "orders", attributes=("customer_id",))
        builder.measure("revenue", "amount", "Revenue", aggregation="sum")
        return builder.build()

    first = build_first()
    second = build_second()
    assert first == second
    first_lowered = lower_authoring(first, draft_id="draft-1", author_reference="author-1")
    second_lowered = lower_authoring(second, draft_id="draft-1", author_reference="author-1")
    assert first_lowered.draft is not None and second_lowered.draft is not None
    assert [
        (assertion.id, assertion.payload_hash()) for assertion in first_lowered.draft.assertions
    ] == [(assertion.id, assertion.payload_hash()) for assertion in second_lowered.draft.assertions]
    assert export_authoring(first).document == export_authoring(second).document


# --- Error surface --------------------------------------------------------------


def test_rejection_message_is_bounded_pathed_and_non_echoing() -> None:
    builder = _minimal_builder()
    secret = "postgres://user:password@db.example.com/prod"
    with pytest.raises(AuthoringBuilderError) as excinfo:
        builder.entity("orders", "Orders").field(
            "amount", "Amount", "int", description=f"See {secret}"
        )
    error = excinfo.value
    assert len(error.message) <= 256
    assert secret not in str(error)
    assert not hasattr(error, "mark")
    assert error.path.render() == "$.spec.entities[0].fields[0]"


def test_misuse_error_is_bounded_and_pathed() -> None:
    builder = _minimal_builder()
    builder.entity("orders", "Orders")
    with pytest.raises(AuthoringBuilderError) as excinfo:
        builder.entity("customers", "Customers")
    error = excinfo.value
    assert len(error.message) <= 256
    assert not hasattr(error, "mark")


def test_policies_and_verification_plan_lower_through_pipeline() -> None:
    document = _build_full_document()
    lowered = lower_authoring(document, draft_id="draft-1", author_reference="author-1")
    assert lowered.draft is not None
    assert any(assertion.type is AssertionType.POLICY for assertion in lowered.draft.assertions)
    assert lowered.draft.verification_plan is not None
    assert lowered.draft.verification_plan.policy_profile == "production-v1"
