"""Unit tests for the closed policy template registry and expansion (4.1)."""

from __future__ import annotations

import re

import pytest
from pydantic import ValidationError

from nl2data_core.assembly.authoring import (
    POLICY_TEMPLATE_NAMES,
    AuthoringPolicyTemplate,
    PolicyTemplateError,
    expand_policy_templates,
)
from nl2data_core.assembly.authoring.models import SemanticAssemblyAuthoring
from nl2data_core.assembly.authoring.policy_templates import (
    MAX_POLICY_DECLARATIONS,
    MAX_POLICY_LIST_ITEMS,
    MAX_POLICY_PARAM_ENTRIES,
    POLICY_TEMPLATE_SPECS,
    expanded_policy_id,
    normalize_policy_parameters,
)

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$")


def minimal_model(policies: list[dict[str, object]]) -> SemanticAssemblyAuthoring:
    return SemanticAssemblyAuthoring.model_validate(
        {
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
                                "fieldId": "tenant_id",
                                "label": "Tenant",
                                "dataType": "int",
                            }
                        ],
                    }
                ],
                "policies": policies,
            },
        }
    )


def test_registry_is_closed_with_four_templates() -> None:
    assert frozenset(
        {"tenant-isolation", "row-restriction", "purpose-gating", "field-masking"}
    ) == POLICY_TEMPLATE_NAMES
    assert set(POLICY_TEMPLATE_SPECS) == POLICY_TEMPLATE_NAMES


def test_registry_parameter_schemas_are_typed_and_bounded() -> None:
    tenant = POLICY_TEMPLATE_SPECS["tenant-isolation"]
    assert [parameter.name for parameter in tenant.parameters] == [
        "entity",
        "field",
        "claim",
    ]
    row = POLICY_TEMPLATE_SPECS["row-restriction"]
    allowed = {parameter.name: parameter for parameter in row.parameters}
    assert allowed["allowed_values"].max_items == 256
    gating = POLICY_TEMPLATE_SPECS["purpose-gating"]
    purposes = {
        parameter.name: parameter
        for parameter in gating.parameters
    }
    assert purposes["purposes"].max_items == 16
    assert purposes["effect"].choices == ("allow", "deny")
    masking = {
        parameter.name: parameter
        for parameter in POLICY_TEMPLATE_SPECS["field-masking"].parameters
    }
    assert masking["fields"].max_items == 64


def test_identity_is_target_derived_and_dotted() -> None:
    assert (
        expanded_policy_id(
            "tenant-isolation", {"entity": "customers", "field": "tenant_id"}
        )
        == "tenant-isolation.customers.tenant_id"
    )
    assert (
        expanded_policy_id(
            "purpose-gating", {"purposes": ["billing", "audit"]}
        )
        == "purpose-gating.audit.billing"
    )
    # field-masking entries are entity.field references and therefore always
    # contain dots, so the identity uses the deterministic digest form.
    masking_id = expanded_policy_id(
        "field-masking", {"fields": ["customers.tenant_id", "orders.amount"]}
    )
    assert masking_id.startswith("field-masking.")
    assert len(masking_id) <= 128
    assert _IDENTIFIER_PATTERN.fullmatch(masking_id) is not None
    assert (
        masking_id
        == expanded_policy_id(
            "field-masking", {"fields": ["orders.amount", "customers.tenant_id"]}
        )
    )


def test_identity_is_injective_across_ambiguous_dot_splits() -> None:
    """Distinct targets that dot-join identically must get distinct ids."""
    first = expanded_policy_id("tenant-isolation", {"entity": "a", "field": "b.c"})
    second = expanded_policy_id("tenant-isolation", {"entity": "a.b", "field": "c"})
    assert first != second
    assert first.startswith("tenant-isolation.")
    assert second.startswith("tenant-isolation.")

    purposes_first = expanded_policy_id(
        "purpose-gating", {"purposes": ["a.b", "c"]}
    )
    purposes_second = expanded_policy_id(
        "purpose-gating", {"purposes": ["a", "b.c"]}
    )
    assert purposes_first != purposes_second


