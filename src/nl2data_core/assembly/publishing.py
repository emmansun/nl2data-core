"""Fail-closed publication gate from approved assemblies to bundle catalogs."""

from __future__ import annotations

from typing import Any, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field

from nl2data_core.bundles import (
    AssertionProvenanceSummary,
    BundleCatalogOutcome,
    DeploymentBindingRedactionSummary,
    PublishAuditRecord,
    PublishIdempotencyStatus,
    PublishVerificationSummary,
    SemanticModelBundle,
    validate_bundle,
)
from nl2data_core.canonical import sha256_fingerprint
from nl2data_core.metadata.policy import ProductionActivationContext

from .authorization import (
    LifecycleAction,
    LifecycleAuthorizationContext,
    LifecycleAuthorizer,
    LifecycleRole,
    require_lifecycle_authorization,
)
from .manifest import AcceptedAssertionManifest
from .models import (
    AssemblyDraft,
    AssemblyState,
    AssertionProvenanceKind,
    AssertionType,
    ReviewState,
)
from .separation import SeparationOfDutiesDecision

_MAX_ISSUES = 32


class AssemblyPublishIssue(BaseModel):
    """One bounded publication rejection safe for administrative surfaces."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    message: str = Field(min_length=1, max_length=256)


class ManifestBundleVerification(BaseModel):
    """Host semantic-contract result binding a manifest to an emitted Bundle."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    valid: bool
    issues: tuple[AssemblyPublishIssue, ...] = Field(
        default_factory=tuple,
        max_length=_MAX_ISSUES,
    )


class ManifestBundleVerifier(Protocol):
    """Required host verifier for assertion-manifest to Bundle equivalence."""

    def verify(
        self,
        draft: AssemblyDraft,
        manifest: AcceptedAssertionManifest,
        bundle: SemanticModelBundle,
    ) -> ManifestBundleVerification: ...


class SemanticBundleEmitter(Protocol):
    """Host emitter that constructs the runtime Bundle inside publish."""

    def emit(self, draft: AssemblyDraft) -> SemanticModelBundle: ...


class AssemblyPublicationCatalog(Protocol):
    """Tenant-bound catalog port used by the atomic assembly publish gate."""

    def publish(
        self,
        bundle: SemanticModelBundle,
        *,
        accepted_assertion_manifest: AcceptedAssertionManifest | None = None,
        audit: PublishAuditRecord | None = None,
        production: ProductionActivationContext | None = None,
        draft: AssemblyDraft | None = None,
        expected_revision: int | None = None,
        tenant_scope_fingerprint: str | None = None,
    ) -> BundleCatalogOutcome: ...


