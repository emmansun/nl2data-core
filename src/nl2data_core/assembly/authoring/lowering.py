"""Pure deterministic lowering from authoring models to lifecycle drafts."""

from __future__ import annotations

from pydantic import ValidationError

from nl2data_core.assembly.models import (
    ASSEMBLY_API_VERSION,
    AssemblyDraft,
    AssertionProvenance,
    AssertionProvenanceKind,
    AssertionType,
    SemanticAssertion,
)

from .diagnostics import AuthoringDiagnostic, AuthoringLoweringResult
from .models import SemanticAssemblyAuthoring
from .policy_templates import PolicyTemplateError, expand_policy_templates
from .validation import normalize_authoring


def lower_authoring(
    model: SemanticAssemblyAuthoring,
    *,
    draft_id: str,
    author_reference: str,
) -> AuthoringLoweringResult:
    """Create a clean revision-zero draft using only trusted lifecycle inputs."""
    normalized, diagnostics = normalize_authoring(model)
    if normalized is None:
        return AuthoringLoweringResult(
            diagnostics=diagnostics[:100],
            issue_count=len(diagnostics),
            truncated=len(diagnostics) > 100,
        )

    descriptor_id = normalized.descriptor.descriptor_id
    provenance = AssertionProvenance(kind=AssertionProvenanceKind.MANUAL)
    assertions: list[SemanticAssertion] = []

    for entity in normalized.descriptor.entities:
        assertions.append(
            SemanticAssertion.create(
                type=AssertionType.ENTITY,
                payload={
                    "descriptor_id": descriptor_id,
                    "entity_id": entity.entity_id,
                    "label": entity.label,
                    "description": entity.description,
                },
                provenance=provenance,
            )
        )
        for field in entity.fields:
            assertions.append(
                SemanticAssertion.create(
                    type=AssertionType.FIELD,
                    payload={
                        "descriptor_id": descriptor_id,
                        "entity_id": entity.entity_id,
                        **field.canonical_payload(),
                    },
                    provenance=provenance,
                )
            )
            if field.value_semantics is not None:
                assertions.append(
                    SemanticAssertion.create(
                        type=AssertionType.MAPPING,
                        payload={
                            "descriptor_id": descriptor_id,
                            "entity_id": entity.entity_id,
                            "field_id": field.field_id,
                            **field.value_semantics.canonical_payload(),
                        },
                        provenance=provenance,
                    )
                )
        for relationship in entity.relationships:
            assertions.append(
                SemanticAssertion.create(
                    type=AssertionType.RELATIONSHIP,
                    payload={"descriptor_id": descriptor_id, **relationship.canonical_payload()},
                    provenance=provenance,
                )
            )
        for calculated in entity.calculated_fields or ():
            assertions.append(
                SemanticAssertion.create(
                    type=AssertionType.CALCULATED_FIELD,
                    payload={
                        "descriptor_id": descriptor_id,
                        "entity_id": entity.entity_id,
                        **calculated.canonical_payload(),
                    },
                    provenance=provenance,
                )
            )
    for measure in normalized.measures:
        assertions.append(
            SemanticAssertion.create(
                type=AssertionType.MEASURE,
                payload={"descriptor_id": descriptor_id, **measure.canonical_payload()},
                provenance=provenance,
            )
        )
    for grain in normalized.grains:
        assertions.append(
            SemanticAssertion.create(
                type=AssertionType.GRAIN,
                payload={"descriptor_id": descriptor_id, **grain.canonical_payload()},
                provenance=provenance,
            )
        )
    try:
        expanded = expand_policy_templates(model)
    except PolicyTemplateError:
        # normalize_authoring already validated the declarations; this is a
        # defensive guard so lowering never emits a partial draft.
        diagnostic = AuthoringDiagnostic(
            code="invalid_member",
            message="The policy template declarations are not valid.",
        )
        return AuthoringLoweringResult(diagnostics=(diagnostic,), issue_count=1)
    for policy in expanded:
        assertions.append(
            SemanticAssertion.create(
                type=AssertionType.POLICY,
                payload={"descriptor_id": descriptor_id, **policy.payload},
                provenance=provenance,
            )
        )

    try:
        draft = AssemblyDraft(
            apiVersion=ASSEMBLY_API_VERSION,
            draft_id=draft_id,
            bundle_id=model.metadata.bundle_id,
            source_id=model.spec.source.source_id,
            model_version=model.metadata.model_version,
            assertions=tuple(sorted(assertions, key=lambda assertion: assertion.id)),
            deployment_bindings=normalized.deployment_bindings,
            author_reference=author_reference,
            source_snapshot_fingerprint=model.spec.source.catalog_fingerprint,
            authoring_description=model.metadata.description,
            authoring_source_references=normalized.source_references,
            authoring_compatibility=model.spec.compatibility,
            verification_plan=(
                model.spec.verification_plan.to_plan()
                if model.spec.verification_plan is not None
                else None
            ),
        )
    except ValidationError:
        diagnostic = AuthoringDiagnostic(
            code="invalid_member",
            message="The trusted draft identity or author reference is not valid.",
        )
        return AuthoringLoweringResult(diagnostics=(diagnostic,), issue_count=1)
    return AuthoringLoweringResult(draft=draft)
