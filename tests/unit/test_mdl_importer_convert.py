"""Deterministic MDL-to-authoring conversion and revalidation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nl2data_core.mdl_importer import import_wren_mdl, parse_mdl

FIXTURES = Path(__file__).parents[1] / "fixtures" / "mdl_importer"

_IDENTITIES = {
    "source_id": "warehouse",
    "bundle_id": "sales",
    "model_version": "1.0.0",
}


def _accepted_payload() -> bytes:
    return (FIXTURES / "accepted_mdl.json").read_bytes()


def _import(payload: bytes, manifest: bytes | None = None):
    return import_wren_mdl(payload, **_IDENTITIES, manifest_payload=manifest)


def test_accepted_fixture_imports_validated_yaml() -> None:
    result = _import(_accepted_payload())
    assert result.successful, [d.code for d in result.diagnostics]
    assert result.document is not None
    assert "entityId" in result.document
    # Physical provenance is never copied into the semantic output.
    assert "refSql" not in result.document
    assert "tableReference" not in result.document
    assert "o_order_id" not in result.document
    assert "sales.customers" not in result.document


def test_every_documented_mapping_is_applied_deterministically() -> None:
    result = _import(_accepted_payload())
    assert result.document is not None
    for expected in (
        '"entityId": "Customers"',
        '"entityId": "Orders"',
        '"fieldId": "credit_limit"',
        '"fieldId": "custkey"',
        '"dataType": "int"',
        '"dataType": "str"',
        '"relationshipId": "OrdersCustomers"',
        '"targetEntityId": "Customers"',
        '"name": "net_amount"',
        '"op": "sub"',
    ):
        assert expected in result.document, expected


def test_calculated_expression_maps_to_governed_tree() -> None:
    result = _import(_accepted_payload())
    assert result.document is not None
    # requires entries exactly match the expression field leaves.
    requires_index = result.document.index('"requires"')
    section = result.document[requires_index : requires_index + 120]
    assert '"discount"' in section and '"gross_amount"' in section


def test_failed_import_never_returns_a_partial_document() -> None:
    document = {
        "schemaVersion": "1.1",
        "models": [
            {"name": "A", "columns": [{"name": "a", "type": "int"}]},
            {"name": "B", "columns": [{"name": "b", "type": "int", "isCalculated": True}]},
        ],
    }
    result = _import(json.dumps(document).encode())
    assert not result.successful
    assert result.document is None
    assert result.diagnostics
    assert all(d.code in _STABLE_CODES for d in result.diagnostics)


_STABLE_CODES = {
    "invalid_input",
    "unsupported_version",
    "unsupported_construct",
    "missing_mapping",
    "ambiguous_reference",
    "identifier_collision",
    "unsupported_expression",
    "invalid_manifest",
    "authoring_validation_failed",
    "output_exists",
}


def test_executable_sql_in_plain_column_is_rejected_without_echo() -> None:
    document = {
        "schemaVersion": "1.1",
        "models": [
            {
                "name": "A",
                "columns": [{"name": "a", "type": "int", "expression": "sum(x) over ()"}],
            }
        ],
    }
    result = _import(json.dumps(document).encode())
    assert not result.successful
    diagnostic = result.diagnostics[0]
    assert diagnostic.code == "unsupported_construct"
    assert diagnostic.path == "$.models[0].columns[0].expression"
    assert "sum(x)" not in diagnostic.message


def test_relationship_column_sql_expression_is_rejected_without_echo() -> None:
    document = {
        "schemaVersion": "1.1",
        "models": [
            {"name": "A", "columns": [{"name": "a", "type": "bigint"}]},
            {
                "name": "B",
                "columns": [
                    {"name": "b", "type": "bigint"},
                    {
                        "name": "nav",
                        "type": "A",
                        "relationship": "AB",
                        "expression": "DROP TABLE users",
                    },
                ],
            },
        ],
        "relationships": [
            {
                "name": "AB",
                "models": ["B", "A"],
                "joinType": "MANY_TO_ONE",
                "condition": "B.b = A.a",
            }
        ],
    }
    result = _import(json.dumps(document).encode())
    assert not result.successful
    diagnostic = result.diagnostics[0]
    assert diagnostic.code == "unsupported_construct"
    assert diagnostic.path == "$.models[1].columns[1].expression"
    assert "DROP" not in diagnostic.message
    assert result.document is None


def test_duplicate_model_names_report_identifier_collision() -> None:
    document = {
        "schemaVersion": "1.1",
        "models": [
            {"name": "A", "columns": [{"name": "a", "type": "bigint"}]},
            {"name": "A", "columns": [{"name": "b", "type": "bigint"}]},
        ],
    }
    result = _import(json.dumps(document).encode())
    assert not result.successful
    assert any(d.code == "identifier_collision" for d in result.diagnostics)
    assert result.document is None


def test_duplicate_field_names_report_identifier_collision() -> None:
    document = {
        "schemaVersion": "1.1",
        "models": [
            {
                "name": "A",
                "columns": [
                    {"name": "x", "type": "bigint"},
                    {"name": "x", "type": "varchar"},
                ],
            }
        ],
    }
    result = _import(json.dumps(document).encode())
    assert not result.successful
    assert any(d.code == "identifier_collision" for d in result.diagnostics)
    assert result.document is None


def test_duplicate_metric_names_report_identifier_collision() -> None:
    document = {
        "schemaVersion": "1.1",
        "models": [{"name": "Orders", "columns": [{"name": "amount", "type": "bigint"}]}],
        "metrics": [
            {"name": "revenue", "measures": [{"name": "m", "expression": "sum(Orders.amount)"}]},
            {"name": "revenue", "measures": [{"name": "m", "expression": "sum(Orders.amount)"}]},
        ],
    }
    result = _import(json.dumps(document).encode())
    assert not result.successful
    assert any(d.code == "identifier_collision" for d in result.diagnostics)
    assert result.document is None


def test_deeply_nested_calculated_expression_is_rejected_not_crashed() -> None:
    expression = "(" * 400 + "a" + ")" * 400
    document = {
        "schemaVersion": "1.1",
        "models": [
            {
                "name": "A",
                "columns": [
                    {"name": "a", "type": "bigint"},
                    {"name": "b", "type": "bigint", "isCalculated": True,
                     "expression": expression},
                ],
            }
        ],
    }
    result = _import(json.dumps(document).encode())
    assert not result.successful
    assert any(d.code == "unsupported_expression" for d in result.diagnostics)
    assert result.document is None


def test_oversized_flat_calculated_expression_is_rejected_not_crashed() -> None:
    expression = " + ".join(["a"] * 40)
    document = {
        "schemaVersion": "1.1",
        "models": [
            {
                "name": "A",
                "columns": [
                    {"name": "a", "type": "bigint"},
                    {"name": "b", "type": "bigint", "isCalculated": True,
                     "expression": expression},
                ],
            }
        ],
    }
    result = _import(json.dumps(document).encode())
    assert not result.successful
    assert any(d.code == "unsupported_expression" for d in result.diagnostics)
    assert result.document is None


def test_expression_diagnostics_carry_column_path() -> None:
    document = {
        "schemaVersion": "1.1",
        "models": [
            {
                "name": "A",
                "columns": [
                    {"name": "a", "type": "bigint"},
                    {
                        "name": "b",
                        "type": "bigint",
                        "isCalculated": True,
                        "expression": "missing + 1",
                    },
                ],
            }
        ],
    }
    result = _import(json.dumps(document).encode())
    assert not result.successful
    diagnostic = next(d for d in result.diagnostics if d.code == "missing_mapping")
    assert diagnostic.path == "$.models[0].columns[1].expression"


def test_dotted_calculated_expression_is_rejected() -> None:
    document = {
        "schemaVersion": "1.1",
        "models": [
            {
                "name": "A",
                "columns": [
                    {"name": "a", "type": "bigint"},
                    {
                        "name": "b",
                        "type": "bigint",
                        "isCalculated": True,
                        "expression": "C.a * 2",
                    },
                ],
            }
        ],
    }
    result = _import(json.dumps(document).encode())
    codes = [d.code for d in result.diagnostics]
    assert "unsupported_expression" in codes


def test_float_const_and_unknown_syntax_are_rejected() -> None:
    base = {
        "schemaVersion": "1.1",
        "models": [
            {
                "name": "A",
                "columns": [
                    {"name": "a", "type": "bigint"},
                    {"name": "b", "type": "bigint", "isCalculated": True,
                     "expression": "{expr}"},
                ],
            }
        ],
    }
    for expression in ("a * 1.5", "a +", "()", "a ? b"):
        base["models"][0]["columns"][1]["expression"] = expression  # type: ignore[index]
        result = _import(json.dumps(base).encode())
        assert not result.successful, expression
        assert any(d.code == "unsupported_expression" for d in result.diagnostics)


def test_many_to_many_join_type_is_rejected() -> None:
    document = {
        "schemaVersion": "1.1",
        "models": [
            {"name": "A", "columns": [{"name": "a", "type": "bigint"}]},
            {"name": "B", "columns": [{"name": "b", "type": "bigint"}]},
        ],
        "relationships": [
            {
                "name": "AB",
                "models": ["A", "B"],
                "joinType": "MANY_TO_MANY",
                "condition": "A.a = B.b",
            }
        ],
    }
    result = _import(json.dumps(document).encode())
    assert any(d.code == "unsupported_construct" for d in result.diagnostics)


def test_relationship_with_unresolvable_column_fails() -> None:
    document = {
        "schemaVersion": "1.1",
        "models": [
            {"name": "A", "columns": [{"name": "a", "type": "bigint"}]},
            {"name": "B", "columns": [{"name": "b", "type": "bigint"}]},
        ],
        "relationships": [
            {
                "name": "AB",
                "models": ["A", "B"],
                "joinType": "MANY_TO_ONE",
                "condition": "A.missing = B.b",
            }
        ],
    }
    result = _import(json.dumps(document).encode())
    assert any(d.code == "missing_mapping" for d in result.diagnostics)


def test_field_collision_requires_manifest_override() -> None:
    colliding = (FIXTURES / "colliding_field_mdl.json").read_bytes()
    manifest = (FIXTURES / "colliding_field_manifest.json").read_bytes()

    without = _import(colliding)
    assert not without.successful
    assert any(d.code == "identifier_collision" for d in without.diagnostics)

    with_override = _import(colliding, manifest)
    assert with_override.successful, [d.code for d in with_override.diagnostics]
    assert with_override.document is not None
    assert '"fieldId": "order_customer_id"' in with_override.document


def test_over_long_names_require_overrides() -> None:
    name = "!" * 200
    document = {
        "schemaVersion": "1.1",
        "models": [
            {
                "name": name,
                "columns": [{"name": "order id", "type": "bigint"}],
            }
        ],
    }
    result = _import(json.dumps(document).encode())
    assert not result.successful
    codes = {d.code for d in result.diagnostics}
    assert "missing_mapping" in codes

    manifest = json.dumps(
        {
            "version": "1",
            "entities": {name: "orders"},
            "fields": {f"{name}.order id": "order_id"},
        }
    ).encode()
    result = _import(json.dumps(document).encode(), manifest)
    assert result.successful, [d.code for d in result.diagnostics]
    assert result.document is not None
    assert '"fieldId": "order_id"' in result.document


def test_invalid_manifest_is_rejected_with_stable_code() -> None:
    payload = _accepted_payload()
    for manifest in (
        b"not json",
        b'{"version": "2"}',
        b'{"version": "1", "entities": {"A": "1bad identifier!"}}',
        b'{"version": "1", "unknown": {}}',
        b'{"version": "1", "version": "1", "entities": {}}',
    ):
        result = _import(payload, manifest)
        assert not result.successful
        assert any(d.code == "invalid_manifest" for d in result.diagnostics)


def test_physical_deployment_and_lifecycle_content_fails_closed() -> None:
    document = {
        "schemaVersion": "1.1",
        "models": [
            {
                "name": "A",
                "columns": [{"name": "a", "type": "bigint"}],
                "properties": {"connectionUrl": "postgres://user:secret@host/db"},
            }
        ],
    }
    result = _import(json.dumps(document).encode())
    assert not result.successful
    diagnostic = result.diagnostics[0]
    assert diagnostic.code == "unsupported_construct"
    assert "secret" not in diagnostic.message
    assert "postgres" not in diagnostic.message
    assert result.document is None


def test_equivalent_imports_are_byte_deterministic() -> None:
    payload = _accepted_payload()
    first = _import(payload)
    second = _import(payload)
    assert first.document == second.document


def test_output_order_is_independent_of_json_presentation_order() -> None:
    payload = json.loads(_accepted_payload())
    payload["models"].reverse()
    for model in payload["models"]:
        model["columns"].reverse()
    reordered = json.dumps(payload).encode()

    baseline = _import(_accepted_payload())
    shuffled = _import(reordered)
    assert baseline.successful and shuffled.successful
    assert baseline.document == shuffled.document

    # Diagnostics are equally order-independent.
    broken = json.loads(_accepted_payload())
    broken["relationships"] = [
        {
            "name": "R1",
            "models": ["Orders", "Customers"],
            "joinType": "MANY_TO_MANY",
            "condition": "Orders.custkey = Customers.customer_id",
        },
        {
            "name": "R2",
            "models": ["Missing", "Customers"],
            "joinType": "MANY_TO_ONE",
            "condition": "Missing.custkey = Customers.customer_id",
        },
    ]
    result = _import(json.dumps(broken).encode())
    assert result.diagnostics == tuple(
        sorted(result.diagnostics, key=lambda d: (d.path, d.code, d.message))
    )


def test_metric_with_single_simple_measure_maps_to_authoring_measure() -> None:
    document = {
        "schemaVersion": "1.1",
        "models": [
            {
                "name": "Orders",
                "columns": [{"name": "amount", "type": "bigint", "expression": "o_amount"}],
            }
        ],
        "metrics": [
            {
                "name": "revenue",
                "measures": [{"name": "revenue", "expression": "sum(Orders.amount)"}],
            }
        ],
    }
    result = _import(json.dumps(document).encode())
    assert result.successful, [d.code for d in result.diagnostics]
    assert result.document is not None
    assert '"measureId": "revenue"' in result.document
    assert '"aggregation": "sum"' in result.document


def test_unrepresentable_metric_shapes_are_rejected() -> None:
    base = {
        "schemaVersion": "1.1",
        "models": [
            {"name": "Orders", "columns": [{"name": "amount", "type": "bigint"}]}
        ],
    }
    cases = [
        {"name": "m", "measures": [{"name": "m", "expression": "sum(amount)"}]},
        {"name": "m", "measures": [{"name": "m", "expression": "count(*)"}]},
        {
            "name": "m",
            "dimensions": ["region"],
            "measures": [{"name": "m", "expression": "sum(Orders.amount)"}],
        },
        {"name": "m", "measures": []},
        {
            "name": "m",
            "measures": [{"name": "m", "expression": "sum(Orders.missing)"}],
        },
    ]
    for metric in cases:
        base["metrics"] = [metric]
        result = _import(json.dumps(base).encode())
        assert not result.successful, metric
        assert result.document is None
        assert all(d.code in _STABLE_CODES for d in result.diagnostics)


def test_relationship_column_is_not_emitted_and_must_resolve() -> None:
    payload = json.loads(_accepted_payload())
    payload["models"][1]["columns"][-1]["relationship"] = "NoSuchRelationship"
    result = _import(json.dumps(payload).encode())
    assert not result.successful
    assert any(d.code == "missing_mapping" for d in result.diagnostics)


def test_primary_key_must_resolve_to_a_plain_column() -> None:
    payload = json.loads(_accepted_payload())
    payload["models"][0]["primaryKey"] = "not_a_column"
    result = _import(json.dumps(payload).encode())
    assert not result.successful
    assert any(d.code == "missing_mapping" for d in result.diagnostics)


def test_diagnostic_bounds_and_value_safety() -> None:
    from nl2data_core.mdl_importer import MAX_MDL_DIAGNOSTICS

    models = []
    for index in range(MAX_MDL_DIAGNOSTICS + 10):
        models.append(
            {
                "name": f"model{index}",
                "bogusMember": True,
                "columns": [{"name": f"col{index}", "type": "bigint"}],
            }
        )
    document = {"schemaVersion": "1.1", "models": models}
    result = _import(json.dumps(document).encode())
    assert len(result.diagnostics) == MAX_MDL_DIAGNOSTICS
    assert result.truncated
    assert result.document is None
    assert all("bogusMember" not in d.message for d in result.diagnostics)


def test_invalid_identity_arguments_fail_with_stable_code() -> None:
    result = import_wren_mdl(
        _accepted_payload(),
        source_id="bad source!",
        bundle_id="sales",
        model_version="1.0.0",
    )
    assert not result.successful
    assert result.diagnostics[0].code == "missing_mapping"
    assert result.document is None


def test_parser_and_conversion_round_trip_through_loader() -> None:
    from nl2data_core.assembly.authoring import SemanticAssemblyAuthoringLoader

    result = _import(_accepted_payload())
    assert result.document is not None
    reparsed = SemanticAssemblyAuthoringLoader().load(result.document)
    assert reparsed.loaded, [d.message for d in reparsed.diagnostics]


@pytest.mark.parametrize(
    ("expression", "output"),
    [
        ("a + b", "int"),
        ("a - b", "int"),
        ("a * b", "int"),
        ("a / b", "float"),
        ("(a + b) * 2", "int"),
    ],
)
def test_output_type_inference_matches_governed_rules(expression: str, output: str) -> None:
    document = {
        "schemaVersion": "1.1",
        "models": [
            {
                "name": "A",
                "columns": [
                    {"name": "a", "type": "bigint"},
                    {"name": "b", "type": "bigint"},
                    {
                        "name": "c",
                        "type": "double",
                        "isCalculated": True,
                        "expression": expression,
                    },
                ],
            }
        ],
    }
    result = _import(json.dumps(document).encode())
    assert result.successful, [d.code for d in result.diagnostics]
    assert result.document is not None
    assert f'"outputType": "{output}"' in result.document


def test_parse_outcome_reports_truncation_free_success() -> None:
    outcome = parse_mdl(_accepted_payload())
    assert not outcome.truncated
    assert outcome.parsed