class AssemblyPublishOutcome(BaseModel):
    """Bounded result of a publication attempt."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    bundle: SemanticModelBundle | None = None
    manifest: AcceptedAssertionManifest | None = None
    audit_reference: str | None = None
    superseded_fingerprint: str | None = None
    idempotency_status: PublishIdempotencyStatus | None = None
    issues: tuple[AssemblyPublishIssue, ...] = Field(
        default_factory=tuple,
        max_length=_MAX_ISSUES,
    )

    @property
    def success(self) -> bool:
        return self.kind in {"published", "reused"}


def _rejected(code: str, message: str) -> AssemblyPublishOutcome:
    return AssemblyPublishOutcome(
        kind="rejected",
        issues=(AssemblyPublishIssue(code=code, message=message),),
    )


def _catalog_outcome(
    outcome: BundleCatalogOutcome,
    manifest: AcceptedAssertionManifest,
) -> AssemblyPublishOutcome:
    if outcome.success:
        return AssemblyPublishOutcome(
            kind=outcome.kind,
            bundle=outcome.bundle,
            manifest=manifest,
            audit_reference=outcome.audit_reference,
            superseded_fingerprint=outcome.superseded_fingerprint,
            idempotency_status=outcome.idempotency_status,
        )
    return AssemblyPublishOutcome(
        kind=outcome.kind,
        issues=tuple(
            AssemblyPublishIssue(code=issue.code, message=issue.message)
            for issue in outcome.issues
        ),
    )


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
        if entity is None:
            return None
        return {**base, **entity.canonical_payload()}
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
            (
                item
                for item in entity.fields
                if item.field_id == payload.get("field_id")
            ),
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
        return (
            {**base, **relationship.canonical_payload()}
            if relationship is not None
            else None
        )
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


def _manifest_matches_bundle(
    manifest: AcceptedAssertionManifest,
    bundle: SemanticModelBundle,
) -> bool:
    return all(
        (
            candidate := _bundle_assertion_payload(
                assertion.type,
                assertion.payload,
                bundle,
            )
        )
        is not None
        and _payload_contains(candidate, assertion.payload)
        for assertion in manifest.assertions
    )


def publish_assembly(
    draft: AssemblyDraft,
    *,
    expected_revision: int,
    authorization: LifecycleAuthorizationContext,
    authorizer: LifecycleAuthorizer,
    separation: SeparationOfDutiesDecision,
    emitter: SemanticBundleEmitter,
    verifier: ManifestBundleVerifier,
    catalog: AssemblyPublicationCatalog,
    approval_chain: tuple[str, ...] = (),
    production: ProductionActivationContext | None = None,
) -> AssemblyPublishOutcome:
    """Validate all publication gates before one atomic catalog write."""
    require_lifecycle_authorization(
        context=authorization,
        authorizer=authorizer,
        required_role=LifecycleRole.PUBLISHER,
        action=LifecycleAction.PUBLISH,
        resource_id=draft.draft_id,
    )
    draft.require_revision(expected_revision)
    if draft.state is not AssemblyState.APPROVED:
        return _rejected("draft_not_approved", "publication requires an approved draft")
    if any(
        assertion.review_state is ReviewState.PENDING
        or not assertion.has_valid_review_binding()
        for assertion in draft.assertions
    ):
        return _rejected(
            "pending_assertions",
            "publication requires every assertion to have a valid review decision",
        )
    if not separation.allowed:
        return _rejected(
            "separation_of_duties_failed",
            "publication does not satisfy separation-of-duties policy",
        )
    try:
        bundle = emitter.emit(draft)
    except Exception:
        return _rejected("bundle_emission_failed", "semantic Bundle emission failed")
    if (
        bundle.bundle_id != draft.bundle_id
        or bundle.model_version != draft.model_version
        or bundle.descriptor.source_id != draft.source_id
    ):
        return _rejected(
            "bundle_identity_mismatch",
            "emitted bundle identity or source does not match the approved draft",
        )
    validation = validate_bundle(bundle)
    if not validation.valid:
        return AssemblyPublishOutcome(
            kind="rejected",
            issues=tuple(
                AssemblyPublishIssue(code=issue.code, message=issue.message)
                for issue in validation.issues[:_MAX_ISSUES]
            ),
        )
    try:
        manifest = AcceptedAssertionManifest.from_draft(
            draft,
            bundle_fingerprint=bundle.fingerprint,
        )
        if not _manifest_matches_bundle(manifest, bundle):
            return _rejected(
                "manifest_mismatch",
                "accepted assertions do not match the emitted semantic Bundle",
            )
        verification = verifier.verify(draft, manifest, bundle)
    except Exception:
        return _rejected(
            "verification_failed",
            "manifest and bundle verification failed",
        )
    if not verification.valid:
        return AssemblyPublishOutcome(kind="rejected", issues=verification.issues)
    provenance_counts = {
        kind: sum(
            1
            for assertion in draft.assertions
            if assertion.review_state is ReviewState.APPROVED
            and assertion.provenance.kind is kind
        )
        for kind in AssertionProvenanceKind
    }
    reviewer_references = tuple(
        sorted(
            {
                assertion.review_binding.reviewer_reference
                for assertion in draft.assertions
                if assertion.review_binding is not None
            }
        )
    )
    chain = approval_chain or tuple(
        dict.fromkeys(
            (
                draft.author_reference,
                *reviewer_references,
                *(() if draft.approved_by is None else (draft.approved_by,)),
                authorization.operator_reference,
            )
        )
    )
    schemes = tuple(
        sorted(
            {
                binding.connection_reference.split(":", 1)[0]
                for binding in draft.deployment_bindings
            }
        )
    )
    try:
        audit = PublishAuditRecord(
            audit_id=(
                "publish-"
                + sha256_fingerprint(
                    {
                        "bundle_id": bundle.bundle_id,
                        "bundle_fingerprint": bundle.fingerprint,
                    }
                ).removeprefix("sha256:")[:24]
            ),
            bundle_id=bundle.bundle_id,
            bundle_fingerprint=bundle.fingerprint,
            approval_chain=chain,
            assertion_provenance=AssertionProvenanceSummary(
                manual=provenance_counts[AssertionProvenanceKind.MANUAL],
                discovered=provenance_counts[AssertionProvenanceKind.DISCOVERED],
                inferred=provenance_counts[AssertionProvenanceKind.INFERRED],
                llm_suggested=provenance_counts[
                    AssertionProvenanceKind.LLM_SUGGESTED
                ],
            ),
            verification=PublishVerificationSummary(
                structural_valid=True,
                manifest_equivalent=True,
                host_callback_count=1,
            ),
            idempotency_status=PublishIdempotencyStatus.CREATED,
            deployment_bindings=DeploymentBindingRedactionSummary(
                binding_count=len(draft.deployment_bindings),
                reference_schemes=schemes,
            ),
            separation_mode=separation.mode.value,
            separation_reason_code=separation.reason_code,
            waiver_reference=(
                separation.waiver.waiver_reference
                if separation.waiver is not None
                else None
            ),
        )
    except ValueError:
        return _rejected("audit_invalid", "publish audit metadata is invalid")
    outcome = catalog.publish(
        bundle,
        accepted_assertion_manifest=manifest,
        audit=audit,
        production=production,
        draft=draft,
        expected_revision=expected_revision,
        tenant_scope_fingerprint=authorization.tenant_scope_fingerprint,
    )
    return _catalog_outcome(outcome, manifest)