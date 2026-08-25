"""Bounded Semantic View registry and fail-closed resolver.

Resolution is a pure function of a view definition plus trusted resolution
context: it applies tenant scope, principal authorization, purpose, policy,
model/catalog/bundle version, adapter capabilities, and feature flags
before projecting any member.  Every failure path is structured and safe - a
missing, inactive, mismatched, stale, or unsupported input never yields
partial semantic members.

When bundle-backed catalog resolution is configured (``bundle=``), views
bound to the bundle's descriptor resolve against the complete active
validated bundle snapshot and their projections carry the bundle
identity/version/fingerprint; other views keep the explicit descriptor-only
compatibility path.  The bundle wraps the descriptor, so there is exactly
one conversion path and no duplicated validation rules.

When no registry is configured, callers fall back to the explicit unbound
IR compatibility mode: existing IR executes without a resolved-view
identity, and no view identity is fabricated.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from nl2data_core.canonical import sha256_fingerprint
from nl2data_core.planning.models import AggregationKind

if TYPE_CHECKING:
    from nl2data_core.bundles.models import SemanticModelBundle

from .context import ResolutionContext
from .models import (
    SemanticDescriptor,
    SemanticViewDefinition,
    ViewMemberRestrictions,
    ViewProvenance,
)
from .outcomes import ResolutionIssue, ResolutionOutcome, denied, unavailable
from .projection import ResolvedViewEntity, ResolvedViewField, ResolvedViewProjection

#: Resolver contract version recorded in every projection provenance.
_RESOLVER_VERSION = 1


class ViewRegistry:
    """An immutable bounded registry of descriptors and view definitions.

    Registration validates duplicate ids, unknown descriptor references,
    and definition-level operation constraints so a misconfigured registry
    fails at construction - before any resolution or provider work.
    """

    def __init__(
        self,
        *,
        descriptors: Iterable[SemanticDescriptor],
        views: Iterable[SemanticViewDefinition],
        bundle: SemanticModelBundle | None = None,
    ) -> None:
        descriptor_list = list(descriptors)
        view_list = list(views)
        descriptor_ids = [descriptor.descriptor_id for descriptor in descriptor_list]
        if len(descriptor_ids) != len(set(descriptor_ids)):
            raise ValueError("descriptor ids must be unique within a registry")
        view_ids = [definition.view_id for definition in view_list]
        if len(view_ids) != len(set(view_ids)):
            raise ValueError("view ids must be unique within a registry")
        known_descriptors = frozenset(descriptor_ids)
        for definition in view_list:
            if definition.descriptor_id not in known_descriptors:
                raise ValueError(
                    f"view '{definition.view_id}' references unknown descriptor "
                    f"'{definition.descriptor_id}'"
                )
            operations = definition.restrictions.allowed_operations
            if operations and "select" not in operations:
                raise ValueError(
                    f"view '{definition.view_id}' allowed_operations must include 'select'"
                )
        if bundle is not None and bundle.descriptor.descriptor_id not in known_descriptors:
            raise ValueError(
                f"bundle descriptor '{bundle.descriptor.descriptor_id}' must be "
                "registered in the registry"
            )
        self._descriptors = {
            descriptor.descriptor_id: descriptor for descriptor in descriptor_list
        }
        self._views = {definition.view_id: definition for definition in view_list}
        self._bundle = bundle

    @property
    def bundle(self) -> SemanticModelBundle | None:
        """The active validated bundle snapshot, or ``None`` (descriptor mode)."""
        return self._bundle

    @property
    def is_empty(self) -> bool:
        """Whether the registry carries no views (unbound compatibility)."""
        return not self._views

    def view(self, view_id: str) -> SemanticViewDefinition | None:
        """The registered view definition, or ``None`` when unknown."""
        return self._views.get(view_id)

    def descriptor(self, descriptor_id: str) -> SemanticDescriptor | None:
        """The registered semantic descriptor, or ``None`` when unknown.

        When bundle-backed resolution is configured, the bundle's descriptor
        is the authoritative snapshot for the bundle's descriptor id.
        """
        if self._bundle is not None and self._bundle.descriptor.descriptor_id == descriptor_id:
            return self._bundle.descriptor
        return self._descriptors.get(descriptor_id)

    def view_ids(self) -> frozenset[str]:
        """Every registered view id."""
        return frozenset(self._views)

    def resolve(self, view_id: str, context: ResolutionContext) -> ResolutionOutcome:
        """Resolve a view against trusted context, failing closed.

        Returns a ``resolved`` outcome with the authorized projection or a
        structured ``denied``/``unavailable`` outcome - never partial
        members and never hidden policy or physical metadata.
        """
        definition = self._views.get(view_id)
        if definition is None:
            return unavailable(
                "view_not_found", f"no view '{view_id}' is registered"
            )
        descriptor = self._descriptors.get(definition.descriptor_id)
        if descriptor is None:
            return unavailable(
                "descriptor_missing",
                f"view '{view_id}' references an unregistered descriptor",
            )

        # -- bundle snapshot (fail closed when bundle-backed) -----------------
        bundle: SemanticModelBundle | None = None
        if (
            self._bundle is not None
            and self._bundle.descriptor.descriptor_id == definition.descriptor_id
        ):
            bundle = self._bundle
            descriptor = bundle.descriptor
            if context.bundle_fingerprint is None:
                return unavailable(
                    "bundle_scope_missing",
                    "bundle-backed resolution requires an active bundle fingerprint",
                )
            if context.bundle_fingerprint != bundle.fingerprint:
                return unavailable(
                    "bundle_stale",
                    "the trusted bundle fingerprint does not match the active bundle",
                )
            if (
                context.snapshot_fingerprint is not None
                and bundle.descriptor.catalog_fingerprint != context.snapshot_fingerprint
            ):
                return unavailable(
                    "snapshot_stale",
                    "the trusted discovery snapshot does not match the active "
                    "bundle source snapshot",
                )

        # -- tenant scope (fail closed) -------------------------------------
        if context.tenant_scope_fingerprint is None:
            return denied("tenant_scope_missing", "resolution requires a trusted tenant scope")
        if not context.tenant_active:
            return denied(
                "tenant_scope_inactive",
                "the trusted tenant scope is not active",
            )
        if (
            definition.bound_tenant_scope_fingerprint is not None
            and context.tenant_scope_fingerprint != definition.bound_tenant_scope_fingerprint
        ):
            return denied(
                "tenant_scope_mismatch",
                "view is bound to a different tenant scope",
            )

        # -- principal authorization scope ----------------------------------
        bound_principals = definition.bound_principal_authorization_fingerprints
        if bound_principals:
            if context.principal_authorization_fingerprint is None:
                return denied(
                    "principal_denied",
                    "view requires a principal authorization fingerprint",
                )
            if context.principal_authorization_fingerprint not in bound_principals:
                return denied(
                    "principal_denied",
                    "principal is not authorized for this view",
                )

        # -- purpose ---------------------------------------------------------
        if definition.allowed_purposes and context.purpose not in definition.allowed_purposes:
            return denied(
                "purpose_denied",
                "the requested purpose is not allowed by this view",
            )

        # -- policy decision -------------------------------------------------
        if (
            definition.bound_policy_fingerprint is not None
            and context.policy_fingerprint != definition.bound_policy_fingerprint
        ):
            return denied(
                "policy_mismatch",
                "view is bound to a different policy decision",
            )

        # -- model / catalog version ----------------------------------------
        if (
            definition.model_version is not None
            and context.model_version != definition.model_version
        ):
            return unavailable(
                "model_stale",
                "view requires a different model version",
            )
        if (
            descriptor.catalog_fingerprint is not None
            and context.catalog_fingerprint != descriptor.catalog_fingerprint
        ):
            return unavailable(
                "catalog_stale",
                "view descriptor is bound to a different catalog",
            )

        # -- adapter capabilities and feature flags --------------------------
        missing_capabilities = sorted(
            definition.required_capabilities - context.adapter_capabilities
        )
        if missing_capabilities:
            return unavailable(
                "capability_unsupported",
                "adapter capabilities do not satisfy the view requirements",
                member_id=missing_capabilities[0],
            )
        missing_flags = sorted(definition.required_feature_flags - context.feature_flags)
        if missing_flags:
            return unavailable(
                "feature_flag_disabled",
                "required feature flags are not enabled",
                member_id=missing_flags[0],
            )

        # -- project the authorized surface -----------------------------------
        projection, issues = _project(definition, descriptor, context, bundle=bundle)
        if issues:
            return ResolutionOutcome(kind="unavailable", issues=tuple(issues))
        assert projection is not None
        return ResolutionOutcome(kind="resolved", projection=projection)


def _missing_member_issue(member_kind: str, member_id: str) -> ResolutionIssue:
    return ResolutionIssue(
        code="missing_member",
        message=f"{member_kind} '{member_id}' is not present in the descriptor",
        member_id=member_id,
    )


def _project(
    definition: SemanticViewDefinition,
    descriptor: SemanticDescriptor,
    context: ResolutionContext,
    *,
    bundle: SemanticModelBundle | None = None,
) -> tuple[ResolvedViewProjection | None, list[ResolutionIssue]]:
    """Apply member restrictions over the descriptor.

    Restrictions are constraints, not authority: aggregation restrictions
    only narrow the descriptor's allowed set, and unresolved member
    references fail closed with bounded missing-member issues.  When a
    validated bundle snapshot is supplied, the projection binds the bundle
    identity/version/fingerprint so evidence stays revalidatable.
    """
    issues: list[ResolutionIssue] = []
    restrictions: ViewMemberRestrictions = definition.restrictions
    entity_ids = frozenset(entity.entity_id for entity in descriptor.entities)
    all_field_ids = descriptor.all_field_ids()
    all_relationship_ids = descriptor.all_relationship_ids()

    for entity_id in sorted(restrictions.include_entities | restrictions.exclude_entities):
        if entity_id not in entity_ids:
            issues.append(_missing_member_issue("entity", entity_id))
    for field_id in sorted(
        restrictions.include_fields
        | restrictions.exclude_fields
        | frozenset(restrictions.field_aliases)
        | frozenset(restrictions.field_aggregation_restrictions)
    ):
        if field_id not in all_field_ids:
            issues.append(_missing_member_issue("field", field_id))
    for relationship_id in sorted(restrictions.allowed_relationships):
        if relationship_id not in all_relationship_ids:
            issues.append(_missing_member_issue("relationship", relationship_id))
    if issues:
        return None, issues

    included_entities = (
        (restrictions.include_entities or entity_ids) - restrictions.exclude_entities
    )
    if not included_entities:
        issues.append(
            ResolutionIssue(
                code="view_bounds_exceeded",
                message="no entities remain after restriction",
            )
        )
        return None, issues

    entities: list[ResolvedViewEntity] = []
    root_entity_ids: set[str] = set()
    field_ids: set[str] = set()
    for entity_id in sorted(included_entities):
        entity = descriptor.entity(entity_id)
        assert entity is not None
        entity_field_ids = frozenset(field.field_id for field in entity.fields)
        selected = (
            (restrictions.include_fields & entity_field_ids)
            if restrictions.include_fields
            else entity_field_ids
        )
        selected -= restrictions.exclude_fields
        if not selected:
            continue
        resolved_fields = tuple(
            ResolvedViewField(
                field_id=field.field_id,
                alias=restrictions.field_aliases.get(field.field_id),
                label=field.label,
                description=field.description,
                data_type=field.data_type,
                allowed_aggregations=_restricted_aggregations(
                    field.allowed_aggregations,
                    restrictions.field_aggregation_restrictions.get(field.field_id),
                ),
            )
            for field in entity.fields
            if field.field_id in selected
        )
        relationships = frozenset(
            relationship.relationship_id
            for relationship in entity.relationships
            if relationship.relationship_id in restrictions.allowed_relationships
        )
        entities.append(
            ResolvedViewEntity(
                entity_id=entity_id,
                label=entity.label,
                description=entity.description,
                fields=resolved_fields,
                relationships=relationships,
            )
        )
        root_entity_ids.add(entity_id)
        field_ids.update(selected)
    if not field_ids:
        issues.append(
            ResolutionIssue(
                code="view_bounds_exceeded",
                message="no fields remain after restriction",
            )
        )
        return None, issues

    projection = ResolvedViewProjection(
        view_id=definition.view_id,
        view_version=definition.version,
        descriptor_id=descriptor.descriptor_id,
        source_id=descriptor.source_id,
        description=definition.description,
        root_entity_ids=frozenset(root_entity_ids),
        field_ids=frozenset(field_ids),
        entities=tuple(entities),
        allowed_operations=restrictions.allowed_operations or frozenset({"select"}),
        allowed_relationships=restrictions.allowed_relationships,
        result_shape_constraints=restrictions.result_shape_constraints,
        catalog_fingerprint=descriptor.catalog_fingerprint,
        bundle_id=bundle.bundle_id if bundle is not None else None,
        bundle_version=bundle.model_version if bundle is not None else None,
        bundle_fingerprint=bundle.fingerprint if bundle is not None else None,
        policy_fingerprint=context.policy_fingerprint,
        tenant_scope_fingerprint=context.tenant_scope_fingerprint,
        principal_authorization_fingerprint=context.principal_authorization_fingerprint,
        purpose=context.purpose,
        model_version=context.model_version,
        adapter_capability_fingerprint=sha256_fingerprint(
            sorted(context.adapter_capabilities)
        ),
        feature_flag_fingerprint=sha256_fingerprint(sorted(context.feature_flags)),
        provenance=ViewProvenance(
            descriptor_fingerprint=descriptor.fingerprint,
            policy_decision_fingerprint=context.policy_fingerprint,
            resolver_version=_RESOLVER_VERSION,
            bundle_id=bundle.bundle_id if bundle is not None else None,
            bundle_version=bundle.model_version if bundle is not None else None,
            bundle_fingerprint=bundle.fingerprint if bundle is not None else None,
        ),
    )
    return projection, issues


def _restricted_aggregations(
    descriptor_allowed: frozenset[AggregationKind],
    restriction: frozenset[AggregationKind] | None,
) -> frozenset[AggregationKind]:
    """Narrow the descriptor's allowed aggregations by the restriction.

    A restriction can only remove aggregations - it never grants an
    aggregation the descriptor does not already allow.
    """
    if restriction is None:
        return descriptor_allowed
    return frozenset(
        aggregation for aggregation in descriptor_allowed if aggregation in restriction
    )
