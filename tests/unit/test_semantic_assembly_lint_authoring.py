"""Authoring lint tests: stable diagnostics and source-located paths."""

from __future__ import annotations

from typing import Any

import pytest
import yaml

from nl2data_core.assembly.authoring import (
    AUTHORING_API_VERSION,
    SemanticAssemblyAuthoring,
    SemanticAssemblyAuthoringLoader,
    export_authoring,
    validate_authoring,
)
from nl2data_core.assembly.lint import (
    LINT_PROFILES,
    LintProfileId,
    lint_authoring,
)

API_VERSION = AUTHORING_API_VERSION


def _document(order: int = 0) -> dict[str, Any]:
    """A valid document with ambiguity, weak descriptions, and PII exposure."""
    email_semantics = {
        "value_mapping": {"jane@example.com": "customer email"},
        "pii": True,
        "sample_values": ["jane@example.com"],
    }
    orders_fields = [
        {
            "fieldId": "amount",
            "label": "Amount",
            "description": "Gross order amount in US dollars.",
            "dataType": "int",
            "allowedAggregations": ["sum"],
        },
        {
            "fieldId": "email",
            "label": "Email address",
            "description": "short",
            "dataType": "string",
            "allowedAggregations": [],
            "valueSemantics": email_semantics,
        },
    ]
    if order == 0:
        first, second = orders_fields
    else:
        second, first = orders_fields
    return {
        "apiVersion": API_VERSION,
        "kind": "SemanticAssembly",
        "metadata": {"bundleId": "sales", "modelVersion": "1.0.0"},
        "spec": {
            "source": {"sourceId": "warehouse"},
            "entities": [
                {
                    "entityId": "orders",
                    "label": "Orders",
                    "description": "Confirmed customer orders.",
                    "fields": [first, second],
                },
                {
                    "entityId": "customers",
                    "label": "Orders",
                    "description": "Registered customer accounts.",
                    "fields": [],
                },
            ],
            "measures": [],
            "grains": [],
        },
    }


def _parsed_model(payload: dict[str, Any]) -> SemanticAssemblyAuthoring:
    model = SemanticAssemblyAuthoring.model_validate(payload)
    result = validate_authoring(model)
    assert result.valid
    assert result.model is not None
    return result.model


def _lint_codes(result: Any) -> list[str]:
    return [diagnostic.code for diagnostic in result.diagnostics]


class TestAuthoringLintStability:
    def test_identical_input_is_byte_deterministic(self) -> None:
        model = _parsed_model(_document())
        first = lint_authoring(model, profile=LintProfileId.PRODUCTION)
        second = lint_authoring(model, profile=LintProfileId.PRODUCTION)
        assert first.model_dump(mode="json") == second.model_dump(mode="json")

    def test_mapping_insertion_order_does_not_change_output(self) -> None:
        model_a = _parsed_model(_document(order=0))
        model_b = _parsed_model(_document(order=1))
        result_a = lint_authoring(model_a, profile=LintProfileId.PRODUCTION)
        result_b = lint_authoring(model_b, profile=LintProfileId.PRODUCTION)
        assert result_a.model_dump(mode="json") == result_b.model_dump(mode="json")

    def test_equivalent_yaml_presentation_keeps_semantic_paths(self) -> None:
        document = _document()
        plain = yaml.dump(document, sort_keys=False, default_flow_style=False)
        sorted_keys = yaml.dump(document, sort_keys=True, default_flow_style=False)
        assert plain != sorted_keys
        parsed_plain = SemanticAssemblyAuthoringLoader().load(plain)
        parsed_sorted = SemanticAssemblyAuthoringLoader().load(sorted_keys)
        assert parsed_plain.loaded and parsed_sorted.loaded
        result_plain = lint_authoring(
            parsed_plain.model,
            profile=LintProfileId.PRODUCTION,
            source_marks={entry.path.parts: entry.mark for entry in parsed_plain.source_marks},
        )
        result_sorted = lint_authoring(
            parsed_sorted.model,
            profile=LintProfileId.PRODUCTION,
            source_marks={entry.path.parts: entry.mark for entry in parsed_sorted.source_marks},
        )
        without_marks_a = [
            diagnostic.model_dump(mode="json", exclude={"source_location"})
            for diagnostic in result_plain.diagnostics
        ]
        without_marks_b = [
            diagnostic.model_dump(mode="json", exclude={"source_location"})
            for diagnostic in result_sorted.diagnostics
        ]
        assert without_marks_a == without_marks_b


