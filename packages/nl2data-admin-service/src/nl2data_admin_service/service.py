"""Framework-neutral admin service implementation."""

from __future__ import annotations

import functools
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, TypeVar, cast

from nl2data_core.bundles.models import SemanticModelBundle
from nl2data_core.bundles.validation import validate_bundle as core_validate_bundle
from nl2data_core.metadata.models import MetadataSnapshot
from nl2data_core.metadata.proposals import SemanticProposalSet

from .auth import AuthContext, Permission
from .config import AdminServiceConfig
from .dtos import (
    BundleDetail,
    BundleLifecycleCommand,
    BundleLifecycleResult,
    BundleListItem,
    BundleValidationResult,
    CapabilitiesResult,
    Capability,
    DriftStatus,
    ErrorCategory,
    ErrorDetail,
    JobInfo,
    JobStatus,
    LifecycleCommand,
    PaginatedResult,
    PaginationParams,
    ProposalListItem,
    ProposalSetDetail,
    ReviewAction,
    ReviewCommand,
    ReviewResult,
    SnapshotDetail,
)
from .errors import (
    AdminServiceError,
    AuthorizationDeniedError,
    ConflictError,
    DiscoveryError,
    NotFoundError,
    ValidationError,
)
from .protocols import AdminServiceDependencies

_F = TypeVar("_F", bound=Callable[..., Any])


def _normalize_errors(method: _F) -> _F:
    """Convert unexpected exceptions into normalized, bounded service errors.

    Admin service errors pass through unchanged; bounded core validation
    errors become ``validation`` errors; anything else becomes a generic
    ``internal`` error so raw backend exceptions, DSNs, and secrets never
    cross the service boundary.
    """

    @functools.wraps(method)
    def wrapper(self: AdminService, *args: Any, **kwargs: Any) -> Any:
        try:
            return method(self, *args, **kwargs)
        except AdminServiceError:
            raise
        except ValueError as err:
            raise ValidationError((str(err) or "invalid request")[:256]) from err
        except Exception:
            raise AdminServiceError(
                category=ErrorCategory.INTERNAL,
                code="internal_service_error",
                message="Internal service error",
            ) from None

    return cast(_F, wrapper)


