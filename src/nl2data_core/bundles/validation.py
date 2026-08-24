"""Structural validation of Semantic Model Bundles.

Construction (pydantic) already enforces bounds, identifier patterns, safe
text, uniqueness, and the schema-version literal.  This module adds the
relational validation a catalog needs before publication or activation -
measure fields, aggregation validity, grain entities/attributes, trust fact
references, dependency fingerprints, completeness, and supported schema
compatibility - and reports every problem as a structured issue so a
rejected bundle never silently degrades.

The wrapped descriptor's own validation (entity/field/relationship
uniqueness and endpoints) is reused as-is; no rule is duplicated here.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from nl2data_core.planning.models import AggregationKind

from .models import (
    BUNDLE_SCHEMA_VERSION,
    BundleQualityStatus,
    SemanticModelBundle,
)

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"

#: Bounded number of issues reported by one validation pass.
_MAX_ISSUES = 64

#: Aggregations a measure may declare; ``none`` is a plain field reference.
_MEASURE_AGGREGATIONS: frozenset[AggregationKind] = frozenset(
    {"none", "count", "sum", "avg", "min", "max"}
)


class BundleValidationIssue(BaseModel):
    """One structured bundle validation issue with a safe reason code."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=256)
    member_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)

    def safe_payload(self) -> dict[str, str | None]:
        return {
            "code": self.code,
            "message": self.message,
            "member_id": self.member_id,
        }


class BundleValidationResult(BaseModel):
    """Immutable result of one bundle validation pass."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    valid: bool
    issues: tuple[BundleValidationIssue, ...] = Field(
        default_factory=tuple, max_length=_MAX_ISSUES
    )

    def issue_codes(self) -> list[str]:
        """The bounded issue codes of this validation result."""
        return [issue.code for issue in self.issues]


def validate_bundle(
    bundle: SemanticModelBundle,
    *,
    supported_schema_versions: tuple[int, ...] = (BUNDLE_SCHEMA_VERSION,),
) -> BundleValidationResult:
    """Validate a bundle's structure, references, and completeness.

    Returns a structured result; invalid bundles report bounded issues and
    must never be published or activated by a catalog.
    """
    issues: list[BundleValidationIssue] = []

    if bundle.schema_version not in supported_schema_versions:
        issues.append(
            BundleValidationIssue(
                code="incompatible_schema",
                message=(
                    f"bundle schema version {bundle.schema_version} is not supported "
                    f"by this runtime"
                ),
            )
        )

    if not bundle.sources:
        issues.append(
            BundleValidationIssue(
                code="missing_sources",
                message="a bundle must declare at least one source reference",
            )
        )
    else:
        if not any(
            source.source_id == bundle.descriptor.source_id for source in bundle.sources
        ):
            issues.append(
                BundleValidationIssue(
                    code="source_identity_mismatch",
                    message="bundle sources must include the descriptor source identity",
                    member_id=bundle.descriptor.source_id,
                )
            )

        descriptor_catalog_fingerprint = bundle.descriptor.catalog_fingerprint
        compatible_catalogs = bundle.compatibility.compatible_catalog_fingerprints
        if compatible_catalogs and (
            descriptor_catalog_fingerprint is None
            or descriptor_catalog_fingerprint not in compatible_catalogs
        ):
            issues.append(
                BundleValidationIssue(
                    code="catalog_incompatible",
                    message=(
                        "bundle compatibility metadata does not include the "
                        "descriptor catalog fingerprint"
                    ),
                    member_id=bundle.descriptor.descriptor_id,
                )
            )

    if bundle.provenance.quality is BundleQualityStatus.DRAFT:
        issues.append(
            BundleValidationIssue(
                code="quality_not_met",
                message="draft bundles cannot be published or activated",
            )
        )

    field_ids = bundle.field_ids()
    entity_ids = bundle.entity_ids()
    entity_field_ids: dict[str, frozenset[str]] = {
        entity.entity_id: frozenset(field.field_id for field in entity.fields)
        for entity in bundle.descriptor.entities
    }

    for measure in bundle.measures:
        field = bundle.descriptor.field(measure.field_id)
        if field is None:
            issues.append(
                BundleValidationIssue(
                    code="unknown_field",
                    message=f"measure '{measure.measure_id}' references an unknown field",
                    member_id=measure.field_id,
                )
            )
            continue
        if measure.aggregation not in _MEASURE_AGGREGATIONS:
            issues.append(
                BundleValidationIssue(
                    code="invalid_aggregation",
                    message=f"measure '{measure.measure_id}' declares an invalid aggregation",
                    member_id=measure.measure_id,
                )
            )
            continue
        if (
            field.allowed_aggregations
            and measure.aggregation not in field.allowed_aggregations
        ):
            issues.append(
                BundleValidationIssue(
                    code="aggregation_not_allowed",
                    message=(
                        f"measure '{measure.measure_id}' aggregates '{measure.field_id}' "
                        f"beyond the descriptor's allowed set"
                    ),
                    member_id=measure.field_id,
                )
            )

    for grain in bundle.grains:
        if grain.entity_id not in entity_ids:
            issues.append(
                BundleValidationIssue(
                    code="unknown_entity",
                    message=f"grain '{grain.grain_id}' references an unknown entity",
                    member_id=grain.entity_id,
                )
            )
            continue
        unknown = sorted(
            attribute
            for attribute in grain.attributes
            if attribute not in entity_field_ids[grain.entity_id]
        )
        if unknown:
            issues.append(
                BundleValidationIssue(
                    code="unknown_attribute",
                    message=(
                        f"grain '{grain.grain_id}' references fields outside "
                        f"entity '{grain.entity_id}'"
                    ),
                    member_id=unknown[0],
                )
            )

    relationship_ids = bundle.relationship_ids()
    for marker in bundle.trust_markers:
        if marker.fact_id not in relationship_ids and marker.fact_id not in field_ids:
            issues.append(
                BundleValidationIssue(
                    code="unknown_fact",
                    message=(
                        f"trust marker '{marker.marker_id}' references an unknown "
                        f"relationship or field"
                    ),
                    member_id=marker.fact_id,
                )
            )

    for dependency in bundle.dependencies:
        if (
            dependency.bundle_id == bundle.bundle_id
            and dependency.version == bundle.model_version
        ):
            issues.append(
                BundleValidationIssue(
                    code="self_dependency",
                    message="a bundle cannot depend on itself at the same version",
                    member_id=dependency.dependency_id,
                )
            )

    return BundleValidationResult(valid=not issues, issues=tuple(issues[: _MAX_ISSUES]))