class TestAuthoringLintSourceLocations:
    def test_diagnostics_are_source_located_after_parse_and_validation(self) -> None:
        payload = yaml.dump(_document(), sort_keys=False)
        parsed = SemanticAssemblyAuthoringLoader().load(payload)
        assert parsed.loaded and parsed.model is not None
        validation = validate_authoring(parsed.model)
        assert validation.valid
        marks = {entry.path.parts: entry.mark for entry in parsed.source_marks}
        result = lint_authoring(
            parsed.model,
            profile=LintProfileId.PRODUCTION,
            source_marks=marks,
        )
        located = [
            diagnostic
            for diagnostic in result.diagnostics
            if diagnostic.source_location is not None
        ]
        assert located, "expected source-backed diagnostics"
        for diagnostic in located:
            assert diagnostic.target_path.render().startswith("$.spec.")

    def test_entity_diagnostics_are_located_at_their_own_position(self) -> None:
        payload = {
            "apiVersion": API_VERSION,
            "kind": "SemanticAssembly",
            "metadata": {"bundleId": "sales", "modelVersion": "1.0.0"},
            "spec": {
                "source": {"sourceId": "warehouse"},
                "entities": [
                    {
                        "entityId": "orders",
                        "label": "Orders",
                        "description": "short",
                        "fields": [],
                    }
                ],
                "measures": [],
                "grains": [],
            },
        }
        document = yaml.dump(payload, sort_keys=False)
        entity_line = next(
            index + 1
            for index, line in enumerate(document.splitlines())
            if line.strip().endswith("entityId: orders")
        )
        parsed = SemanticAssemblyAuthoringLoader().load(document)
        assert parsed.loaded and parsed.model is not None
        marks = {entry.path.parts: entry.mark for entry in parsed.source_marks}
        result = lint_authoring(
            parsed.model, profile=LintProfileId.PRODUCTION, source_marks=marks
        )
        entity_diagnostic = next(
            diagnostic
            for diagnostic in result.diagnostics
            if diagnostic.target_path.render() == "$.spec.entities.orders"
        )
        assert entity_diagnostic.source_location is not None
        assert entity_diagnostic.source_location.line == entity_line

    def test_diagnostics_without_marks_omit_source_locations(self) -> None:
        model = _parsed_model(_document())
        result = lint_authoring(model, profile=LintProfileId.PRODUCTION)
        assert result.diagnostics
        assert all(
            diagnostic.source_location is None for diagnostic in result.diagnostics
        )


class TestAuthoringLintCatalog:
    def test_duplicate_labels_are_reported_with_references(self) -> None:
        model = _parsed_model(_document())
        result = lint_authoring(model, profile=LintProfileId.RECOMMENDED)
        duplicates = [
            diagnostic
            for diagnostic in result.diagnostics
            if diagnostic.code == "SAL001"
        ]
        assert len(duplicates) == 1
        assert "Orders" in duplicates[0].message
        assert len(duplicates[0].references) == 2

    def test_pii_exposure_is_error_only_in_production(self) -> None:
        model = _parsed_model(_document())
        recommended = lint_authoring(model, profile=LintProfileId.RECOMMENDED)
        production = lint_authoring(model, profile=LintProfileId.PRODUCTION)
        assert "SAL005" in _lint_codes(recommended)
        assert recommended.summary.blocking is False
        assert "SAL005" in _lint_codes(production)
        assert production.summary.blocking is True

    def test_compatibility_profile_runs_only_catalog_rules(self) -> None:
        model = _parsed_model(_document())
        result = lint_authoring(model, profile=LintProfileId.COMPATIBILITY)
        assert set(_lint_codes(result)) <= {"SAL001", "SAL007"}

    def test_missing_verification_plan_is_reported_in_production(self) -> None:
        model = _parsed_model(_document())
        result = lint_authoring(model, profile=LintProfileId.PRODUCTION)
        readiness = [
            diagnostic
            for diagnostic in result.diagnostics
            if diagnostic.code == "SAL010"
        ]
        assert len(readiness) == 1
        assert readiness[0].severity.name == "ERROR"

    def test_messages_never_leak_sample_values(self) -> None:
        model = _parsed_model(_document())
        result = lint_authoring(model, profile=LintProfileId.PRODUCTION)
        for diagnostic in result.diagnostics:
            assert "jane@example.com" not in diagnostic.message


class TestAuthoringLintRoundTrip:
    def test_parse_export_parse_round_trip_is_diagnostic_stable(self) -> None:
        model = _parsed_model(_document())
        exported = export_authoring(model)
        assert exported.exported and exported.document is not None
        reparsed = SemanticAssemblyAuthoringLoader().load(exported.document)
        assert reparsed.loaded and reparsed.model is not None
        first = lint_authoring(model, profile=LintProfileId.PRODUCTION)
        second = lint_authoring(reparsed.model, profile=LintProfileId.PRODUCTION)
        assert first.model_dump(mode="json") == second.model_dump(mode="json")

    def test_round_trip_target_paths_are_stable(self) -> None:
        model = _parsed_model(_document())
        exported = export_authoring(model)
        assert exported.document is not None
        reparsed = SemanticAssemblyAuthoringLoader().load(exported.document)
        assert reparsed.model is not None
        first = lint_authoring(model, profile=LintProfileId.PRODUCTION)
        second = lint_authoring(reparsed.model, profile=LintProfileId.PRODUCTION)
        paths_first = [diagnostic.target_path.render() for diagnostic in first.diagnostics]
        paths_second = [
            diagnostic.target_path.render() for diagnostic in second.diagnostics
        ]
        assert paths_first == paths_second
        assert paths_first


@pytest.mark.parametrize(
    ("profile", "rule", "expected"),
    [
        (LintProfileId.COMPATIBILITY, "SAL004", None),
        (LintProfileId.RECOMMENDED, "SAL004", "warning"),
        (LintProfileId.PRODUCTION, "SAL004", "error"),
    ],
)
def test_profile_severity_table_is_honored(
    profile: LintProfileId, rule: str, expected: str | None
) -> None:
    severity = LINT_PROFILES[profile].severity_for(rule)
    if expected is None:
        assert severity is None
    else:
        assert severity is not None and severity.value == expected
