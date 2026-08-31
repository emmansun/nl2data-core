"""Tests for semantic authoring validation and deterministic lowering."""

from __future__ import annotations

from copy import deepcopy

import yaml

from nl2data_core.assembly import AssemblyState, AssertionProvenanceKind, AssertionType, ReviewState
from nl2data_core.assembly.authoring import (
    SemanticAssemblyAuthoring,
    SemanticAssemblyAuthoringLoader,
    lower_authoring,
    validate_authoring,
)


def full_document() -> dict[str, object]:
    return {
        "apiVersion": "nl2data.io/semantic-assembly-authoring/v1alpha1",
        "kind": "SemanticAssembly",
        "metadata": {"bundleId": "sales", "modelVersion": "1.0.0"},
        "spec": {
            "source": {"sourceId": "warehouse"},
            "entities": [
                {
                    "entityId": "orders",
                    "label": "Orders",
                    "fields": [
                        {
                            "fieldId": "order_id",
                            "label": "Order ID",
                            "dataType": "int",
                        },
                        {
                            "fieldId": "amount",
                            "label": "Amount",
                            "dataType": "int",
                            "allowedAggregations": ["sum"],
                            "valueSemantics": {
                                "value_mapping": {"small": 1},
                                "display_order": ["small"],
                            },
                        },
                    ],
                    "relationships": [
                        {
                            "relationshipId": "orders_customer",
                            "targetEntityId": "customers",
                            "sourceFields": ["order_id"],
                            "targetFields": ["customer_id"],
                            "label": "Customer",
                        }
                    ],
                    "calculatedFields": [
                        {
                            "name": "double_amount",
                            "label": "Double amount",
                            "expression": {
                                "op": "mul",
                                "left": {"op": "field", "field_id": "amount"},
                                "right": {"op": "const", "const": 2},
                            },
                            "outputType": "int",
                            "requires": ["amount"],
                        }
                    ],
                },
                {
                    "entityId": "customers",
                    "label": "Customers",
                    "fields": [
                        {"fieldId": "customer_id", "label": "Customer ID", "dataType": "int"}
                    ],
                },
            ],
            "measures": [
                {
                    "measureId": "revenue",
                    "fieldId": "amount",
                    "aggregation": "sum",
                    "label": "Revenue",
                }
            ],
            "grains": [
                {"grainId": "order", "entityId": "orders", "attributes": ["order_id"]}
            ],
            "sourceReferences": [{"referenceId": "primary", "sourceId": "warehouse"}],
            "deploymentBindings": [
                {
                    "bindingId": "prod",
                    "environment": "production",
                    "sourceId": "warehouse",
                    "connectionReference": "env:WAREHOUSE_DSN",
                }
            ],
        },
    }


def test_every_assertion_type_lowers_pending_with_trusted_identity() -> None:
    model = SemanticAssemblyAuthoring.model_validate(full_document())
    validation = validate_authoring(model)
    result = lower_authoring(model, draft_id="trusted-draft", author_reference="trusted-author")
    assert validation.valid
    assert result.lowered
    assert result.draft is not None
    assert result.draft.draft_id == "trusted-draft"
    assert result.draft.author_reference == "trusted-author"
    assert result.draft.state is AssemblyState.DRAFT
    assert result.draft.draft_revision == 0
    assert {assertion.type for assertion in result.draft.assertions} == set(AssertionType) - {
        AssertionType.POLICY
    }
    assert all(
        assertion.provenance.kind is AssertionProvenanceKind.MANUAL
        and assertion.review_state is ReviewState.PENDING
        and assertion.review_binding is None
        for assertion in result.draft.assertions
    )
    assert tuple(item.id for item in result.draft.assertions) == tuple(
        sorted(item.id for item in result.draft.assertions)
    )
    assert result.draft.deployment_bindings[0].connection_reference == "env:WAREHOUSE_DSN"


