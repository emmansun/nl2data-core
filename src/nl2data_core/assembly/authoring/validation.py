"""Semantic validation and governed-model normalization for authoring."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError

from nl2data_core.assembly.models import DeploymentBinding
from nl2data_core.bundles.models import (
    BundleProvenance,
    BundleQualityStatus,
    SemanticGrain,
    SemanticMeasure,
    SemanticModelBundle,
    SemanticSourceReference,
)
from nl2data_core.bundles.validation import validate_bundle
from nl2data_core.views.models import (
    CalculatedField,
    SemanticDescriptor,
    SemanticEntityDescriptor,
    SemanticFieldDescriptor,
    SemanticRelationshipDescriptor,
)

from .diagnostics import (
    AuthoringDiagnostic,
    AuthoringPath,
    AuthoringSourceMark,
    AuthoringSummary,
    AuthoringValidationResult,
)
from .models import SemanticAssemblyAuthoring


@dataclass(frozen=True)
class NormalizedAuthoring:
    descriptor: SemanticDescriptor
    measures: tuple[SemanticMeasure, ...]
    grains: tuple[SemanticGrain, ...]
    source_references: tuple[SemanticSourceReference, ...]
    deployment_bindings: tuple[DeploymentBinding, ...]


def _diagnostic(
    code: str,
    path: tuple[str | int, ...],
    message: str,
    marks: dict[tuple[str | int, ...], AuthoringSourceMark] | None,
) -> AuthoringDiagnostic:
    mark = None
    if marks is not None:
        for size in range(len(path), -1, -1):
            mark = marks.get(path[:size])
            if mark is not None:
                break
    return AuthoringDiagnostic(
        code=code,  # type: ignore[arg-type]
        path=AuthoringPath(parts=path),
        mark=mark,
        message=message,
    )


def normalize_authoring(
    model: SemanticAssemblyAuthoring,
    *,
    source_marks: dict[tuple[str | int, ...], AuthoringSourceMark] | None = None,
) -> tuple[NormalizedAuthoring | None, tuple[AuthoringDiagnostic, ...]]:
    """Construct governed semantic models only after all references are valid."""
    issues: list[AuthoringDiagnostic] = []
    entity_fields = {
        entity.entity_id: {field.field_id for field in entity.fields}
        for entity in model.spec.entities
    }
    all_fields = {field_id for fields in entity_fields.values() for field_id in fields}

    for entity_index, entity in enumerate(model.spec.entities):
        for relationship_index, relationship in enumerate(entity.relationships):
            base = ("spec", "entities", entity_index, "relationships", relationship_index)
            target_fields = entity_fields.get(relationship.target_entity_id)
            if target_fields is None:
                issues.append(
                    _diagnostic(
                        "invalid_reference",
                        (*base, "targetEntityId"),
                        "The relationship target entity does not exist.",
                        source_marks,
                    )
                )
                continue
            if any(
                field_id not in entity_fields[entity.entity_id]
                for field_id in relationship.source_fields
            ):
                issues.append(
                    _diagnostic(
                        "invalid_reference",
                        (*base, "sourceFields"),
                        "A relationship source field does not exist on its entity.",
                        source_marks,
                    )
                )
            if any(field_id not in target_fields for field_id in relationship.target_fields):
                issues.append(
                    _diagnostic(
                        "invalid_reference",
                        (*base, "targetFields"),
                        "A relationship target field does not exist on its entity.",
                        source_marks,
                    )
                )

    for index, measure in enumerate(model.spec.measures):
        if measure.field_id not in all_fields:
            issues.append(
                _diagnostic(
                    "invalid_reference",
                    ("spec", "measures", index, "fieldId"),
                    "The measure field does not exist.",
                    source_marks,
                )
            )
    for index, grain in enumerate(model.spec.grains):
        fields = entity_fields.get(grain.entity_id)
        if fields is None:
            issues.append(
                _diagnostic(
                    "invalid_reference",
                    ("spec", "grains", index, "entityId"),
                    "The grain entity does not exist.",
                    source_marks,
                )
            )
        elif any(attribute not in fields for attribute in grain.attributes):
            issues.append(
                _diagnostic(
                    "invalid_reference",
                    ("spec", "grains", index, "attributes"),
                    "A grain attribute does not exist on its entity.",
                    source_marks,
                )
            )
    if issues:
        return None, tuple(issues)

    try:
        entities = tuple(
            SemanticEntityDescriptor(
                entity_id=entity.entity_id,
                label=entity.label,
                description=entity.description,
                fields=tuple(
                    SemanticFieldDescriptor(
                        field_id=field.field_id,
                        label=field.label,
                        description=field.description,
                        data_type=field.data_type,
                        allowed_aggregations=field.allowed_aggregations,
                        value_semantics=field.value_semantics,
                    )
                    for field in entity.fields
                ),
                relationships=tuple(
                    SemanticRelationshipDescriptor(
                        relationship_id=relationship.relationship_id,
                        source_entity_id=entity.entity_id,
                        target_entity_id=relationship.target_entity_id,
                        label=relationship.label,
                        source_fields=relationship.source_fields,
                        target_fields=relationship.target_fields,
                    )
                    for relationship in entity.relationships
                ),
                calculated_fields=(
                    tuple(
                        CalculatedField(
                            name=calculated.name,
                            label=calculated.label,
                            description=calculated.description,
                            expression=calculated.expression,
                            output_type=calculated.output_type,
                            requires=calculated.requires,
                            zero_division_policy=calculated.zero_division_policy,
                        )
                        for calculated in entity.calculated_fields
                    )
                    or None
                ),
            )
            for entity in model.spec.entities
        )
        descriptor = SemanticDescriptor(
            descriptor_id=model.metadata.bundle_id,
            version=1,
            source_id=model.spec.source.source_id,
            catalog_fingerprint=model.spec.source.catalog_fingerprint,
            entities=entities,
        )
        measures = tuple(
            SemanticMeasure(
                measure_id=measure.measure_id,
                field_id=measure.field_id,
                aggregation=measure.aggregation,
                label=measure.label,
                description=measure.description,
            )
            for measure in model.spec.measures
        )
        grains = tuple(
            SemanticGrain(
                grain_id=grain.grain_id,
                entity_id=grain.entity_id,
                attributes=grain.attributes,
                description=grain.description,
            )
            for grain in model.spec.grains
        )
        source_references = tuple(
            SemanticSourceReference(
                reference_id=reference.reference_id,
                source_id=reference.source_id,
                catalog_fingerprint=reference.catalog_fingerprint,
                description=reference.description,
            )
            for reference in model.spec.source_references
        ) or (
            SemanticSourceReference(
                reference_id=model.spec.source.source_id,
                source_id=model.spec.source.source_id,
                catalog_fingerprint=model.spec.source.catalog_fingerprint,
            ),
        )
        deployment_bindings = tuple(
            DeploymentBinding(
                binding_id=binding.binding_id,
                environment=binding.environment,
                source_id=binding.source_id,
                connection_reference=binding.connection_reference,
            )
            for binding in model.spec.deployment_bindings
        )
        oracle = SemanticModelBundle(
            bundle_id=model.metadata.bundle_id,
            model_version=model.metadata.model_version,
            descriptor=descriptor,
            measures=measures,
            grains=grains,
            sources=source_references,
            compatibility=model.spec.compatibility,
            provenance=BundleProvenance(
                owner_reference="authoring-validation",
                quality=BundleQualityStatus.VALIDATED,
            ),
        )
    except ValidationError:
        return None, (
            _diagnostic(
                "invalid_member",
                (),
                "The semantic authoring content is not valid.",
                source_marks,
            ),
        )

    bundle_validation = validate_bundle(oracle)
    if not bundle_validation.valid:
        diagnostics = tuple(
            _diagnostic(
                "invalid_reference",
                (),
                "The semantic authoring content contains an invalid reference.",
                source_marks,
            )
            for _ in bundle_validation.issues
        )
        if bundle_validation.truncated:
            diagnostics += tuple(
                _diagnostic(
                    "invalid_reference",
                    (),
                    "The semantic authoring content contains an invalid reference.",
                    source_marks,
                )
                for _ in range(bundle_validation.issue_count - len(diagnostics))
            )
        return None, diagnostics
    return (
        NormalizedAuthoring(
            descriptor=descriptor,
            measures=measures,
            grains=grains,
            source_references=source_references,
            deployment_bindings=deployment_bindings,
        ),
        (),
    )


def validate_authoring(
    model: SemanticAssemblyAuthoring,
    *,
    source_marks: dict[tuple[str | int, ...], AuthoringSourceMark] | None = None,
) -> AuthoringValidationResult:
    normalized, diagnostics = normalize_authoring(model, source_marks=source_marks)
    if normalized is None:
        return AuthoringValidationResult(
            diagnostics=diagnostics[:100],
            issue_count=len(diagnostics),
            truncated=len(diagnostics) > 100,
        )
    field_count = sum(len(entity.fields) for entity in model.spec.entities)
    assertion_count = (
        len(model.spec.entities)
        + field_count
        + sum(len(entity.relationships) for entity in model.spec.entities)
        + sum(len(entity.calculated_fields) for entity in model.spec.entities)
        + sum(
            field.value_semantics is not None
            for entity in model.spec.entities
            for field in entity.fields
        )
        + len(model.spec.measures)
        + len(model.spec.grains)
    )
    return AuthoringValidationResult(
        model=model,
        summary=AuthoringSummary(
            bundle_id=model.metadata.bundle_id,
            model_version=model.metadata.model_version,
            source_id=model.spec.source.source_id,
            entity_count=len(model.spec.entities),
            field_count=field_count,
            assertion_count=assertion_count,
        ),
    )
