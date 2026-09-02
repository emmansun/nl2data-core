"""Published Bundle lifecycle Admin capability service."""

from __future__ import annotations

from nl2data_core.assembly import (
    LifecycleAction,
    LifecycleRole,
    require_lifecycle_authorization,
)
from nl2data_core.bundles.catalog import BundleCatalogOutcome
from nl2data_core.bundles.models import SemanticModelBundle
from nl2data_core.bundles.publication import PublishAuditRecord
from nl2data_core.bundles.validation import validate_bundle as core_validate_bundle

from .auth import AuthContext, Permission
from .common import (
    AdminDependencyAccess,
    lifecycle_context,
    page_window,
    require_permission,
    require_source,
)
from .common import (
    normalize_errors as _normalize_errors,
)
from .config import AdminServiceConfig
from .dtos import (
    BundleDetail,
    BundleLifecycleCommand,
    BundleLifecycleResult,
    BundleListItem,
    BundleValidationResult,
    ErrorDetail,
    LifecycleCommand,
    PaginatedResult,
    PaginationParams,
    PublishAuditSummary,
    PublishedVersionItem,
    VerificationEvidenceReference,
    VersionListResult,
)
from .errors import NotFoundError, ValidationError
from .protocols import AdminServiceDependencies, MetadataCatalogPort
from .verification_admin import verification_reference


def _bundle_to_detail(
    bundle: SemanticModelBundle,
    catalog: MetadataCatalogPort,
    auth_context: AuthContext,
) -> BundleDetail:
    active = catalog.active(
        bundle.bundle_id,
        tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
    )
    return BundleDetail(
        bundle_id=bundle.bundle_id,
        version=bundle.model_version,
        fingerprint=bundle.fingerprint,
        status="active" if active and active.fingerprint == bundle.fingerprint else "published",
        entity_count=len(bundle.descriptor.entities),
        relationship_count=len(bundle.descriptor.all_relationship_ids()),
        measure_count=len(bundle.measures),
        quality=bundle.provenance.quality.value,
        provenance_owner=bundle.provenance.owner_reference,
    )


def _lifecycle_result(
    command: LifecycleCommand,
    bundle_id: str,
    version: str | None,
    outcome: BundleCatalogOutcome,
    auth_context: AuthContext,
    host_audit_reference: str,
) -> BundleLifecycleResult:
    return BundleLifecycleResult(
        command=command,
        bundle_id=bundle_id,
        version=version,
        fingerprint=outcome.bundle.fingerprint if outcome.bundle else None,
        success=outcome.success,
        issues=tuple(
            ErrorDetail(code=issue.code, message=issue.message, member_id=issue.member_id)
            for issue in outcome.issues
        ),
        audit_reference=auth_context.audit_reference or host_audit_reference,
    )


