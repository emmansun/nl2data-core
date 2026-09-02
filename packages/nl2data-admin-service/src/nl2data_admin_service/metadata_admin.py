"""Metadata, discovery, proposal, and drift Admin capability service."""

from __future__ import annotations

from datetime import UTC, datetime

from nl2data_core.metadata.models import MetadataSnapshot
from nl2data_core.metadata.proposals import SemanticProposalSet

from .auth import AuthContext, Permission
from .common import (
    AdminDependencyAccess,
    page_window,
    require_permission,
    require_source,
)
from .common import (
    job_record_to_info as _job_record_to_info,
)
from .common import (
    make_job as _make_job,
)
from .common import (
    normalize_errors as _normalize_errors,
)
from .config import AdminServiceConfig
from .dtos import (
    DriftStatus,
    JobInfo,
    JobStatus,
    PaginatedResult,
    PaginationParams,
    ProposalListItem,
    ProposalSetDetail,
    ReviewAction,
    ReviewCommand,
    ReviewResult,
    SnapshotDetail,
)
from .errors import ConflictError, DiscoveryError, NotFoundError, ValidationError
from .protocols import AdminServiceDependencies, MetadataCatalogPort


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


def _assert_expected_fingerprint(expected: str, actual: str) -> None:
    if expected != actual:
        raise ConflictError(f"Expected fingerprint {expected} does not match current {actual}")


class MetadataAdminCapability:
    """Snapshot, discovery/job, proposal review, and drift orchestration."""

    def __init__(self, dependencies: AdminServiceDependencies, config: AdminServiceConfig) -> None:
        self._deps = dependencies
        self._access = AdminDependencyAccess(dependencies)
        self._config = config

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
        require_permission(auth_context, Permission.SNAPSHOT_READ)
        pagination, page_size, _ = page_window(
            pagination,
            max_page_size=self._config.max_page_size,
        )
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
        require_permission(auth_context, Permission.SNAPSHOT_READ)
        catalog = self._access.catalog()
        snapshot = catalog.snapshot(
            snapshot_fingerprint,
            tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
        )
        if snapshot is None:
            raise NotFoundError("snapshot")
        require_source(auth_context, snapshot.source.source_id)
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
        require_permission(auth_context, Permission.SNAPSHOT_READ)
        require_source(auth_context, source_id)
        catalog = self._access.catalog()
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
        require_permission(auth_context, Permission.DISCOVERY_RUN)
        require_source(auth_context, source_id)
        discoverer = self._access.discoverer()
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
            return _make_job(
                "discovery",
                status,
                snapshot.fingerprint if snapshot is not None else None,
            )
        job = self._deps.job_runner.submit(
            "discovery",
            payload={"source_id": source_id},
            auth_context=auth_context,
            idempotency_key=idempotency_key,
        )
        return _job_record_to_info(job)

    @_normalize_errors
    def get_job(self, job_id: str, *, auth_context: AuthContext) -> JobInfo:
        require_permission(auth_context, Permission.JOB_READ)
        if self._deps.job_runner is None:
            raise NotFoundError("job")
        return _job_record_to_info(self._deps.job_runner.status(job_id))

    @_normalize_errors
    def cancel_job(self, job_id: str, *, auth_context: AuthContext) -> JobInfo:
        require_permission(auth_context, Permission.JOB_CANCEL)
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
        require_permission(auth_context, Permission.PROPOSAL_READ)
        catalog = self._access.catalog()
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
        require_permission(auth_context, Permission.PROPOSAL_REVIEW)
        catalog = self._access.catalog()
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
    # drift
    # ------------------------------------------------------------------
    @_normalize_errors
    def get_drift_status(
        self, snapshot_fingerprint: str, *, auth_context: AuthContext
    ) -> DriftStatus:
        require_permission(auth_context, Permission.DRIFT_READ)
        raise NotFoundError("drift decision")

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _require_snapshot_source(
        self,
        catalog: MetadataCatalogPort,
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
        require_source(auth_context, snapshot.source.source_id)
