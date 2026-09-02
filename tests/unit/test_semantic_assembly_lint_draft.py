"""Draft lint tests: revision stability, no source marks, no mutation."""

from __future__ import annotations

from typing import Any

import pytest

from nl2data_core.assembly import (
    ASSEMBLY_API_VERSION,
    AssemblyDraft,
    AssertionProvenance,
    AssertionProvenanceKind,
    AssertionType,
    ReviewState,
    SemanticAssertion,
)
from nl2data_core.assembly.lint import LintProfileId, lint_draft

_PROVENANCE = AssertionProvenance(kind=AssertionProvenanceKind.MANUAL)


def _assertion(assertion_type: AssertionType, payload: dict[str, Any]) -> SemanticAssertion:
    return SemanticAssertion.create(
        type=assertion_type,
        payload=payload,
        provenance=_PROVENANCE,
    )


def _draft() -> AssemblyDraft:
    return AssemblyDraft(
        apiVersion=ASSEMBLY_API_VERSION,
        draft_id="draft-1",
        bundle_id="sales",
        source_id="sales",
        model_version="1.0.0",
        assertions=(
            _assertion(
                AssertionType.ENTITY,
                {
                    "descriptor_id": "sales",
                    "entity_id": "orders",
                    "label": "Orders",
                    "description": "Confirmed customer orders.",
                },
            ),
            _assertion(
                AssertionType.FIELD,
                {
                    "descriptor_id": "sales",
                    "entity_id": "orders",
                    "field_id": "email",
                    "label": "Email address",
                    "description": "short",
                    "data_type": "string",
                    "allowed_aggregations": [],
                    "value_semantics": {
                        "pii": True,
                        "sample_values": ["jane@example.com"],
                    },
                },
            ),
        ),
        author_reference="author-1",
    )


class TestDraftLintPaths:
    def test_diagnostics_use_stable_assertion_paths_without_marks(self) -> None:
        result = lint_draft(_draft(), profile=LintProfileId.PRODUCTION)
        assert result.diagnostics
        for diagnostic in result.diagnostics:
            assert diagnostic.source_location is None
            rendered = diagnostic.target_path.render()
            assert rendered == "$" or rendered.startswith("$.")
            assert "assertions" in rendered or rendered in {"$", "$.sourceSnapshotFingerprint"}

    def test_pii_exposure_is_actionable_without_source_marks(self) -> None:
        result = lint_draft(_draft(), profile=LintProfileId.PRODUCTION)
        pii = [item for item in result.diagnostics if item.code == "SAL005"]
        assert len(pii) == 1
        assert pii[0].target_path.render().startswith("$.assertions.")

    def test_missing_source_fingerprint_is_reported_at_draft_root(self) -> None:
        result = lint_draft(_draft(), profile=LintProfileId.PRODUCTION)
        hints = [item for item in result.diagnostics if item.code == "SAL006"]
        assert hints
        assert "$.sourceSnapshotFingerprint" in {
            item.target_path.render() for item in hints
        }

    def test_missing_verification_plan_is_reported_at_root_path(self) -> None:
        result = lint_draft(_draft(), profile=LintProfileId.PRODUCTION)
        readiness = [item for item in result.diagnostics if item.code == "SAL010"]
        assert len(readiness) == 1
        assert readiness[0].target_path.render() == "$"


class TestDraftLintStabilityAndPurity:
    def test_lint_is_deterministic_per_draft_revision(self) -> None:
        draft = _draft()
        first = lint_draft(draft, profile=LintProfileId.PRODUCTION)
        second = lint_draft(draft, profile=LintProfileId.PRODUCTION)
        assert first.model_dump(mode="json") == second.model_dump(mode="json")

    def test_lint_is_revision_stable(self) -> None:
        draft = _draft()
        bumped = draft.model_copy(update={"draft_revision": draft.draft_revision + 1})
        first = lint_draft(draft, profile=LintProfileId.PRODUCTION)
        second = lint_draft(bumped, profile=LintProfileId.PRODUCTION)
        assert first.model_dump(mode="json") == second.model_dump(mode="json")

    def test_lint_does_not_mutate_draft_state(self) -> None:
        draft = _draft()
        before = draft.model_dump(mode="python")
        lint_draft(draft, profile=LintProfileId.PRODUCTION)
        assert draft.model_dump(mode="python") == before
        assert draft.draft_revision == 0
        assert all(
            assertion.review_state is ReviewState.PENDING
            and assertion.review_binding is None
            for assertion in draft.assertions
        )
        assert draft.approved_by is None
        assert draft.approved_verification_plan_fingerprint is None

    def test_lint_output_is_bounded_and_safe(self) -> None:
        result = lint_draft(_draft(), profile=LintProfileId.PRODUCTION)
        assert "jane@example.com" not in str(
            [item.message for item in result.diagnostics]
        )
        assert result.summary.diagnostic_count == len(result.diagnostics)


@pytest.mark.parametrize("profile", list(LintProfileId))
def test_draft_lint_never_blocks_outside_production(profile: LintProfileId) -> None:
    result = lint_draft(_draft(), profile=profile)
    if profile is not LintProfileId.PRODUCTION:
        assert result.summary.blocking is False