class AdminService:
    """Transport-neutral admin service.

    The service validates authorization and scope, delegates to injected
    discoverer/catalog/job runner ports, and returns bounded DTOs.
    """

    def __init__(self, dependencies: AdminServiceDependencies, config: AdminServiceConfig) -> None:
        self._deps = dependencies
        self._config = config

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _require_catalog(self) -> Any:
        catalog = self._deps.catalog
        if catalog is None:
            raise NotFoundError("catalog")
        return catalog

    def _require_discoverer(self) -> Any:
        discoverer = self._deps.discoverer
        if discoverer is None:
            raise DiscoveryError("No metadata discoverer configured")
        return discoverer

    def _check_permission(self, auth_context: AuthContext, permission: Permission) -> None:
        if not auth_context.is_allowed(permission):
            raise AuthorizationDeniedError(f"Missing permission: {permission.value}")

    def _check_source(self, auth_context: AuthContext, source_id: str) -> None:
        """Reject reads/mutations outside the operator's authorized sources."""
        if not auth_context.is_source_allowed(source_id):
            raise AuthorizationDeniedError("Source not authorized")

    def _require_snapshot_source(
        self,
        catalog: Any,
        snapshot_fingerprint: str,
        auth_context: AuthContext,
    ) -> None:
        """Resolve a snapshot and enforce source scope for its proposal set."""
        snapshot = catalog.snapshot(
            snapshot_fingerprint,
            tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
        )
        if snapshot is None:
            raise NotFoundError("snapshot")
        self._check_source(auth_context, snapshot.source.source_id)

    @_normalize_errors
    def capabilities(self) -> CapabilitiesResult:
        """Return the versioned capability listing for the admin service."""
        return CapabilitiesResult(
            version=self._config.contract_version,
            capabilities=tuple(
                [
                    Capability(name="snapshot read", permission=Permission.SNAPSHOT_READ),
                    Capability(name="snapshot list", permission=Permission.SNAPSHOT_READ),
                    Capability(name="active snapshot", permission=Permission.SNAPSHOT_READ),
                    Capability(name="discovery run", permission=Permission.DISCOVERY_RUN),
                    Capability(name="job read", permission=Permission.JOB_READ),
                    Capability(name="job cancel", permission=Permission.JOB_CANCEL),
                    Capability(name="proposal read", permission=Permission.PROPOSAL_READ),
                    Capability(name="proposal review", permission=Permission.PROPOSAL_REVIEW),
                    Capability(name="bundle read", permission=Permission.BUNDLE_READ),
                    Capability(name="bundle validate", permission=Permission.BUNDLE_VALIDATE),
                    Capability(name="bundle publish", permission=Permission.BUNDLE_PUBLISH),
                    Capability(name="bundle activate", permission=Permission.BUNDLE_ACTIVATE),
                    Capability(name="bundle rollback", permission=Permission.BUNDLE_ROLLBACK),
                    Capability(name="drift read", permission=Permission.DRIFT_READ),
                ]
            ),
        )

    # ------------------------------------------------------------------
    # snapshots
    # ------------------------------------------------------------------
    @_normalize_errors
    def list_snapshots(
        self,
        *,
        auth_context: AuthContext,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult:
        self._check_permission(auth_context, Permission.SNAPSHOT_READ)
        if pagination is None:
            pagination = PaginationParams()
        page_size = min(pagination.page_size, self._config.max_page_size)
        # The durable catalog does not expose a list-by-source operation; the
        # service delegates to host-owned discovery/catalog records in practice.
        # For the reference implementation we return a bounded empty list.
        return PaginatedResult(
            page=pagination.page,
            page_size=page_size,
            total=0,
            items=(),
        )

    @_normalize_errors
    def get_snapshot(
        self, snapshot_fingerprint: str, *, auth_context: AuthContext
    ) -> SnapshotDetail:
        self._check_permission(auth_context, Permission.SNAPSHOT_READ)
        catalog = self._require_catalog()
        snapshot = catalog.snapshot(
            snapshot_fingerprint,
            tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
        )
        if snapshot is None:
            raise NotFoundError("snapshot")
        self._check_source(auth_context, snapshot.source.source_id)
        active = catalog.active_snapshot(
            snapshot.source.source_id,
            auth_context.tenant_scope_fingerprint,
        )
        status = (
            "active"
            if active is not None and active.fingerprint == snapshot.fingerprint
            else "retained"
        )
        return _snapshot_to_detail(snapshot, status)

    @_normalize_errors
    def get_active_snapshot(self, source_id: str, *, auth_context: AuthContext) -> SnapshotDetail:
        self._check_permission(auth_context, Permission.SNAPSHOT_READ)
        self._check_source(auth_context, source_id)
        catalog = self._require_catalog()
        snapshot = catalog.active_snapshot(
            source_id,
            auth_context.tenant_scope_fingerprint,
        )
        if snapshot is None:
            raise NotFoundError("active snapshot")
        return _snapshot_to_detail(snapshot, "active")

    # ------------------------------------------------------------------
    # discovery / jobs
    # ------------------------------------------------------------------
    @_normalize_errors
    def submit_discovery(
        self,
        source_id: str,
        *,
        auth_context: AuthContext,
        idempotency_key: str,
    ) -> JobInfo:
        self._check_permission(auth_context, Permission.DISCOVERY_RUN)
        self._check_source(auth_context, source_id)
        discoverer = self._require_discoverer()
        if not discoverer.supports_source(source_id):
            raise DiscoveryError(f"Source not supported: {source_id}")
        if self._deps.job_runner is None:
            try:
                snapshot = discoverer.discover(source_id, auth_context=auth_context)
                if snapshot is not None and self._deps.catalog is not None:
                    self._deps.catalog.register_snapshot(
                        snapshot,
                        tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
                    )
            except Exception:
                raise DiscoveryError("Metadata discovery failed") from None
            status = JobStatus.COMPLETED if snapshot is not None else JobStatus.FAILED
            return _make_job("discovery", status, snapshot)
        job = self._deps.job_runner.submit(
            "discovery",
            payload={"source_id": source_id},
            auth_context=auth_context,
            idempotency_key=idempotency_key,
        )
        return _job_record_to_info(job)

    @_normalize_errors
    def get_job(self, job_id: str, *, auth_context: AuthContext) -> JobInfo:
        self._check_permission(auth_context, Permission.JOB_READ)
        if self._deps.job_runner is None:
            raise NotFoundError("job")
        return _job_record_to_info(self._deps.job_runner.status(job_id))

    @_normalize_errors
    def cancel_job(self, job_id: str, *, auth_context: AuthContext) -> JobInfo:
        self._check_permission(auth_context, Permission.JOB_CANCEL)
        if self._deps.job_runner is None:
            raise NotFoundError("job")
        return _job_record_to_info(self._deps.job_runner.cancel(job_id))

    # ------------------------------------------------------------------
    # proposal sets
    # ------------------------------------------------------------------
    @_normalize_errors
    def get_proposal_set(
        self, snapshot_fingerprint: str, *, auth_context: AuthContext
    ) -> ProposalSetDetail:
        self._check_permission(auth_context, Permission.PROPOSAL_READ)
        catalog = self._require_catalog()
        proposal_set = catalog.proposal_set(
            snapshot_fingerprint,
            tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
        )
        if proposal_set is None:
            raise NotFoundError("proposal set")
        self._require_snapshot_source(catalog, snapshot_fingerprint, auth_context)
        return _proposal_set_to_detail(proposal_set)

    @_normalize_errors
    def review_proposals(
        self,
        snapshot_fingerprint: str,
        command: ReviewCommand,
        *,
        auth_context: AuthContext,
    ) -> ReviewResult:
        self._check_permission(auth_context, Permission.PROPOSAL_REVIEW)
        catalog = self._require_catalog()
        proposal_set = catalog.proposal_set(
            snapshot_fingerprint,
            tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
        )
        if proposal_set is None:
            raise NotFoundError("proposal set")
        self._require_snapshot_source(catalog, snapshot_fingerprint, auth_context)
        _assert_expected_fingerprint(
            command.expected_set_fingerprint,
            proposal_set.evidence_fingerprint_of(),
        )
        if command.action is ReviewAction.APPROVE:
            new_set = proposal_set.approve(command.proposal_ids)
        elif command.action is ReviewAction.REJECT:
            new_set = proposal_set.reject(command.proposal_ids)
        elif command.action is ReviewAction.REVISE:
            if len(command.proposal_ids) != 1:
                raise ValidationError("revise requires exactly one proposal id")
            if command.revision_fact is None:
                raise ValidationError("revise requires a revision_fact")
            new_set = proposal_set.revise(
                command.proposal_ids[0],
                fact=command.revision_fact,
            )
        else:
            raise ValidationError("unsupported review action")
        catalog.save_proposal_set(
            new_set,
            tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
        )
        return ReviewResult(
            snapshot_fingerprint=new_set.snapshot_fingerprint,
            set_fingerprint=new_set.evidence_fingerprint_of(),
            action=command.action,
            reviewed_proposals=command.proposal_ids,
            reviewed_at=datetime.now(UTC),
            audit_reference=auth_context.audit_reference or self._deps.audit_reference,
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
        self._check_permission(auth_context, Permission.BUNDLE_READ)
        catalog = self._require_catalog()
        versions = catalog.versions(
            bundle_id,
            tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
        )
        if versions:
            self._check_source(auth_context, versions[0].descriptor.source_id)
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
        if pagination is None:
            pagination = PaginationParams()
        page_size = min(pagination.page_size, self._config.max_page_size)
        start = pagination.offset
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
        self._check_permission(auth_context, Permission.BUNDLE_READ)
        catalog = self._require_catalog()
        bundle = catalog.get(
            bundle_id,
            version,
            tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
        )
        if bundle is None:
            raise NotFoundError("bundle")
        self._check_source(auth_context, bundle.descriptor.source_id)
        return _bundle_to_detail(bundle, catalog, auth_context)

    @_normalize_errors
    def validate_bundle(
        self,
        bundle: SemanticModelBundle,
        *,
        auth_context: AuthContext,
    ) -> BundleValidationResult:
        """Validate a bundle with core rules; no publication side effects."""
        self._check_permission(auth_context, Permission.BUNDLE_VALIDATE)
        self._check_source(auth_context, bundle.descriptor.source_id)
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
        self._check_permission(auth_context, Permission.BUNDLE_PUBLISH)
        self._check_source(auth_context, bundle.descriptor.source_id)
        catalog = self._require_catalog()
        outcome = catalog.publish(
            bundle,
            tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
        )
        return _lifecycle_result(
            LifecycleCommand.PUBLISH,
            bundle.bundle_id,
            bundle.model_version,
            outcome,
            auth_context,
            self._deps.audit_reference,
        )

    @_normalize_errors
    def lifecycle_command(
        self,
        command: BundleLifecycleCommand,
        *,
        auth_context: AuthContext,
    ) -> BundleLifecycleResult:
        catalog = self._require_catalog()
        if command.command.value == "publish":
            raise ValidationError("use publish_bundle: publish requires a bundle payload")
        if command.command.value == "activate":
            self._check_permission(auth_context, Permission.BUNDLE_ACTIVATE)
            if command.version is None:
                raise ValidationError("activate requires a version")
            bundle = catalog.get(
                command.bundle_id,
                command.version,
                tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
            )
            if bundle is None:
                raise NotFoundError("bundle")
            self._check_source(auth_context, bundle.descriptor.source_id)
            outcome = catalog.activate(
                command.bundle_id,
                command.version,
                tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
            )
        elif command.command.value == "rollback":
            self._check_permission(auth_context, Permission.BUNDLE_ROLLBACK)
            active = catalog.active(
                command.bundle_id,
                tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
            )
            if active is not None:
                self._check_source(auth_context, active.descriptor.source_id)
            outcome = catalog.rollback(
                command.bundle_id,
                tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
            )
        else:
            raise ValidationError("unsupported lifecycle command")
        return _lifecycle_result(
            command.command,
            command.bundle_id,
            command.version,
            outcome,
            auth_context,
            self._deps.audit_reference,
        )

    # ------------------------------------------------------------------
    # drift
    # ------------------------------------------------------------------
    @_normalize_errors
    def get_drift_status(
        self, snapshot_fingerprint: str, *, auth_context: AuthContext
    ) -> DriftStatus:
        self._check_permission(auth_context, Permission.DRIFT_READ)
        raise NotFoundError("drift decision")


# --------------------------------------------------------------------------
# projection helpers
# --------------------------------------------------------------------------
def _snapshot_to_detail(snapshot: MetadataSnapshot, status: str) -> SnapshotDetail:
    return SnapshotDetail(
        snapshot_id=snapshot.snapshot_id,
        source_id=snapshot.source.source_id,
        fingerprint=snapshot.fingerprint,
        discovered_at=snapshot.freshness.discovered_at,
        status=status,
        object_count=len(snapshot.objects),
        relationship_count=len(snapshot.relationships),
        trust_summary=snapshot.objects[0].trust_level.value if snapshot.objects else "",
        provenance_method=snapshot.provenance.method,
    )


def _proposal_set_to_detail(proposal_set: SemanticProposalSet) -> ProposalSetDetail:
    return ProposalSetDetail(
        snapshot_fingerprint=proposal_set.snapshot_fingerprint,
        set_fingerprint=proposal_set.evidence_fingerprint_of(),
        proposal_count=len(proposal_set.proposals),
        reviewed_at=proposal_set.reviewed_at,
        proposals=tuple(
            ProposalListItem(
                proposal_id=p.proposal_id,
                kind=p.kind.value,
                target_id=p.target_id,
                status=p.status.value,
                trust_level=p.trust_level.value,
                method=p.method,
                snapshot_fingerprint=p.snapshot_fingerprint,
            )
            for p in proposal_set.proposals
        ),
    )


def _bundle_to_detail(
    bundle: SemanticModelBundle,
    catalog: Any,
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


def _make_job(command: str, status: JobStatus, snapshot: MetadataSnapshot | None = None) -> JobInfo:
    return JobInfo(
        job_id="sync",
        status=status,
        command=command,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        result_fingerprint=snapshot.fingerprint if snapshot else None,
    )


def _lifecycle_result(
    command: LifecycleCommand,
    bundle_id: str,
    version: str | None,
    outcome: Any,
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


def _job_record_to_info(record: Any) -> JobInfo:
    return JobInfo(
        job_id=record.job_id,
        status=JobStatus(record.status),
        command=record.command,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _assert_expected_fingerprint(expected: str, actual: str) -> None:
    if expected != actual:
        raise ConflictError(f"Expected fingerprint {expected} does not match current {actual}")
