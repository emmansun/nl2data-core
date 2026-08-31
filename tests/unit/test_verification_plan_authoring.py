"""Verification plan YAML authoring and lifecycle-bound lowering."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from nl2data_core.assembly.authoring import (
    AUTHORING_API_VERSION,
    SemanticAssemblyAuthoring,
    export_authoring,
    lower_authoring,
)


def _query() -> dict[str, object]:
    return {
        "irId": "verify-orders",
        "sourceId": "warehouse",
        "rootEntityId": "orders",
        "selections": [
            {"selectionId": "amount", "fieldId": "amount"},
        ],
        "limit": 10,
        "provenance": {
            "sourceId": "warehouse",
            "rootEntityId": "orders",
        },
    }


def _document() -> dict[str, object]:
    return {
        "apiVersion": AUTHORING_API_VERSION,
        "kind": "SemanticAssembly",
        "metadata": {"bundleId": "sales", "modelVersion": "1.0.0"},
        "spec": {
            "source": {"sourceId": "warehouse"},
            "entities": [
                {
                    "entityId": "orders",
                    "label": "Orders",
                    "fields": [
                        {"fieldId": "amount", "label": "Amount", "dataType": "int"}
                    ],
                }
            ],
            "verificationPlan": {
                "verificationVersion": 1,
                "policyProfile": "production-v1",
                "policyVersion": 1,
                "deadlines": {"caseMs": 1000, "layerMs": 2000, "suiteMs": 3000},
                "smokeCases": [
                    {
                        "caseId": "smoke-orders",
                        "query": _query(),
                        "fixtureProfileId": "sqlite-v1",
                        "assertions": [
                            {
                                "assertionId": "outcome",
                                "kind": "outcome",
                                "expected": "success",
                            }
                        ],
                    }
                ],
                "semanticCases": [
                    {
                        "caseId": "semantic-orders",
                        "query": _query(),
                        "fixtureProfileId": "sqlite-v1",
                        "contracts": [
                            {
                                "assertionId": "rows",
                                "kind": "row_count_equality",
                                "expected": 1,
                            }
                        ],
                    }
                ],
            },
        },
    }


def test_verification_plan_lowers_and_exports_without_computed_identity() -> None:
    model = SemanticAssemblyAuthoring.model_validate(_document())
    lowered = lower_authoring(model, draft_id="draft-1", author_reference="author-1")
    assert lowered.draft is not None
    assert lowered.draft.verification_plan is not None
    assert lowered.draft.verification_plan.policy_profile == "production-v1"
    exported = export_authoring(model)
    assert exported.document is not None
    assert "verificationPlan" in exported.document
    assert '"fingerprint":' not in exported.document
    reparsed = SemanticAssemblyAuthoring.model_validate(
        model.authoring_payload()
    )
    assert reparsed.spec.verification_plan is not None
    assert (
        reparsed.spec.verification_plan.to_plan().fingerprint
        == lowered.draft.verification_plan.fingerprint
    )


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("fingerprint",), "sha256:" + "1" * 64),
        (("status",), "passed"),
        (("executorId",), "executor"),
        (("evidence",), {"rows": [["secret"]]}),
    ],
)
def test_verification_authoring_rejects_lifecycle_evidence(
    path: tuple[str, ...], value: object
) -> None:
    payload = _document()
    plan = payload["spec"]["verificationPlan"]  # type: ignore[index]
    plan[path[0]] = value  # type: ignore[index]
    with pytest.raises(ValidationError, match="lifecycle evidence"):
        SemanticAssemblyAuthoring.model_validate(payload)


def test_verification_authoring_rejects_backend_syntax_and_unknown_fields() -> None:
    payload = _document()
    query = payload["spec"]["verificationPlan"]["smokeCases"][0]["query"]  # type: ignore[index]
    query["sql"] = "SELECT amount FROM orders"  # type: ignore[index]
    with pytest.raises(ValidationError):
        SemanticAssemblyAuthoring.model_validate(payload)

    drifted = _document()
    drifted_query = drifted["spec"]["verificationPlan"]["smokeCases"][0]["query"]  # type: ignore[index]
    drifted_query["sourceId"] = "other"  # type: ignore[index]
    with pytest.raises(ValidationError, match="document source"):
        SemanticAssemblyAuthoring.model_validate(drifted)