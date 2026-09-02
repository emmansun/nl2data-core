"""Normalized semantic snapshots consumed by the built-in lint rules.

A snapshot is a bounded, path-annotated view of one assembly's semantic
content.  It can be extracted from a validated authoring model (with
optional authoring source marks) or from a lifecycle ``AssemblyDraft``;
rules are pure functions over a snapshot and never touch lifecycle state.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from nl2data_core.assembly.authoring.diagnostics import AuthoringSourceMark
from nl2data_core.assembly.authoring.models import SemanticAssemblyAuthoring
from nl2data_core.assembly.models import (
    AssemblyDraft,
    AssertionType,
    SemanticAssertion,
)
from nl2data_core.views.models import ExprNode

SourceMarks = Mapping[tuple[str | int, ...], AuthoringSourceMark]


@dataclass(frozen=True)
class LintLocation:
    """A semantic target path plus an optional authoring source mark."""

    path: tuple[str | int, ...]
    mark: AuthoringSourceMark | None = None


@dataclass(frozen=True)
class LintEntityMember:
    entity_id: str
    label: str
    description: str
    location: LintLocation


@dataclass(frozen=True)
class LintFieldMember:
    entity_id: str
    field_id: str
    label: str
    description: str
    location: LintLocation
    pii: bool = False
    has_sample_values: bool = False
    mapping_terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class LintMeasureMember:
    measure_id: str
    field_id: str
    label: str
    description: str
    location: LintLocation


@dataclass(frozen=True)
class LintGrainMember:
    grain_id: str
    entity_id: str
    attributes: frozenset[str]
    description: str
    location: LintLocation


@dataclass(frozen=True)
class LintCalculatedMember:
    name: str
    label: str
    description: str
    location: LintLocation
    zero_division_policy: str = "null"
    has_division: bool = False


@dataclass(frozen=True)
class LintMappingMember:
    """One value-semantics mapping with its term/value business domain."""

    entity_id: str
    field_id: str
    location: LintLocation
    values_by_term: tuple[tuple[str, str], ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class LintVerificationView:
    """Verification-plan readiness facts for one assembly."""

    location: LintLocation
    enabled_smoke_cases: int = 0
    enabled_semantic_cases: int = 0
    enabled_cases_without_capabilities: int = 0


@dataclass(frozen=True)
class LintSnapshot:
    """Bounded semantic content of one assembly, ready for lint rules."""

    entities: tuple[LintEntityMember, ...] = ()
    fields: tuple[LintFieldMember, ...] = ()
    measures: tuple[LintMeasureMember, ...] = ()
    grains: tuple[LintGrainMember, ...] = ()
    calculated_fields: tuple[LintCalculatedMember, ...] = ()
    mappings: tuple[LintMappingMember, ...] = ()
    verification: LintVerificationView | None = None
    missing_source_hints: tuple[LintLocation, ...] = ()


def _expression_has_division(expression: ExprNode) -> bool:
    if expression.op == "div":
        return True
    if expression.left is not None and _expression_has_division(expression.left):
        return True
    return expression.right is not None and _expression_has_division(expression.right)


def _payload_expression_has_division(expression: Mapping[str, object]) -> bool:
    if expression.get("op") == "div":
        return True
    left = expression.get("left")
    right = expression.get("right")
    if isinstance(left, Mapping) and _payload_expression_has_division(left):
        return True
    return isinstance(right, Mapping) and _payload_expression_has_division(right)


def snapshot_from_authoring(
    model: SemanticAssemblyAuthoring,
    *,
    source_marks: SourceMarks | None = None,
) -> LintSnapshot:
    """Extract a lint snapshot from a validated authoring model."""
    marks = source_marks

    def mark_at(path: tuple[str | int, ...]) -> AuthoringSourceMark | None:
        if marks is None:
            return None
        for size in range(len(path), -1, -1):
            found = marks.get(path[:size])
            if found is not None:
                return found
        return None

    def location(
        *,
        path: tuple[str | int, ...],
        at: tuple[str | int, ...] | None = None,
    ) -> LintLocation:
        """Identity-stable semantic path with the source mark of its document position."""
        return LintLocation(path=path, mark=mark_at(at if at is not None else path))

    entities: list[LintEntityMember] = []
    fields: list[LintFieldMember] = []
    calculated: list[LintCalculatedMember] = []
    mappings: list[LintMappingMember] = []
    for entity_index, entity in enumerate(model.spec.entities):
        entity_path = ("spec", "entities", entity.entity_id)
        entity_at = ("spec", "entities", entity_index)
        entities.append(
            LintEntityMember(
                entity_id=entity.entity_id,
                label=entity.label,
                description=entity.description,
                location=location(path=entity_path, at=entity_at),
            )
        )
        for field_index, member in enumerate(entity.fields):
            semantics = member.value_semantics
            terms: tuple[str, ...] = ()
            values_by_term: tuple[tuple[str, str], ...] = ()
            field_path = (*entity_path, "fields", member.field_id)
            field_at = (*entity_at, "fields", field_index)
            if semantics is not None:
                terms = tuple(sorted(semantics.value_mapping))
                values_by_term = tuple(
                    (term, str(semantics.value_mapping[term]))
                    for term in sorted(semantics.value_mapping)
                )
                mappings.append(
                    LintMappingMember(
                        entity_id=entity.entity_id,
                        field_id=member.field_id,
                        location=location(path=field_path, at=field_at),
                        values_by_term=values_by_term,
                    )
                )
            fields.append(
                LintFieldMember(
                    entity_id=entity.entity_id,
                    field_id=member.field_id,
                    label=member.label,
                    description=member.description,
                    location=location(path=field_path, at=field_at),
                    pii=semantics is not None and semantics.pii,
                    has_sample_values=(
                        semantics is not None and bool(semantics.sample_values)
                    ),
                    mapping_terms=terms,
                )
            )
        for calculated_index, calculated_member in enumerate(entity.calculated_fields):
            calculated.append(
                LintCalculatedMember(
                    name=calculated_member.name,
                    label=calculated_member.label,
                    description=calculated_member.description,
                    location=location(
                        path=(
                            *entity_path,
                            "calculatedFields",
                            calculated_member.name,
                        ),
                        at=(*entity_at, "calculatedFields", calculated_index),
                    ),
                    zero_division_policy=calculated_member.zero_division_policy,
                    has_division=_expression_has_division(calculated_member.expression),
                )
            )
    measures = tuple(
        LintMeasureMember(
            measure_id=measure.measure_id,
            field_id=measure.field_id,
            label=measure.label,
            description=measure.description,
            location=location(
                path=("spec", "measures", measure.measure_id),
                at=("spec", "measures", measure_index),
            ),
        )
        for measure_index, measure in enumerate(model.spec.measures)
    )
    grains = tuple(
        LintGrainMember(
            grain_id=grain.grain_id,
            entity_id=grain.entity_id,
            attributes=frozenset(grain.attributes),
            description=grain.description,
            location=location(
                path=("spec", "grains", grain.grain_id),
                at=("spec", "grains", grain_index),
            ),
        )
        for grain_index, grain in enumerate(model.spec.grains)
    )
    verification = _authoring_verification(model, location)
    missing_hints: list[LintLocation] = []
    if model.spec.source.catalog_fingerprint is None:
        missing_hints.append(location(path=("spec", "source")))
    for reference_index, reference in enumerate(model.spec.source_references):
        if reference.catalog_fingerprint is None:
            missing_hints.append(
                location(
                    path=("spec", "sourceReferences", reference.reference_id),
                    at=("spec", "sourceReferences", reference_index),
                )
            )
    return LintSnapshot(
        entities=tuple(entities),
        fields=tuple(fields),
        measures=measures,
        grains=grains,
        calculated_fields=tuple(calculated),
        mappings=tuple(mappings),
        verification=verification,
        missing_source_hints=tuple(missing_hints),
    )


def _authoring_verification(
    model: SemanticAssemblyAuthoring,
    location: Callable[..., LintLocation],
) -> LintVerificationView | None:
    plan = model.spec.verification_plan
    if plan is None:
        return None
    enabled_smoke = sum(case.enabled for case in plan.smoke_cases)
    enabled_semantic = sum(case.enabled for case in plan.semantic_cases)
    without_capabilities = sum(
        case.enabled and not case.capability_requirements.capabilities
        for case in (*plan.smoke_cases, *plan.semantic_cases)
    )
    return LintVerificationView(
        location=location(path=("spec", "verificationPlan")),
        enabled_smoke_cases=enabled_smoke,
        enabled_semantic_cases=enabled_semantic,
        enabled_cases_without_capabilities=without_capabilities,
    )


def _assertion_location(assertion: SemanticAssertion) -> LintLocation:
    return LintLocation(path=("assertions", assertion.id))


def snapshot_from_draft(draft: AssemblyDraft) -> LintSnapshot:
    """Extract a lint snapshot from a lifecycle draft without mutation."""
    entities: list[LintEntityMember] = []
    fields: list[LintFieldMember] = []
    calculated: list[LintCalculatedMember] = []
    mappings: list[LintMappingMember] = []
    measures: list[LintMeasureMember] = []
    grains: list[LintGrainMember] = []
    for assertion in draft.assertions:
        payload = assertion.payload
        location = _assertion_location(assertion)
        if assertion.type is AssertionType.ENTITY:
            entities.append(
                LintEntityMember(
                    entity_id=str(payload.get("entity_id", "")),
                    label=str(payload.get("label", "")),
                    description=str(payload.get("description", "")),
                    location=location,
                )
            )
        elif assertion.type is AssertionType.FIELD:
            semantics = payload.get("value_semantics")
            semantics_mapping = semantics if isinstance(semantics, Mapping) else {}
            terms = tuple(sorted(str(term) for term in semantics_mapping.get("value_mapping", {})))
            if terms:
                values = semantics_mapping.get("value_mapping", {})
                mappings.append(
                    LintMappingMember(
                        entity_id=str(payload.get("entity_id", "")),
                        field_id=str(payload.get("field_id", "")),
                        location=location,
                        values_by_term=tuple(
                            (term, str(values[term])) for term in sorted(values, key=str)
                        ),
                    )
                )
            samples = semantics_mapping.get("sample_values")
            fields.append(
                LintFieldMember(
                    entity_id=str(payload.get("entity_id", "")),
                    field_id=str(payload.get("field_id", "")),
                    label=str(payload.get("label", "")),
                    description=str(payload.get("description", "")),
                    location=location,
                    pii=bool(semantics_mapping.get("pii", False)),
                    has_sample_values=bool(samples),
                    mapping_terms=terms,
                )
            )
        elif assertion.type is AssertionType.MEASURE:
            measures.append(
                LintMeasureMember(
                    measure_id=str(payload.get("measure_id", "")),
                    field_id=str(payload.get("field_id", "")),
                    label=str(payload.get("label", "")),
                    description=str(payload.get("description", "")),
                    location=location,
                )
            )
        elif assertion.type is AssertionType.GRAIN:
            attributes = payload.get("attributes", ())
            grains.append(
                LintGrainMember(
                    grain_id=str(payload.get("grain_id", "")),
                    entity_id=str(payload.get("entity_id", "")),
                    attributes=frozenset(str(item) for item in attributes),
                    description=str(payload.get("description", "")),
                    location=location,
                )
            )
        elif assertion.type is AssertionType.CALCULATED_FIELD:
            expression = payload.get("expression")
            calculated.append(
                LintCalculatedMember(
                    name=str(payload.get("name", "")),
                    label=str(payload.get("label", "")),
                    description=str(payload.get("description", "")),
                    location=location,
                    zero_division_policy=str(payload.get("zero_division_policy", "null")),
                    has_division=(
                        isinstance(expression, Mapping)
                        and _payload_expression_has_division(expression)
                    ),
                )
            )
    verification = None
    plan = draft.verification_plan
    if plan is not None:
        enabled_smoke = sum(case.enabled for case in plan.smoke_cases)
        enabled_semantic = sum(case.enabled for case in plan.semantic_cases)
        without_capabilities = sum(
            case.enabled and not case.capability_requirements.capabilities
            for case in (*plan.smoke_cases, *plan.semantic_cases)
        )
        verification = LintVerificationView(
            location=LintLocation(path=("verificationPlan",)),
            enabled_smoke_cases=enabled_smoke,
            enabled_semantic_cases=enabled_semantic,
            enabled_cases_without_capabilities=without_capabilities,
        )
    missing_hints: list[LintLocation] = []
    if draft.source_snapshot_fingerprint is None:
        missing_hints.append(LintLocation(path=("sourceSnapshotFingerprint",)))
    references = draft.authoring_source_references or ()
    for index, reference in enumerate(references):
        if reference.catalog_fingerprint is None:
            missing_hints.append(LintLocation(path=("authoringSourceReferences", index)))
    return LintSnapshot(
        entities=tuple(entities),
        fields=tuple(fields),
        measures=tuple(measures),
        grains=tuple(grains),
        calculated_fields=tuple(calculated),
        mappings=tuple(mappings),
        verification=verification,
        missing_source_hints=tuple(missing_hints),
    )
