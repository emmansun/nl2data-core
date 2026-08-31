"""Authorized semantic view model shared by the governed paths.

The view bounds which sources, root entities, and fields a semantic IR
may reference; IR view-scope validation lives in
:mod:`nl2data_core.planning.ir.validation`.  A view may optionally carry a
resolved Semantic View binding (``view_id``/``view_version``/``view_fingerprint``
together); an absent binding is the explicit unbound-IR compatibility
mode and never fabricates a resolved-view identity.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, model_validator

if TYPE_CHECKING:
    from nl2data_core.views.projection import ResolvedViewProjection

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"
_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"


class AuthorizedView(BaseModel):
    """The authorized semantic view an IR may reference.

    The binding fields are all-or-none: when any of ``view_id``,
    ``view_version``, or ``view_fingerprint`` is present, all three must
    be present.  An absent binding keeps the legacy unbound-IR
    compatibility behavior - IR validation and workflow evidence treat
    the view as unscoped and never fabricate a resolved-view identity.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    root_entity_ids: frozenset[str] = Field(default_factory=frozenset)
    field_ids: frozenset[str] = Field(default_factory=frozenset)
    calculated_field_ids: frozenset[str] = Field(default_factory=frozenset)
    catalog_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)

    # -- optional resolved-view binding (unbound compatibility when absent)
    view_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    view_version: int | None = Field(default=None, ge=1, le=1_000_000)
    view_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    allowed_operations: frozenset[str] = Field(default_factory=frozenset)
    allowed_relationships: frozenset[str] = Field(default_factory=frozenset)
    field_aggregation_restrictions: dict[str, frozenset[str]] | None = None
    result_shape_constraints: tuple[str, ...] = Field(default_factory=tuple)
    purpose: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)

    @model_validator(mode="after")
    def _binding_consistency(self) -> AuthorizedView:
        bound = [self.view_id, self.view_version, self.view_fingerprint]
        if any(value is not None for value in bound) and not all(
            value is not None for value in bound
        ):
            raise ValueError(
                "view binding requires view_id, view_version, and view_fingerprint together"
            )
        return self

    @property
    def view_bound(self) -> bool:
        """Whether this view carries a resolved-view binding."""
        return self.view_fingerprint is not None

    def contains_field(self, field_id: str) -> bool:
        return field_id in self.field_ids

    def contains_calculated_field(self, calculated_name: str) -> bool:
        """Whether the view permits the given calculated-field name."""
        return calculated_name in self.calculated_field_ids

    def allowed_aggregations_for(self, field_id: str) -> frozenset[str] | None:
        """The aggregations permitted for a field, or ``None`` when unconstrained."""
        if self.field_aggregation_restrictions is None:
            return None
        return self.field_aggregation_restrictions.get(field_id)

    @classmethod
    def from_projection(cls, projection: ResolvedViewProjection) -> AuthorizedView:
        """Build the shared authorized-view contract from a resolved projection.

        The projection is the single source of truth: only members present
        in the projection can enter the view, and the binding fingerprint
        is the projection fingerprint so IR/workflow evidence stays
        revalidatable against the current resolution.
        """
        return cls(
            source_id=projection.source_id,
            root_entity_ids=projection.root_entity_ids,
            field_ids=projection.field_ids,
            calculated_field_ids=projection.calculated_field_ids,
            catalog_fingerprint=projection.catalog_fingerprint,
            view_id=projection.view_id,
            view_version=projection.view_version,
            view_fingerprint=projection.fingerprint,
            allowed_operations=projection.allowed_operations,
            allowed_relationships=projection.allowed_relationships,
            field_aggregation_restrictions={
                field.field_id: frozenset(field.allowed_aggregations)
                for entity in projection.entities
                for field in entity.fields
            }
            or None,
            result_shape_constraints=projection.result_shape_constraints,
            purpose=projection.purpose,
        )

    def to_unbound(self) -> AuthorizedView:
        """A copy of this view without the resolved-view binding.

        Used only by the explicit unbound-IR compatibility path; the copy
        keeps the scoped members but carries no view identity, so it never
        fabricates a resolved-view reference.
        """
        return self.model_copy(
            update={
                "view_id": None,
                "view_version": None,
                "view_fingerprint": None,
                "allowed_operations": frozenset(),
                "allowed_relationships": frozenset(),
                "field_aggregation_restrictions": None,
                "result_shape_constraints": (),
                "purpose": None,
            }
        )
