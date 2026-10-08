"""Bounded, fail-closed parsing of the supported Wren MDL subset."""

from __future__ import annotations

import json
from pathlib import Path

from nl2data_core.mdl_importer import parse_mdl

FIXTURES = Path(__file__).parents[1] / "fixtures" / "mdl_importer"


def _accepted() -> bytes:
    return (FIXTURES / "accepted_mdl.json").read_bytes()


def test_accepted_fixture_parses_cleanly() -> None:
    outcome = parse_mdl(_accepted())
    assert outcome.parsed
    assert outcome.document is not None
    assert outcome.document["schemaVersion"] == "1.1"


def test_invalid_json_reports_line_and_column_without_echo() -> None:
    outcome = parse_mdl(b'{"schemaVersion": "1.1", "models": [}')
    assert not outcome.parsed
    diagnostic = outcome.diagnostics[0]
    assert diagnostic.code == "invalid_input"
    assert diagnostic.mark is not None
    assert diagnostic.mark.line == 1
    assert diagnostic.mark.column > 1
    assert '"models"' not in diagnostic.message


def test_invalid_utf8_is_rejected() -> None:
    outcome = parse_mdl(b'{"schemaVersion": "\xff\xfe"}')
    assert not outcome.parsed
    assert outcome.diagnostics[0].code == "invalid_input"


def test_oversized_document_is_rejected() -> None:
    from nl2data_core.mdl_importer import MAX_MDL_BYTES

    outcome = parse_mdl(b" " * (MAX_MDL_BYTES + 1))
    assert not outcome.parsed
    assert outcome.diagnostics[0].code == "invalid_input"


def test_duplicate_keys_are_rejected() -> None:
    payload = b'{"schemaVersion": "1.1", "schemaVersion": "1.1", "models": []}'
    outcome = parse_mdl(payload)
    assert not outcome.parsed
    assert outcome.diagnostics[0].code == "invalid_input"
    assert "duplicate" in outcome.diagnostics[0].message


def test_over_deep_document_is_rejected() -> None:
    from nl2data_core.mdl_importer import MAX_MDL_DEPTH

    deep = {"schemaVersion": "1.1"}
    current = deep
    for _ in range(MAX_MDL_DEPTH + 1):
        current["nested"] = {}
        current = current["nested"]
    outcome = parse_mdl(json.dumps(deep).encode())
    assert not outcome.parsed
    assert outcome.diagnostics[0].code == "invalid_input"


def test_extreme_nesting_returns_diagnostic_instead_of_crashing() -> None:
    """Depth measurement is iterative and never raises on adversarial input."""
    payload = b'{"schemaVersion": "1.1", "models": ' + b"[" * 500 + b"]" * 500 + b"}"
    outcome = parse_mdl(payload)
    assert not outcome.parsed
    assert outcome.diagnostics[0].code == "invalid_input"


def test_unknown_schema_version_fails_closed() -> None:
    for version in (None, "1.0", "2.0", 1.1):
        document = {"schemaVersion": version, "models": [{"name": "A", "columns": []}]}
        outcome = parse_mdl(json.dumps(document).encode())
        assert not outcome.parsed
        assert outcome.diagnostics[0].code == "unsupported_version"


def test_unknown_top_level_member_fails_closed() -> None:
    document = {
        "schemaVersion": "1.1",
        "models": [{"name": "A", "columns": [{"name": "a", "type": "int"}]}],
        "cubes": [],
    }
    outcome = parse_mdl(json.dumps(document).encode())
    assert not outcome.parsed
    assert outcome.diagnostics[0].code == "unsupported_construct"


def test_non_empty_views_macros_enums_fail_closed() -> None:
    for member in ("views", "enumDefinitions", "macros"):
        document = {
            "schemaVersion": "1.1",
            "models": [{"name": "A", "columns": [{"name": "a", "type": "int"}]}],
            member: [{"name": "x"}],
        }
        outcome = parse_mdl(json.dumps(document).encode())
        assert not outcome.parsed
        assert outcome.diagnostics[0].code == "unsupported_construct"
        assert outcome.diagnostics[0].path == f"$.{member}"


def test_non_object_document_fails_closed() -> None:
    outcome = parse_mdl(b"[1, 2, 3]")
    assert not outcome.parsed
    assert outcome.diagnostics[0].code == "unsupported_construct"


def test_missing_or_mistyped_models_fail_closed() -> None:
    outcome = parse_mdl(b'{"schemaVersion": "1.1"}')
    assert not outcome.parsed
    assert outcome.diagnostics[0].code == "missing_mapping"
    assert outcome.diagnostics[0].path == "$.models"

    outcome = parse_mdl(b'{"schemaVersion": "1.1", "models": {"name": "A"}}')
    assert not outcome.parsed
    assert outcome.diagnostics[0].code == "unsupported_construct"


def test_metrics_envelope_allows_objects_for_semantic_checks() -> None:
    document = {
        "schemaVersion": "1.1",
        "models": [{"name": "A", "columns": [{"name": "a", "type": "int"}]}],
        "metrics": [{"name": "m"}],
    }
    outcome = parse_mdl(json.dumps(document).encode())
    assert outcome.parsed