def test_identity_digest_fallback_matches_identifier_pattern() -> None:
    entity = "a" * 120
    field = "b" * 120
    policy_id = expanded_policy_id(
        "tenant-isolation", {"entity": entity, "field": field}
    )
    assert len(policy_id) <= 128
    assert _IDENTIFIER_PATTERN.fullmatch(policy_id) is not None
    assert policy_id.startswith("tenant-isolation.")
    assert expanded_policy_id("tenant-isolation", {"entity": entity, "field": field}) == policy_id


def test_value_parameter_change_preserves_identity() -> None:
    first = minimal_model(
        [
            {
                "template": "row-restriction",
                "parameters": {
                    "entity": "customers",
                    "field": "tenant_id",
                    "allowed_values": [1, 2],
                },
            }
        ]
    )
    second = minimal_model(
        [
            {
                "template": "row-restriction",
                "parameters": {
                    "entity": "customers",
                    "field": "tenant_id",
                    "allowed_values": [3, 4, 5],
                },
            }
        ]
    )
    first_expanded = expand_policy_templates(first)
    second_expanded = expand_policy_templates(second)
    assert [item.policy_id for item in first_expanded] == [
        item.policy_id for item in second_expanded
    ]
    assert [dict(item.payload) for item in first_expanded] != [
        dict(item.payload) for item in second_expanded
    ]


def test_expansion_is_ordered_by_expanded_identity() -> None:
    model = minimal_model(
        [
            {
                "template": "purpose-gating",
                "parameters": {"purposes": ["audit"], "effect": "deny"},
            },
            {
                "template": "tenant-isolation",
                "parameters": {
                    "entity": "customers",
                    "field": "tenant_id",
                    "claim": "tenant",
                },
            },
        ]
    )
    expanded = expand_policy_templates(model)
    assert [item.policy_id for item in expanded] == sorted(
        item.policy_id for item in expanded
    )
    assert [item.declaration_index for item in expanded] == [0, 1]


def test_unknown_template_fails_closed_without_partial_expansion() -> None:
    model = minimal_model(
        [
            {
                "template": "tenant-isolation",
                "parameters": {
                    "entity": "customers",
                    "field": "tenant_id",
                    "claim": "tenant",
                },
            },
            {"template": "custom-policy", "parameters": {}},
        ]
    )
    with pytest.raises(PolicyTemplateError) as error:
        expand_policy_templates(model)
    assert [issue.kind for issue in error.value.issues] == ["unknown_template"]


def test_unknown_missing_and_wrong_kind_parameters_fail_closed() -> None:
    model = minimal_model(
        [
            {
                "template": "purpose-gating",
                "parameters": {"purposes": ["audit"], "mode": "deny"},
            }
        ]
    )
    with pytest.raises(PolicyTemplateError) as error:
        expand_policy_templates(model)
    kinds = {issue.kind for issue in error.value.issues}
    assert kinds == {"unknown_parameter", "missing_parameter"}

    wrong_kind = minimal_model(
        [
            {
                "template": "purpose-gating",
                "parameters": {"purposes": ["audit"], "effect": "block"},
            }
        ]
    )
    with pytest.raises(PolicyTemplateError) as error:
        expand_policy_templates(wrong_kind)
    assert [issue.kind for issue in error.value.issues] == ["invalid_parameter"]


def test_registry_list_bounds_fail_closed() -> None:
    model = minimal_model(
        [
            {
                "template": "purpose-gating",
                "parameters": {"purposes": [f"p{index}" for index in range(20)], "effect": "deny"},
            }
        ]
    )
    with pytest.raises(PolicyTemplateError) as error:
        expand_policy_templates(model)
    assert [issue.kind for issue in error.value.issues] == ["parameter_bounds"]