class PublishedBundleAdminCapability:
    """Published Bundle lookup, audit/evidence, versions, activation, rollback."""

    def __init__(self, dependencies: AdminServiceDependencies, config: AdminServiceConfig) -> None:
        self._deps = dependencies
        self._access = AdminDependencyAccess(dependencies)
        self._config = config

    @_normalize_errors
    def get_publish_audit(
        self,
        bundle_id: str,
        fingerprint: str,
        *,
        auth_context: AuthContext,
    ) -> PublishAuditSummary:
        require_permission(auth_context, Permission.ASSEMBLY_AUDIT)
        catalog = self._access.lifecycle_catalog()
        bundle = catalog.get_by_fingerprint(
            bundle_id,
            fingerprint,
            tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
        )
        if bundle is None:
            raise NotFoundError("bundle")
        require_source(auth_context, bundle.descriptor.source_id)
        audit: PublishAuditRecord | None = catalog.publish_audit(
            bundle_id,
            fingerprint,
            tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
        )
        if audit is None:
            raise NotFoundError("publish audit")
        evidence = catalog.verification_evidence(
            bundle_id,
            fingerprint,
            tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
        )
        provenance = audit.assertion_provenance
        return PublishAuditSummary(
            audit_reference=audit.audit_id,
            bundle_id=bundle_id,
            fingerprint=fingerprint,
            approval_count=len(audit.approval_chain),
            accepted_assertion_count=(
                provenance.manual
                + provenance.discovered
                + provenance.inferred
                + provenance.llm_suggested
            ),
            verification_valid=(
                audit.verification.structural_valid
                and audit.verification.manifest_equivalent
            ),
            idempotency_status=audit.idempotency_status.value,
            deployment_binding_count=audit.deployment_bindings.binding_count,
            deployment_reference_schemes=audit.deployment_bindings.reference_schemes,
            waiver_applied=audit.waiver_reference is not None,
            verification=(
                verification_reference(evidence)
                if evidence is not None
                else None
            ),
        )

    @_normalize_errors
    def get_verification_evidence(
        self,
        bundle_id: str,
        fingerprint: str,
        *,
        auth_context: AuthContext,
    ) -> VerificationEvidenceReference:
        """Inspect bounded published verification evidence under trusted scope."""
        require_permission(auth_context, Permission.ASSEMBLY_AUDIT)
        catalog = self._access.lifecycle_catalog()
        bundle = catalog.get_by_fingerprint(
            bundle_id,
            fingerprint,
            tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
        )
        if bundle is None:
            raise NotFoundError("bundle")
        require_source(auth_context, bundle.descriptor.source_id)
        evidence = catalog.verification_evidence(
            bundle_id,
            fingerprint,
            tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
        )
        if evidence is None:
            raise NotFoundError("verification evidence")
        return verification_reference(evidence)

    @_normalize_errors
    def list_published_versions(
        self,
        bundle_id: str,
        *,
        auth_context: AuthContext,
    ) -> VersionListResult:
        require_permission(auth_context, Permission.BUNDLE_READ)
        catalog = self._access.lifecycle_catalog()
        records = catalog.publication_records(
            bundle_id,
            tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
        )
        if records:
            require_source(auth_context, records[0].bundle.descriptor.source_id)
        return VersionListResult(
            bundle_id=bundle_id,
            versions=tuple(
                PublishedVersionItem(
                    bundle_id=bundle_id,
                    model_version=record.bundle.model_version,
                    fingerprint=record.bundle.fingerprint,
                    state=record.state.value,
                    predecessor_fingerprint=record.supersession.predecessor_fingerprint,
                    successor_fingerprint=record.supersession.successor_fingerprint,
                    audit_reference=(record.audit.audit_id if record.audit is not None else None),
                    verification_evidence_reference=(
                        record.audit.verification.evidence_reference
                        if record.audit is not None
                        else None
                    ),
                )
                for record in records
            ),
        )

    @_normalize_errors
    def activate_published_fingerprint(
        self,
        bundle_id: str,
        fingerprint: str,
        *,
        auth_context: AuthContext,
    ) -> BundleLifecycleResult:
        require_permission(auth_context, Permission.BUNDLE_ACTIVATE)
        catalog = self._access.lifecycle_catalog()
        bundle = catalog.get_by_fingerprint(
            bundle_id,
            fingerprint,
            tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
        )
        if bundle is None:
            raise NotFoundError("bundle")
        require_source(auth_context, bundle.descriptor.source_id)
        authorization = lifecycle_context(
            self._deps, auth_context, bundle.descriptor.source_id
        )
        require_lifecycle_authorization(
            context=authorization,
            authorizer=self._access.lifecycle_authorizer(),
            required_role=LifecycleRole.PUBLISHER,
            action=LifecycleAction.ACTIVATE,
            resource_id=bundle_id,
        )
        outcome = catalog.activate_fingerprint(
            bundle_id,
            fingerprint,
            tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
        )
        return _lifecycle_result(
            LifecycleCommand.ACTIVATE,
            bundle_id,
            bundle.model_version,
            outcome,
            auth_context,
            self._deps.audit_reference,
        )

    @_normalize_errors
    def rollback_published_fingerprint(
        self,
        bundle_id: str,
        fingerprint: str,
        *,
        auth_context: AuthContext,
    ) -> BundleLifecycleResult:
        require_permission(auth_context, Permission.BUNDLE_ROLLBACK)
        catalog = self._access.lifecycle_catalog()
        bundle = catalog.get_by_fingerprint(
            bundle_id,
            fingerprint,
            tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
        )
        if bundle is None:
            raise NotFoundError("bundle")
        require_source(auth_context, bundle.descriptor.source_id)
        authorization = lifecycle_context(
            self._deps, auth_context, bundle.descriptor.source_id
        )
        require_lifecycle_authorization(
            context=authorization,
            authorizer=self._access.lifecycle_authorizer(),
            required_role=LifecycleRole.PUBLISHER,
            action=LifecycleAction.ROLLBACK,
            resource_id=bundle_id,
        )
        outcome = catalog.rollback_to_fingerprint(
            bundle_id,
            fingerprint,
            tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
        )
        return _lifecycle_result(
            LifecycleCommand.ROLLBACK,
            bundle_id,
            bundle.model_version,
            outcome,
            auth_context,
            self._deps.audit_reference,
        )

    # ------------------------------------------------------------------
    # bundles
    # ------------------------------------------------------------------
    @_normalize_errors
    def list_bundles(
        self,
        bundle_id: str,
        *,
        auth_context: AuthContext,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult:
        require_permission(auth_context, Permission.BUNDLE_READ)
        catalog = self._access.catalog()
        versions = catalog.versions(
            bundle_id,
            tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
        )
        if versions:
            require_source(auth_context, versions[0].descriptor.source_id)
        active = catalog.active(
            bundle_id,
            tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
        )
        active_fingerprint = active.fingerprint if active else None
        items = [
            BundleListItem(
                bundle_id=b.bundle_id,
                version=b.model_version,
                fingerprint=b.fingerprint,
                status="active" if b.fingerprint == active_fingerprint else "published",
            )
            for b in versions
        ]
        pagination, page_size, start = page_window(
            pagination,
            max_page_size=self._config.max_page_size,
        )
        return PaginatedResult(
            page=pagination.page,
            page_size=page_size,
            total=len(items),
            items=tuple(items[start : start + page_size]),
        )

    @_normalize_errors
    def get_bundle(
        self, bundle_id: str, version: str, *, auth_context: AuthContext
    ) -> BundleDetail:
        require_permission(auth_context, Permission.BUNDLE_READ)
        catalog = self._access.catalog()
        bundle = catalog.get(
            bundle_id,
            version,
            tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
        )
        if bundle is None:
            raise NotFoundError("bundle")
        require_source(auth_context, bundle.descriptor.source_id)
        return _bundle_to_detail(bundle, catalog, auth_context)

    @_normalize_errors
    def validate_bundle(
        self,
        bundle: SemanticModelBundle,
        *,
        auth_context: AuthContext,
    ) -> BundleValidationResult:
        """Validate a bundle with core rules; no publication side effects."""
        require_permission(auth_context, Permission.BUNDLE_VALIDATE)
        require_source(auth_context, bundle.descriptor.source_id)
        result = core_validate_bundle(bundle)
        return BundleValidationResult(
            valid=result.valid,
            issues=tuple(
                ErrorDetail(
                    code=issue.code, message=issue.message, member_id=issue.member_id
                )
                for issue in result.issues
            ),
        )

    @_normalize_errors
    def publish_bundle(
        self,
        bundle: SemanticModelBundle,
        *,
        auth_context: AuthContext,
        idempotency_key: str,
    ) -> BundleLifecycleResult:
        """Validate and publish one immutable Bundle version via the catalog."""
        if not idempotency_key:
            raise ValidationError("idempotency_key is required")
        require_permission(auth_context, Permission.BUNDLE_PUBLISH)
        require_source(auth_context, bundle.descriptor.source_id)
        raise ValidationError(
            "direct Bundle publication is unsupported; publish an approved assembly draft"
        )

    @_normalize_errors
    def lifecycle_command(
        self,
        command: BundleLifecycleCommand,
        *,
        auth_context: AuthContext,
    ) -> BundleLifecycleResult:
        if command.command.value == "publish":
            raise ValidationError("use publish_draft for approved assembly publication")
        if command.command.value == "activate":
            if command.expected_fingerprint is None:
                raise ValidationError("activate requires expected_fingerprint")
            return self.activate_published_fingerprint(
                command.bundle_id,
                command.expected_fingerprint,
                auth_context=auth_context,
            )
        elif command.command.value == "rollback":
            if command.expected_fingerprint is None:
                raise ValidationError("rollback requires expected_fingerprint")
            return self.rollback_published_fingerprint(
                command.bundle_id,
                command.expected_fingerprint,
                auth_context=auth_context,
            )
        else:
            raise ValidationError("unsupported lifecycle command")
