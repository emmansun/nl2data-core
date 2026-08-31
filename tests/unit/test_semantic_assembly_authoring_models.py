"""Tests for the semantic-only authoring contract."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from nl2data_core.assembly.authoring import AUTHORING_API_VERSION, SemanticAssemblyAuthoring


def minimal_document() -> dict[str, object]:
    return {
        "apiVersion": AUTHORING_API_VERSION,
        "kind": "SemanticAssembly",
        "metadata": {"bundleId": "sales", "modelVersion": "1.0.0"},
        "spec": {
            "source": {"sourceId": "warehouse"},
            "entities": [{"entityId": "orders", "label": "Orders"}],
        },
    }


def test_minimal_document_is_frozen_and_bounded() -> None:
    document = SemanticAssemblyAuthoring.model_validate(minimal_document())
    assert document.metadata.bundle_id == "sales"
    assert document.spec.entities[0].entity_id == "orders"
    with pytest.raises(ValidationError, match="frozen"):
        document.metadata.bundle_id = "other"  # type: ignore[misc]


def test_full_document_accepts_semantic_and_safe_deployment_content() -> None:
    payload = minimal_document()
    payload["spec"] = {
        "source": {"sourceId": "warehouse"},
        "entities": [
            {
                "entityId": "orders",
                "label": "Orders",
                "fields": [
                    {
                        "fieldId": "amount",
                        "label": "Amount",
                        "dataType": "int",
                        "allowedAggregations": ["sum"],
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
            }
        ],
        "measures": [
            {
                "measureId": "revenue",
                "fieldId": "amount",
                "aggregation": "sum",
                "label": "Revenue",
            }
        ],
        "grains": [{"grainId": "order", "entityId": "orders", "attributes": ["amount"]}],
        "sourceReferences": [{"referenceId": "primary", "sourceId": "warehouse"}],
        "deploymentBindings": [
            {
                "bindingId": "prod",
                "environment": "production",
                "sourceId": "warehouse",
                "connectionReference": "vault:data/warehouse",
            }
        ],
    }
    document = SemanticAssemblyAuthoring.model_validate(payload)
    assert document.spec.measures[0].measure_id == "revenue"
    assert document.authoring_payload()["metadata"]["bundleId"] == "sales"


@pytest.mark.parametrize(
    ("member", "value"),
    [("apiVersion", "nl2data.io/semantic-assembly-authoring/v9"), ("kind", "AssemblyDraft")],
)
def test_unsupported_version_or_kind_is_rejected(member: str, value: str) -> None:
    payload = minimal_document()
    payload[member] = value
    with pytest.raises(ValidationError):
        SemanticAssemblyAuthoring.model_validate(payload)


@pytest.mark.parametrize(
    "forbidden",
    [
        "id",
        "assertionId",
        "provenance",
        "reviewState",
        "reviewBinding",
        "draftRevision",
        "approvedBy",
        "publishAudit",
        "fingerprint",
        "activationState",
    ],
)
def test_lifecycle_owned_and_unknown_members_are_rejected(forbidden: str) -> None:
    payload = minimal_document()
    payload[forbidden] = "caller-controlled"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SemanticAssemblyAuthoring.model_validate(payload)


def test_descriptor_global_duplicates_and_source_mismatch_are_rejected() -> None:
    payload = minimal_document()
    spec = payload["spec"]
    assert isinstance(spec, dict)
    spec["entities"] = [
        {"entityId": "orders", "label": "Orders"},
        {"entityId": "orders", "label": "Duplicate"},
    ]
    with pytest.raises(ValidationError, match="descriptor-global"):
        SemanticAssemblyAuthoring.model_validate(payload)

    payload = minimal_document()
    spec = payload["spec"]
    assert isinstance(spec, dict)
    spec["sourceReferences"] = [{"referenceId": "other", "sourceId": "other"}]
    with pytest.raises(ValidationError, match="must match the document source"):
        SemanticAssemblyAuthoring.model_validate(payload)


def test_collection_bounds_are_enforced() -> None:
    payload = minimal_document()
    spec = payload["spec"]
    assert isinstance(spec, dict)
    spec["entities"] = [
        {"entityId": f"entity-{index}", "label": "Entity"} for index in range(1_025)
    ]
    with pytest.raises(ValidationError, match="at most 1024"):
        SemanticAssemblyAuthoring.model_validate(payload)


def test_descriptor_wide_field_bound_is_enforced_across_entities() -> None:
    payload = minimal_document()
    spec = payload["spec"]
    assert isinstance(spec, dict)
    spec["entities"] = [
        {
            "entityId": f"entity-{entity_index}",
            "label": "Entity",
            "fields": [
                {
                    "fieldId": f"field-{entity_index}-{field_index}",
                    "label": "Field",
                    "dataType": "int",
                }
                for field_index in range(field_count)
            ],
        }
        for entity_index, field_count in enumerate((2_049, 2_048))
    ]
    with pytest.raises(ValidationError, match="fields must contain at most 4096"):
        SemanticAssemblyAuthoring.model_validate(payload)