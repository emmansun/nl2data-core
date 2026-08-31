"""Pure core-owned Layer 1 structural verification."""

from __future__ import annotations

from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field

from nl2data_core.assembly.manifest import AcceptedAssertionManifest
from nl2data_core.assembly.models import (
    AssemblyDraft,
    AssemblyState,
    AssertionType,
    ReviewState,
)
from nl2data_core.bundles import SemanticModelBundle, validate_bundle
from nl2data_core.verification.models import (
    VerificationCaseEvidence,
    VerificationLayer,
    VerificationLayerEvidence,
    VerificationStatus,
)

CORE_RUNNER_ID = "nl2data-core-structural"
CORE_RUNNER_VERSION = 1


class StructuralVerificationIssue(BaseModel):
    """Bounded safe detail for one failed structural check."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    message: str = Field(min_length=1, max_length=256)


class CoreStructuralVerificationResult(BaseModel):
    """Layer 1 evidence and the derived manifest when construction succeeded."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence: VerificationLayerEvidence
    manifest: AcceptedAssertionManifest | None = None
    issues: tuple[StructuralVerificationIssue, ...] = Field(default_factory=tuple, max_length=32)

    @property
    def valid(self) -> bool:
        return self.evidence.status is VerificationStatus.PASSED


def _payload_contains(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and _payload_contains(actual[key], value)
            for key, value in expected.items()
        )
    if isinstance(expected, (list, tuple)):
        return isinstance(actual, (list, tuple)) and list(actual) == list(expected)
    return cast(bool, actual == expected)


def _bundle_assertion_payload(
    assertion_type: AssertionType,
    payload: dict[str, Any],
    bundle: SemanticModelBundle,
) -> dict[str, Any] | None:
    descriptor = bundle.descriptor
    descriptor_id = payload.get("descriptor_id")
    if descriptor_id != descriptor.descriptor_id:
        return None
    base = {"descriptor_id": descriptor.descriptor_id}
    if assertion_type is AssertionType.ENTITY:
        entity = descriptor.entity(str(payload.get("entity_id")))
        return {**base, **entity.canonical_payload()} if entity is not None else None
    if assertion_type in {
        AssertionType.FIELD,
        AssertionType.MAPPING,
        AssertionType.CALCULATED_FIELD,
    }:
        entity = descriptor.entity(str(payload.get("entity_id")))
        if entity is None:
            return None
        if assertion_type is AssertionType.CALCULATED_FIELD:
            calculated = entity.calculated_field(str(payload.get("name")))
            return (
                {**base, "entity_id": entity.entity_id, **calculated.canonical_payload()}
                if calculated is not None
                else None
            )
        field = next(
            (item for item in entity.fields if item.field_id == payload.get("field_id")),
            None,
        )
        if field is None:
            return None
        candidate = {**base, "entity_id": entity.entity_id, **field.canonical_payload()}
        if assertion_type is AssertionType.MAPPING:
            semantics = field.value_semantics
            if semantics is None:
                return None
            candidate.update(semantics.canonical_payload())
        return candidate
    if assertion_type is AssertionType.RELATIONSHIP:
        relationship = next(
            (
                item
                for entity in descriptor.entities
                for item in entity.relationships
                if item.relationship_id == payload.get("relationship_id")
            ),
            None,
        )
        return {**base, **relationship.canonical_payload()} if relationship is not None else None
    if assertion_type is AssertionType.MEASURE:
        measure = next(
            (item for item in bundle.measures if item.measure_id == payload.get("measure_id")),
            None,
        )
        return {**base, **measure.canonical_payload()} if measure is not None else None
    if assertion_type is AssertionType.GRAIN:
        grain = next(
            (item for item in bundle.grains if item.grain_id == payload.get("grain_id")),
            None,
        )
        return {**base, **grain.canonical_payload()} if grain is not None else None
    return None


def manifest_matches_bundle(
    manifest: AcceptedAssertionManifest,
    bundle: SemanticModelBundle,
) -> bool:
    """Return whether every accepted semantic assertion is represented by the Bundle."""
    return all(
        (
            candidate := _bundle_assertion_payload(assertion.type, assertion.payload, bundle)
        )
        is not None
        and _payload_contains(candidate, assertion.payload)
        for assertion in manifest.assertions
    )


class CoreStructuralVerificationRunner:
    """Deterministically evaluate core structural publication invariants."""

    runner_id = CORE_RUNNER_ID
    runner_version = CORE_RUNNER_VERSION

    def run(
        self,
        draft: AssemblyDraft,
        bundle: SemanticModelBundle,
        *,
        expected_revision: int,
        expected_source_id: str,
    ) -> CoreStructuralVerificationResult:
        checks: list[tuple[str, bool, str]] = []
        checks.append(
            (
                "draft_approved",
                draft.state is AssemblyState.APPROVED,
                "publication requires an approved draft",
            )
        )
        checks.append(
            (
                "draft_revision_matches",
                draft.draft_revision == expected_revision,
                "the frozen draft revision does not match",
            )
        )
        checks.append(
            (
                "review_bindings_valid",
                all(
                    assertion.review_state is not ReviewState.PENDING
                    and assertion.has_valid_review_binding()
                    for assertion in draft.assertions
                ),
                "one or more assertion review bindings are invalid",
            )
        )
        plan_fingerprint = (
            draft.verification_plan.fingerprint if draft.verification_plan is not None else None
        )
        checks.append(
            (
                "verification_plan_bound",
                draft.approved_verification_plan_fingerprint == plan_fingerprint,
                "the approved verification plan binding does not match",
            )
        )
        checks.append(
            (
                "bundle_identity_mismatch",
                bundle.bundle_id == draft.bundle_id
                and bundle.model_version == draft.model_version
                and bundle.descriptor.source_id == draft.source_id,
                "emitted Bundle identity does not match the draft",
            )
        )
        checks.append(
            (
                "source_scope_matches",
                expected_source_id == draft.source_id,
                "trusted source scope does not match the draft",
            )
        )
        validation = validate_bundle(bundle)
        checks.append(
            (
                "bundle_valid",
                validation.valid,
                "emitted Bundle structural validation failed",
            )
        )
        manifest: AcceptedAssertionManifest | None = None
        try:
            manifest = AcceptedAssertionManifest.from_draft(
                draft,
                bundle_fingerprint=bundle.fingerprint,
            )
            manifest_derived = True
        except (TypeError, ValueError):
            manifest_derived = False
        checks.append(
            (
                "manifest_derived",
                manifest_derived,
                "accepted assertion manifest derivation failed",
            )
        )
        checks.append(
            (
                "manifest_mismatch",
                manifest is not None and manifest_matches_bundle(manifest, bundle),
                "accepted assertions do not match the emitted semantic Bundle",
            )
        )

        cases = tuple(
            VerificationCaseEvidence(
                case_id=code,
                layer=VerificationLayer.STRUCTURAL,
                status=(VerificationStatus.PASSED if passed else VerificationStatus.FAILED),
                assertion_count=1,
                passed_assertion_count=int(passed),
                issue_codes=(() if passed else (code,)),
            )
            for code, passed, _ in checks
        )
        issues = tuple(
            StructuralVerificationIssue(code=code, message=message)
            for code, passed, message in checks
            if not passed
        )
        status = VerificationStatus.PASSED if not issues else VerificationStatus.FAILED
        return CoreStructuralVerificationResult(
            evidence=VerificationLayerEvidence(
                layer=VerificationLayer.STRUCTURAL,
                status=status,
                cases=cases,
                issue_codes=tuple(issue.code for issue in issues),
            ),
            manifest=manifest,
            issues=issues,
        )