def test_mapping_order_does_not_change_ids_or_payload_hashes() -> None:
    first_payload = full_document()
    second_payload = deepcopy(first_payload)
    second_payload["metadata"] = {"modelVersion": "1.0.0", "bundleId": "sales"}
    first = lower_authoring(
        SemanticAssemblyAuthoring.model_validate(first_payload),
        draft_id="draft",
        author_reference="author",
    ).draft
    second = lower_authoring(
        SemanticAssemblyAuthoring.model_validate(second_payload),
        draft_id="draft",
        author_reference="author",
    ).draft
    assert first is not None and second is not None
    assert [(item.id, item.payload_hash()) for item in first.assertions] == [
        (item.id, item.payload_hash()) for item in second.assertions
    ]


def test_comments_and_aliases_do_not_change_lowering() -> None:
    plain_text = yaml.safe_dump(full_document(), sort_keys=False)
    anchored_text = "# presentation-only comment\n" + plain_text.replace(
        "sourceId: warehouse",
        "sourceId: &source warehouse",
        1,
    ).replace("sourceId: warehouse", "sourceId: *source")
    loader = SemanticAssemblyAuthoringLoader()
    plain = loader.load(plain_text)
    anchored = loader.load(anchored_text)
    assert plain.model is not None and anchored.model is not None
    first = lower_authoring(plain.model, draft_id="draft", author_reference="author").draft
    second = lower_authoring(anchored.model, draft_id="draft", author_reference="author").draft
    assert first is not None and second is not None
    assert [(item.id, item.payload_hash()) for item in first.assertions] == [
        (item.id, item.payload_hash()) for item in second.assertions
    ]


def test_unknown_and_ambiguous_references_return_no_model_or_draft() -> None:
    unknown = full_document()
    spec = unknown["spec"]
    assert isinstance(spec, dict)
    measures = spec["measures"]
    assert isinstance(measures, list)
    assert isinstance(measures[0], dict)
    measures[0]["fieldId"] = "missing"
    model = SemanticAssemblyAuthoring.model_validate(unknown)
    validation = validate_authoring(model)
    lowering = lower_authoring(model, draft_id="draft", author_reference="author")
    assert not validation.valid and validation.model is None
    assert not lowering.lowered and lowering.draft is None
    assert validation.diagnostics[0].path.render() == "$.spec.measures[0].fieldId"

    duplicate = full_document()
    spec = duplicate["spec"]
    assert isinstance(spec, dict)
    entities = spec["entities"]
    assert isinstance(entities, list) and isinstance(entities[1], dict)
    fields = entities[1]["fields"]
    assert isinstance(fields, list) and isinstance(fields[0], dict)
    fields[0]["fieldId"] = "amount"
    yaml_text = __import__("yaml").safe_dump(duplicate, sort_keys=False)
    parse = SemanticAssemblyAuthoringLoader().load(yaml_text)
    assert not parse.loaded and parse.model is None


def test_reference_failure_retains_source_location() -> None:
    payload = full_document()
    spec = payload["spec"]
    assert isinstance(spec, dict)
    measures = spec["measures"]
    assert isinstance(measures, list) and isinstance(measures[0], dict)
    measures[0]["fieldId"] = "missing"
    result = SemanticAssemblyAuthoringLoader().load(yaml.safe_dump(payload, sort_keys=False))
    assert result.model is None
    assert result.diagnostics[0].path.render() == "$.spec.measures[0].fieldId"
    assert result.diagnostics[0].mark is not None


def test_inline_deployment_secret_fails_without_echoing_value() -> None:
    payload = full_document()
    spec = payload["spec"]
    assert isinstance(spec, dict)
    bindings = spec["deploymentBindings"]
    assert isinstance(bindings, list) and isinstance(bindings[0], dict)
    bindings[0]["connectionReference"] = "postgres://user:secret@host/db"
    model = SemanticAssemblyAuthoring.model_validate(payload)
    result = validate_authoring(model)
    assert not result.valid
    assert "postgres" not in result.model_dump_json()