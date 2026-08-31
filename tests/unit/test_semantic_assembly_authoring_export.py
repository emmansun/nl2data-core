"""Tests for deterministic semantic-only authoring export."""

from __future__ import annotations

from tests.unit.test_semantic_assembly_authoring_lowering import full_document

from nl2data_core.assembly import AssemblyState
from nl2data_core.assembly.authoring import (
    SemanticAssemblyAuthoring,
    SemanticAssemblyAuthoringLoader,
    export_authoring,
    export_authoring_draft,
    lower_authoring,
)


def assertion_facts(model: SemanticAssemblyAuthoring) -> list[tuple[str, str]]:
    result = lower_authoring(model, draft_id="draft", author_reference="author")
    assert result.draft is not None
    return [(item.id, item.payload_hash()) for item in result.draft.assertions]


def test_export_is_repeatable_block_yaml_without_aliases_or_lifecycle_fields() -> None:
    model = SemanticAssemblyAuthoring.model_validate(full_document())
    first = export_authoring(model)
    second = export_authoring(model)
    assert first.document == second.document
    assert first.document is not None
    assert "&id" not in first.document and "*id" not in first.document
    for forbidden in (
        "assertionId",
        "provenance",
        "reviewState",
        "draftRevision",
        "approvedBy",
        "author_reference",
        "fingerprint:",
    ):
        assert forbidden not in first.document


def test_export_parse_lower_round_trip_preserves_payload_hashes() -> None:
    model = SemanticAssemblyAuthoring.model_validate(full_document())
    exported = export_authoring(model)
    assert exported.document is not None
    parsed = SemanticAssemblyAuthoringLoader().load(exported.document)
    assert parsed.model is not None
    assert assertion_facts(parsed.model) == assertion_facts(model)


def test_ambiguous_strings_remain_strings() -> None:
    payload = full_document()
    spec = payload["spec"]
    assert isinstance(spec, dict)
    source = spec["source"]
    assert isinstance(source, dict)
    source["sourceId"] = "on"
    for reference in spec["sourceReferences"]:  # type: ignore[union-attr]
        reference["sourceId"] = "on"
    for binding in spec["deploymentBindings"]:  # type: ignore[union-attr]
        binding["sourceId"] = "on"
    model = SemanticAssemblyAuthoring.model_validate(payload)
    exported = export_authoring(model)
    assert exported.document is not None and '"on"' in exported.document
    parsed = SemanticAssemblyAuthoringLoader().load(exported.document)
    assert parsed.model is not None and parsed.model.spec.source.source_id == "on"


def test_clean_authoring_draft_exports_and_reviewed_or_unknown_shape_fails() -> None:
    model = SemanticAssemblyAuthoring.model_validate(full_document())
    lowered = lower_authoring(model, draft_id="draft", author_reference="trusted")
    assert lowered.draft is not None
    exported = export_authoring_draft(lowered.draft)
    assert exported.exported
    assert exported.document is not None
    parsed = SemanticAssemblyAuthoringLoader().load(exported.document)
    assert parsed.model is not None
    assert parsed.model.spec.source.catalog_fingerprint == model.spec.source.catalog_fingerprint
    reviewed = lowered.draft.model_copy(update={"state": AssemblyState.REVIEW, "draft_revision": 1})
    rejected = export_authoring_draft(reviewed)
    assert not rejected.exported
    assert rejected.diagnostics[0].code == "unsupported_export"