def test_unresolved_targets_fail_closed() -> None:
    model = minimal_model(
        [
            {
                "template": "tenant-isolation",
                "parameters": {
                    "entity": "orders",
                    "field": "tenant_id",
                    "claim": "tenant",
                },
            }
        ]
    )
    with pytest.raises(PolicyTemplateError) as error:
        expand_policy_templates(model)
    assert [issue.kind for issue in error.value.issues] == ["invalid_reference"]

    masked = minimal_model(
        [
            {
                "template": "field-masking",
                "parameters": {"fields": ["customers.missing"], "replacement": "***"},
            }
        ]
    )
    with pytest.raises(PolicyTemplateError) as error:
        expand_policy_templates(masked)
    assert [issue.kind for issue in error.value.issues] == ["invalid_reference"]


def test_duplicate_expanded_identity_fails_closed() -> None:
    model = minimal_model(
        [
            {
                "template": "tenant-isolation",
                "parameters": {
                    "entity": "customers",
                    "field": "tenant_id",
                    "claim": "first",
                },
            },
            {
                "template": "tenant-isolation",
                "parameters": {
                    "entity": "customers",
                    "field": "tenant_id",
                    "claim": "second",
                },
            },
        ]
    )
    with pytest.raises(PolicyTemplateError) as error:
        expand_policy_templates(model)
    assert [issue.kind for issue in error.value.issues] == ["duplicate_identity"]
    assert error.value.issues[0].duplicate_index == 0


def test_model_rejects_unsafe_parameter_content() -> None:
    with pytest.raises(ValidationError):
        AuthoringPolicyTemplate.model_validate(
            {
                "template": "row-restriction",
                "parameters": {"payload": {"policy_id": "x"}},
            }
        )
    with pytest.raises(ValidationError):
        AuthoringPolicyTemplate.model_validate(
            {
                "template": "row-restriction",
                "parameters": {"digest": "sha256:" + "0" * 64},
            }
        )
    with pytest.raises(ValidationError):
        AuthoringPolicyTemplate.model_validate(
            {"template": "row-restriction", "parameters": {"status": "approved"}}
        )


def test_model_rejects_too_many_declarations() -> None:
    declaration = {
        "template": "purpose-gating",
        "parameters": {"purposes": ["audit"], "effect": "allow"},
    }
    spec: dict[str, object] = {
        "source": {"sourceId": "warehouse"},
        "entities": [
            {
                "entityId": "customers",
                "label": "Customers",
                "fields": [{"fieldId": "tenant_id", "label": "Tenant", "dataType": "int"}],
            }
        ],
        "policies": [declaration] * (MAX_POLICY_DECLARATIONS + 1),
    }
    with pytest.raises(ValidationError):
        SemanticAssemblyAuthoring.model_validate(
            {
                "apiVersion": "nl2data.io/semantic-assembly-authoring/v1alpha1",
                "kind": "SemanticAssembly",
                "metadata": {"bundleId": "sales", "modelVersion": "1.0.0"},
                "spec": spec,
            }
        )


def test_normalize_policy_parameters_bounds() -> None:
    assert normalize_policy_parameters({"claim": "tenant"}) == {"claim": "tenant"}
    assert normalize_policy_parameters({"values": (1, 2)}) == {"values": [1, 2]}
    with pytest.raises(ValueError):
        normalize_policy_parameters({"Bad Key": "x"})
    with pytest.raises(ValueError):
        normalize_policy_parameters({"claim": "x" * 2_048})
    with pytest.raises(ValueError):
        normalize_policy_parameters({"values": list(range(MAX_POLICY_LIST_ITEMS + 1))})


def test_model_rejects_too_many_parameter_entries() -> None:
    parameters = {f"param_{index}": "x" for index in range(MAX_POLICY_PARAM_ENTRIES + 1)}
    with pytest.raises(ValidationError):
        AuthoringPolicyTemplate.model_validate(
            {"template": "row-restriction", "parameters": parameters}
        )
