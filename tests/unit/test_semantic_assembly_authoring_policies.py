"""Authoring validation, lowering contract, and export tests for policies (4.2-4.4)."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import yaml

from nl2data_core.assembly import (
    AssertionProvenanceKind,
    AssertionType,
    ReviewState,
)
from nl2data_core.assembly.authoring import (
    SemanticAssemblyAuthoring,
    SemanticAssemblyAuthoringLoader,
    lower_authoring,
    validate_authoring,
)
from nl2data_core.assembly.authoring.export import export_authoring, export_authoring_draft
from nl2data_core.assembly.lint import LintProfileId, lint_authoring, lint_draft


def full_document(policies: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    document: dict[str, Any] = {
        "apiVersion": "nl2data.io/semantic-assembly-authoring/v1alpha1",
        "kind": "SemanticAssembly",
        "metadata": {"bundleId": "sales", "modelVersion": "1.0.0"},
        "spec": {
            "source": {"sourceId": "warehouse"},
            "entities": [
                {
                    "entityId": "customers",
                    "label": "Customers",
                    "fields": [
                        {
                            "fieldId": "customer_id",
                            "label": "Customer ID",
                            "dataType": "int",
                        },
                        {
                            "fieldId": "tenant_id",
                            "label": "Tenant",
                            "dataType": "int",
                        },
                    ],
                },
                {
                    "entityId": "orders",
                    "label": "Orders",
                    "fields": [
                        {"fieldId": "amount", "label": "Amount", "dataType": "int"}
                    ],
                },
            ],
        },
    }
    if policies is not None:
        document["spec"]["policies"] = policies
    return document


def load_document(payload: dict[str, Any]) -> Any:
    return SemanticAssemblyAuthoringLoader().load(
        yaml.safe_dump(payload, sort_keys=False)
    )


def tenant_isolation(claim: str = "tenant") -> dict[str, Any]:
    return {
        "template": "tenant-isolation",
        "parameters": {
            "entity": "customers",
            "field": "tenant_id",
            "claim": claim,
        },
    }


# -- 4.2: validation failure classes with source-located diagnostics ------


def test_unknown_template_is_source_located() -> None:
    payload = full_document([{"template": "custom", "parameters": {}}])
    result = load_document(payload)
    assert not result.loaded and result.model is None
    diagnostic = result.diagnostics[0]
    assert diagnostic.code == "invalid_member"
    assert diagnostic.path.render() == "$.spec.policies[0].template"
    assert diagnostic.mark is not None


def test_unknown_and_missing_parameter_is_source_located() -> None:
    payload = full_document(
        [
            {
                "template": "purpose-gating",
                "parameters": {"purposes": ["audit"], "mode": "deny"},
            }
        ]
    )
    result = load_document(payload)
    assert not result.loaded
    paths = {diagnostic.path.render() for diagnostic in result.diagnostics}
    assert paths == {
        "$.spec.policies[0].parameters.effect",
        "$.spec.policies[0].parameters.mode",
    }
    assert all(diagnostic.mark is not None for diagnostic in result.diagnostics)


def test_wrong_value_kind_is_source_located() -> None:
    payload = full_document(
        [
            {
                "template": "purpose-gating",
                "parameters": {"purposes": ["audit"], "effect": "block"},
            }
        ]
    )
    result = load_document(payload)
    assert not result.loaded
    assert result.diagnostics[0].path.render() == "$.spec.policies[0].parameters.effect"


def test_parameter_bounds_violation_is_source_located() -> None:
    payload = full_document(
        [
            {
                "template": "purpose-gating",
                "parameters": {
                    "purposes": [f"purpose{index}" for index in range(20)],
                    "effect": "deny",
                },
            }
        ]
    )
    result = load_document(payload)
    assert not result.loaded
    assert result.diagnostics[0].path.render() == (
        "$.spec.policies[0].parameters.purposes"
    )


def test_model_level_list_bound_is_source_located() -> None:
    payload = full_document(
        [
            {
                "template": "row-restriction",
                "parameters": {
                    "entity": "orders",
                    "field": "amount",
                    "allowed_values": list(range(300)),
                },
            }
        ]
    )
    result = load_document(payload)
    assert not result.loaded
    assert result.diagnostics[0].path.render() == "$.spec.policies[0].parameters"


def test_unresolved_entity_target_is_source_located() -> None:
    declaration = tenant_isolation()
    declaration["parameters"]["entity"] = "invoices"
    result = load_document(full_document([declaration]))
    assert not result.loaded
    diagnostic = result.diagnostics[0]
    assert diagnostic.code == "invalid_reference"
    assert diagnostic.path.render() == "$.spec.policies[0].parameters.entity"


def test_unresolved_entity_field_target_is_source_located() -> None:
    payload = full_document(
        [
            {
                "template": "field-masking",
                "parameters": {"fields": ["orders.missing"], "replacement": "***"},
            }
        ]
    )
    result = load_document(payload)
    assert not result.loaded
    assert result.diagnostics[0].code == "invalid_reference"
    assert result.diagnostics[0].path.render() == "$.spec.policies[0].parameters.fields"


def test_duplicate_expanded_identity_names_both_declarations() -> None:
    result = load_document(
        full_document([tenant_isolation("first"), tenant_isolation("second")])
    )
    assert not result.loaded
    diagnostics = result.diagnostics
    assert all(item.code == "duplicate_identity" for item in diagnostics)
    paths = {item.path.render() for item in diagnostics}
    assert paths == {"$.spec.policies[0]", "$.spec.policies[1]"}


def test_unsafe_policy_content_is_rejected_without_echo() -> None:
    payload = full_document(
        [{"template": "row-restriction", "parameters": {"payload": {"a": 1}}}]
    )
    result = load_document(payload)
    assert not result.loaded
    assert result.model is None
    assert "policy_id" not in result.model_dump_json()


def test_policy_failure_blocks_lowering() -> None:
    payload = full_document([tenant_isolation()])
    payload["spec"]["policies"][0]["parameters"]["entity"] = "missing"
    model = SemanticAssemblyAuthoring.model_validate(payload)
    validation = validate_authoring(model)
    lowering = lower_authoring(model, draft_id="draft", author_reference="author")
    assert not validation.valid
    assert not lowering.lowered and lowering.draft is None


# -- 4.2: equivalent-YAML determinism -------------------------------------


def policy_document_text(*, anchored: bool) -> str:
    payload = full_document(
        [
            tenant_isolation(),
            {
                "template": "purpose-gating",
                "parameters": {"purposes": ["audit", "billing"], "effect": "deny"},
            },
        ]
    )
    text = yaml.safe_dump(payload, sort_keys=False)
    if anchored:
        text = (
            "# presentation-only comment\n"
            + text.replace("sourceId: warehouse", "sourceId: &source warehouse", 1).replace(
                "sourceId: warehouse", "sourceId: *source"
            )
        )
    return text


def test_equivalent_yaml_lowering_is_deterministic() -> None:
    loader = SemanticAssemblyAuthoringLoader()
    plain = loader.load(policy_document_text(anchored=False))
    anchored = loader.load(policy_document_text(anchored=True))
    assert plain.model is not None and anchored.model is not None
    first = lower_authoring(plain.model, draft_id="d", author_reference="a").draft
    second = lower_authoring(anchored.model, draft_id="d", author_reference="a").draft
    assert first is not None and second is not None
    assert [(item.id, item.payload_hash()) for item in first.assertions] == [
        (item.id, item.payload_hash()) for item in second.assertions
    ]


def test_policies_entry_order_does_not_change_lowering() -> None:
    payload = full_document(
        [
            tenant_isolation(),
            {
                "template": "field-masking",
                "parameters": {"fields": ["orders.amount"], "replacement": "***"},
            },
        ]
    )
    reordered = deepcopy(payload)
    reordered["spec"]["policies"] = list(reversed(reordered["spec"]["policies"]))
    first = lower_authoring(
        SemanticAssemblyAuthoring.model_validate(payload),
        draft_id="d",
        author_reference="a",
    ).draft
    second = lower_authoring(
        SemanticAssemblyAuthoring.model_validate(reordered),
        draft_id="d",
        author_reference="a",
    ).draft
    assert first is not None and second is not None
    assert [(item.id, item.payload_hash()) for item in first.assertions] == [
        (item.id, item.payload_hash()) for item in second.assertions
    ]


# -- 4.3: contract tests ---------------------------------------------------


def lowered_policy_assertions() -> Any:
    model = SemanticAssemblyAuthoring.model_validate(
        full_document(
            [
                tenant_isolation(),
                {
                    "template": "row-restriction",
                    "parameters": {
                        "entity": "orders",
                        "field": "amount",
                        "allowed_values": [1, 2],
                    },
                },
            ]
        )
    )
    result = lower_authoring(model, draft_id="d", author_reference="a")
    assert result.lowered and result.draft is not None
    assertions = [
        item for item in result.draft.assertions if item.type is AssertionType.POLICY
    ]
    assert len(assertions) == 2
    return result.draft, assertions


def test_expanded_assertions_are_pending_manual_with_no_review_binding() -> None:
    draft, assertions = lowered_policy_assertions()
    assert all(
        item.provenance.kind is AssertionProvenanceKind.MANUAL
        and item.review_state is ReviewState.PENDING
        and item.review_binding is None
        for item in assertions
    )
    assert all(item.provenance.proposal_reference is None for item in assertions)
    assert draft.state.value == "draft"
    assert draft.draft_revision == 0


def test_canonical_payload_contains_resolved_semantics_only() -> None:
    _, assertions = lowered_policy_assertions()
    known_keys = {
        "descriptor_id",
        "policy_id",
        "policy_kind",
        "entity",
        "field",
        "claim",
        "allowed_values",
    }
    for assertion in assertions:
        payload = dict(assertion.payload)
        assert "template" not in payload
        assert set(payload) <= known_keys
        assert {"descriptor_id", "policy_id", "policy_kind"} <= set(payload)
        assert all(not isinstance(value, dict) for value in payload.values())
        assert not any(
            isinstance(value, str) and value.startswith("sha256:")
            for value in payload.values()
        )


def test_draft_carries_no_template_construct() -> None:
    draft, _ = lowered_policy_assertions()
    rendered = draft.model_dump_json()
    assert '"template"' not in rendered
    assert "parameters" not in rendered


# -- 4.4: export round-trip and lint regression ----------------------------


def test_export_round_trip_preserves_policy_identities_and_payload_hashes() -> None:
    model = SemanticAssemblyAuthoring.model_validate(
        full_document(
            [
                tenant_isolation(),
                {
                    "template": "purpose-gating",
                    "parameters": {"purposes": ["audit"], "effect": "allow"},
                },
                {
                    "template": "field-masking",
                    "parameters": {"fields": ["orders.amount"], "replacement": "***"},
                },
            ]
        )
    )
    exported = export_authoring(model)
    assert exported.exported
    reparsed = SemanticAssemblyAuthoringLoader().load(exported.document)
    assert reparsed.loaded
    first = lower_authoring(model, draft_id="d", author_reference="a").draft
    second = lower_authoring(reparsed.model, draft_id="d", author_reference="a").draft
    assert first is not None and second is not None
    assert [(item.id, item.payload_hash()) for item in first.assertions] == [
        (item.id, item.payload_hash()) for item in second.assertions
    ]
    policies = [item for item in second.assertions if item.type is AssertionType.POLICY]
    assert len(policies) == 3


def test_export_orders_policies_presentation_invariantly() -> None:
    payload = full_document(
        [
            tenant_isolation(),
            {
                "template": "field-masking",
                "parameters": {"fields": ["orders.amount"], "replacement": "***"},
            },
        ]
    )
    reordered = deepcopy(payload)
    reordered["spec"]["policies"] = list(reversed(reordered["spec"]["policies"]))
    first = export_authoring(SemanticAssemblyAuthoring.model_validate(payload))
    second = export_authoring(SemanticAssemblyAuthoring.model_validate(reordered))
    assert first.exported and second.exported
    assert first.document == second.document


def test_no_policies_documents_export_without_the_section() -> None:
    model = SemanticAssemblyAuthoring.model_validate(full_document())
    exported = export_authoring(model)
    assert exported.exported
    assert "policies" not in exported.document
    result = lower_authoring(model, draft_id="d", author_reference="a")
    assert result.lowered
    assert result.draft is not None
    assert not any(
        item.type is AssertionType.POLICY for item in result.draft.assertions
    )


def test_draft_export_round_trips_policy_assertions() -> None:
    model = SemanticAssemblyAuthoring.model_validate(full_document([tenant_isolation()]))
    draft = lower_authoring(model, draft_id="d", author_reference="a").draft
    assert draft is not None
    exported = export_authoring_draft(draft)
    assert exported.exported
    assert "policies" in exported.document


def test_lint_regression_on_documents_with_policy_assertions() -> None:
    model = SemanticAssemblyAuthoring.model_validate(
        full_document([tenant_isolation()])
    )
    authoring_result = lint_authoring(model, profile=LintProfileId.RECOMMENDED)
    assert authoring_result.summary is not None
    draft = lower_authoring(model, draft_id="d", author_reference="a").draft
    assert draft is not None
    draft_result = lint_draft(draft, profile=LintProfileId.RECOMMENDED)
    assert draft_result.summary is not None
    assert draft_result.summary.profile == authoring_result.summary.profile
