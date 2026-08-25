"""End-to-end production discovery profile tests.

Covers the full governed chain over a real sqlite source snapshot:
discover -> infer -> approve -> convert -> publish/activate Bundle ->
resolve View -> bind IR evidence.  Also verifies that stale
snapshot/bundle/view/IR evidence fails closed before provider or adapter
execution, that snapshot retention/cleanup is host-owned with a manual
Bundle fallback (no distributed metadata registry), and that health and
operational evidence never exposes DSNs, credentials, raw values, or
unrestricted sensitive metadata names.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from nl2data import ErrorCode, OutcomeStatus, QueryContext, QueryRequest
from nl2data_core.adapters.sql.adapter import SqlQueryAdapter
from nl2data_core.adapters.sql.discovery import SqlMetadataDiscoverer
from nl2data_core.ai.fake import FakeModelProvider
from nl2data_core.bundles.catalog import InMemorySemanticBundleCatalog
from nl2data_core.bundles.models import (
    BundleProvenance,
    BundleQualityStatus,
    SemanticModelBundle,
    SemanticSourceReference,
)
from nl2data_core.fixtures import SQLiteFixtureProfile
from nl2data_core.governance.models import PolicyScope
from nl2data_core.metadata import (
    DiscoveryAuthorization,
    DiscoveryOutcome,
    DiscoveryOutcomeCategory,
    ProductionActivationContext,
    ProductionDiscoveryConfig,
    SnapshotActivationPolicy,
    SnapshotLedger,
    convert_approved_proposals,
    discovery_health,
    infer_proposals,
    run_production_discovery,
)
from nl2data_core.metadata.models import MetadataSnapshot
from nl2data_core.metadata.protocol import MetadataDiscoveryConfig
from nl2data_core.planning.models import ColumnBinding, PhysicalBinding
from nl2data_core.planning.validation import AuthorizedView
from nl2data_core.tenancy import (
    EntitlementRevision,
    IsolationProfile,
    SubjectContext,
    TenantContext,
    TenantScopeContext,
)
from nl2data_core.views import (
    ResolutionContext,
    SemanticDescriptor,
    SemanticEntityDescriptor,
    SemanticFieldDescriptor,
    SemanticViewDefinition,
    ViewMemberRestrictions,
    ViewProvenance,
    ViewRegistry,
)
from nl2data_core.workflow.models import WorkflowEvent, WorkflowStage, WorkflowStatus
from nl2data_core.workflow.runner import QueryExecutionRunner, StaticPlanResolver
from nl2data_core.workflow.runtime import DeterministicWorkflowRuntime
from nl2data_core.workflow.sqlite_store import SQLiteStateStore

FIELDS = frozenset({"order_id", "customer_id", "amount", "region", "status", "created_at"})
TENANT_FP = "sha256:" + "11" * 32
IDENTITY_FP = "sha256:" + "22" * 32
PRINCIPAL_FP = "sha256:" + "ff" * 32
ENFORCEMENT = "sha256:" + "e1" * 32

#: Sensitive-name markers matched against discovered member names.  Matched
#: members are counted in evidence but never named.
SENSITIVE_MARKERS = frozenset({"amount"})

INTENT = {
    "intent": {
        "source_id": "sales",
        "root_entity_id": "orders",
        "selections": [
            {"selection_id": "s1", "field_id": "order_id"},
            {"selection_id": "s2", "field_id": "amount"},
        ],
        "filters": [{"filter_id": "f1", "field_id": "region", "operator": "eq", "value": "emea"}],
        "orderings": [{"ordering_id": "o1", "field_id": "order_id", "direction": "desc"}],
        "limit": 10,
        "confidence": 0.95,
    }
}

BINDING = PhysicalBinding(
    object_id="orders",
    dialect="sqlite",
    column_bindings=(
        ColumnBinding(field_id="order_id", physical_name="order_id"),
        ColumnBinding(field_id="customer_id", physical_name="customer_id"),
        ColumnBinding(field_id="amount", physical_name="amount"),
        ColumnBinding(field_id="region", physical_name="region"),
        ColumnBinding(field_id="status", physical_name="status"),
        ColumnBinding(field_id="created_at", physical_name="created_at"),
    ),
)


class CountingAdapter(SqlQueryAdapter):
    """Adapter that counts executions (the external work boundary)."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.executions = 0

    async def execute(self, validated, context):
        self.executions += 1
        return await super().execute(validated, context)


# -- production chain fixtures ---------------------------------------------


def make_tenant_scope(tenant_id: str = "acme") -> TenantScopeContext:
    """A trusted production tenant scope for one fixture tenant."""
    return TenantScopeContext(
        tenant=TenantContext(
            tenant_id=tenant_id,
            environment="prod",
            isolation_profile=IsolationProfile.SCHEMA_ISOLATED,
            enforcement_fingerprint=ENFORCEMENT,
        ),
        subject=SubjectContext(
            principal_id="alice",
            roles=frozenset({"analyst"}),
            entitlement_revision=EntitlementRevision(
                revision_id="rev-1", issued_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
            ),
        ),
    )


def make_production_config(
    *, markers: frozenset[str] = SENSITIVE_MARKERS,
) -> ProductionDiscoveryConfig:
    """Bounded production discovery configuration for the fixture source."""
    return ProductionDiscoveryConfig(
        authorization=DiscoveryAuthorization(
            source_id="sales",
            tenant_scope_fingerprint=TENANT_FP,
            discovery_identity_fingerprint=IDENTITY_FP,
        ),
        bounds=MetadataDiscoveryConfig(
            allowed_objects=frozenset({"orders"}),
            allowed_fields=FIELDS,
            include_statistics=True,
        ),
        sensitive_name_markers=markers,
    )


def make_discoverer(db_path: Path) -> SqlMetadataDiscoverer:
    """Read-only sqlite discoverer bound to the fixture orders table."""
    return SqlMetadataDiscoverer(
        dialect="sqlite",
        db_path=db_path,
        source_id="sales",
        allowed_objects=frozenset({"orders"}),
        allowed_fields=FIELDS,
    )


def provision_db(tmp_path: Path) -> Path:
    """Provision a real sqlite source file with the controlled fixture.

    Only the ``orders`` table remains discoverable so discovery observes a
    complete (unbounded) source snapshot; bounded-omission behavior is
    covered separately by the partial-snapshot test.
    """
    db_path = tmp_path / "sales.db"
    fixture = SQLiteFixtureProfile(db_path=db_path)
    fixture.provision()
    with sqlite3.connect(db_path) as connection:
        connection.execute("DROP TABLE IF EXISTS customers")
        connection.execute("DROP TABLE IF EXISTS _nl2data_fixture_meta")
    return db_path


def make_policy(snapshot: MetadataSnapshot) -> SnapshotActivationPolicy:
    """The bounded activation policy for the fixture source snapshot."""
    return SnapshotActivationPolicy(
        max_age_seconds=3_600.0,
        allow_partial=False,
        compatible_catalog_fingerprints=frozenset(
            {snapshot.source.catalog_fingerprint}
        ),
        tenant_scope_fingerprint=TENANT_FP,
        source_id="sales",
    )


def make_production_context(
    snapshot: MetadataSnapshot,
) -> ProductionActivationContext:
    """Production activation evidence bound to one active snapshot."""
    return ProductionActivationContext(
        snapshot_policy=make_policy(snapshot),
        active_snapshot=snapshot,
        tenant_scope_fingerprint=TENANT_FP,
    )


def bundle_from_snapshot(
    snapshot: MetadataSnapshot, *, version: str = "1.0.0"
) -> SemanticModelBundle:
    """Infer -> approve -> convert -> build a validated bundle input."""
    proposals = infer_proposals(snapshot)
    approved = proposals.approve(
        proposal.proposal_id for proposal in proposals.proposals
    )
    converted = convert_approved_proposals(
        approved, descriptor_id="sales_descriptor", version=1, source_id="sales"
    )
    assert converted is not None
    assert converted.descriptor.catalog_fingerprint == snapshot.fingerprint
    return SemanticModelBundle(
        bundle_id="sales_model",
        model_version=version,
        descriptor=converted.descriptor,
        sources=(
            SemanticSourceReference(
                reference_id="src-sales",
                source_id="sales",
                catalog_fingerprint=snapshot.source.catalog_fingerprint,
            ),
        ),
        provenance=BundleProvenance(
            owner_reference="team-analytics",
            quality=BundleQualityStatus.VALIDATED,
        ),
    )


def activate_in_ledger(
    ledger: SnapshotLedger, snapshot: MetadataSnapshot
) -> None:
    """Register and activate one snapshot under the production policy."""
    ledger.register(snapshot, tenant_scope_fingerprint=TENANT_FP)
    activation = ledger.activate(
        snapshot.fingerprint,
        tenant_scope_fingerprint=TENANT_FP,
        policy=make_policy(snapshot),
    )
    assert activation.activated, activation.reason
    assert ledger.active("sales", TENANT_FP) is not None


def make_policy_scope(scope: TenantScopeContext) -> PolicyScope:
    return PolicyScope(
        policy_id="fixture-policy",
        source_ids=frozenset({"sales"}),
        resource_ids=frozenset({"orders"}),
        operation_ids=frozenset({"select"}),
        field_ids=FIELDS,
        tenant_scope_fingerprint=scope.scope_fingerprint,
        isolation_profile=scope.tenant.isolation_profile.value,
    )


def make_view_definition(
    descriptor: SemanticDescriptor, policy_scope: PolicyScope, scope: TenantScopeContext
) -> SemanticViewDefinition:
    return SemanticViewDefinition(
        view_id="sales_view",
        version=1,
        descriptor_id=descriptor.descriptor_id,
        allowed_purposes=frozenset({"analytics", "ops"}),
        restrictions=ViewMemberRestrictions(
            allowed_operations=frozenset({"select", "order"})
        ),
        bound_tenant_scope_fingerprint=scope.scope_fingerprint,
        bound_policy_fingerprint=policy_scope.policy_fingerprint,
        bound_principal_authorization_fingerprints=frozenset({PRINCIPAL_FP}),
        provenance=ViewProvenance(
            descriptor_fingerprint=descriptor.fingerprint,
            resolver_version=1,
        ),
    )


def make_resolution_context(
    *,
    scope: TenantScopeContext,
    policy_scope: PolicyScope,
    snapshot: MetadataSnapshot,
    bundle: SemanticModelBundle,
    **overrides,
) -> ResolutionContext:
    values = {
        "tenant_scope_fingerprint": scope.scope_fingerprint,
        "principal_authorization_fingerprint": PRINCIPAL_FP,
        "purpose": "analytics",
        "policy_fingerprint": policy_scope.policy_fingerprint,
        "catalog_fingerprint": snapshot.fingerprint,
        "bundle_fingerprint": bundle.fingerprint,
        "snapshot_fingerprint": snapshot.fingerprint,
    }
    values.update(overrides)
    return ResolutionContext(**values)


def make_registry(
    bundle: SemanticModelBundle,
    definition: SemanticViewDefinition,
) -> ViewRegistry:
    """A bundle-backed registry with exactly one view."""
    return ViewRegistry(
        descriptors=[bundle.descriptor],
        views=[definition],
        bundle=bundle,
    )


def make_runtime(
    *,
    db_path: Path,
    projection,
    scope: TenantScopeContext,
    policy_scope: PolicyScope,
    adapter: CountingAdapter,
    state_store: SQLiteStateStore,
    provider: FakeModelProvider | None = None,
) -> DeterministicWorkflowRuntime:
    """A workflow runtime bound to the resolved projection and IR binding."""
    execution = QueryExecutionRunner(
        adapter=adapter,
        policy_scope=policy_scope,
        view=AuthorizedView(
            source_id="sales",
            root_entity_ids=frozenset({"orders"}),
            field_ids=FIELDS,
        ),
        plan_resolver=StaticPlanResolver(None),
        tenant_context=scope,
    )
    return DeterministicWorkflowRuntime(
        provider=provider or FakeModelProvider(default_response=INTENT),
        execution=execution,
        binding=BINDING,
        state_store=state_store,
        projection=projection,
    )


def make_request(request_id: str, workflow_id: str) -> QueryRequest:
    return QueryRequest(
        request_id=request_id,
        prompt="top 10 order amounts in emea",
        context=QueryContext(request_id=request_id, workflow_id=workflow_id),
    )


def make_checkpoint(
    store: SQLiteStateStore,
    *,
    workflow_id: str,
    request_id: str,
    tenant_scope_fingerprint: str,
    **overrides,
) -> None:
    from nl2data_core.workflow.models import WorkflowState

    values = {
        "workflow_id": workflow_id,
        "request_id": request_id,
        "tenant_scope_fingerprint": tenant_scope_fingerprint,
        "status": WorkflowStatus.RUNNING,
        "attempts": 1,
    }
    values.update(overrides)
    store.create(WorkflowState(**values))


class TestProductionChainEndToEnd:
    async def test_discover_infer_approve_convert_activate_resolve_bind_ir(
        self, tmp_path: Path
    ) -> None:
        """The full governed chain over a real sqlite source snapshot."""
        store = SQLiteStateStore(tmp_path / "durable.db")
        try:
            db_path = provision_db(tmp_path)
            result = await run_production_discovery(
                make_discoverer(db_path), make_production_config()
            )
            outcome = result.outcome
            snapshot = result.snapshot
            assert snapshot is not None

            # -- discover: bounded, sensitive members counted but never named
            assert outcome.snapshot_fingerprint == snapshot.fingerprint
            assert outcome.redacted_sensitive_fields >= 1
            payload_text = json.dumps(outcome.safe_payload())
            for name in ("orders", "order_id", "amount", "region", "created_at"):
                assert name not in payload_text

            # -- host-owned ledger: retain + activate under the policy
            ledger = SnapshotLedger()
            ledger.record_outcome(
                outcome, source_id="sales", tenant_scope_fingerprint=TENANT_FP
            )
            activate_in_ledger(ledger, snapshot)

            # -- infer -> approve -> convert -> publish/activate Bundle
            bundle = bundle_from_snapshot(snapshot)
            context = make_production_context(snapshot)
            catalog = InMemorySemanticBundleCatalog()
            published = catalog.publish(bundle, production=context)
            assert published.success, published.issue_codes()
            activated = catalog.activate(
                bundle.bundle_id, bundle.model_version, production=context
            )
            assert activated.success, activated.issue_codes()
            assert catalog.active(bundle.bundle_id) is not None

            # -- resolve View against the active bundle + active snapshot
            scope = make_tenant_scope()
            policy_scope = make_policy_scope(scope)
            definition = make_view_definition(bundle.descriptor, policy_scope, scope)
            registry = make_registry(bundle, definition)
            resolution = registry.resolve(
                "sales_view",
                make_resolution_context(
                    scope=scope,
                    policy_scope=policy_scope,
                    snapshot=snapshot,
                    bundle=bundle,
                ),
            )
            assert resolution.kind == "resolved"
            projection = resolution.projection
            assert projection is not None
            assert projection.source_id == "sales"
            assert projection.bundle_fingerprint == bundle.fingerprint
            assert projection.catalog_fingerprint == snapshot.fingerprint

            # -- bind IR: the view-bound workflow executes and records evidence
            adapter = CountingAdapter(
                dialect="sqlite",
                db_path=db_path,
                allowed_objects=frozenset({"orders"}),
                allowed_columns=FIELDS,
                max_rows=100,
            )
            runtime = make_runtime(
                db_path=db_path,
                projection=projection,
                scope=scope,
                policy_scope=policy_scope,
                adapter=adapter,
                state_store=store,
            )
            assert runtime.view.view_bound
            assert runtime.view.view_id == "sales_view"
            assert runtime.view.view_fingerprint == projection.fingerprint

            workflow_outcome = await runtime.execute(
                make_request("req-prod-e2e", "wf-prod-e2e")
            )
            assert workflow_outcome.status == OutcomeStatus.SUCCEEDED
            assert adapter.executions == 1

            checkpoint = store.get_checkpoint(
                "wf-prod-e2e",
                "req-prod-e2e",
                tenant_scope_fingerprint=scope.scope_fingerprint,
            )
            assert checkpoint is not None
            # For a bound view the semantic fingerprint IS the projection
            # fingerprint; the view key is the composite view identity.
            assert checkpoint.compatibility_fingerprints["semantic"] == projection.fingerprint
            assert checkpoint.compatibility_fingerprints["view"].startswith("sha256:")
            # The execute-stage event carries the resolved-view + IR evidence.
            view_events = [
                event
                for event in checkpoint.events
                if event.metadata.get("view_id") == "sales_view"
            ]
            assert view_events
            assert (
                view_events[-1].metadata.get("view_fingerprint")
                == projection.fingerprint
            )
            assert any(
                event.metadata.get("ir_fingerprint") for event in checkpoint.events
            )

            # -- health evidence stays safe and healthy
            health = discovery_health(
                ledger, source_id="sales", tenant_scope_fingerprint=TENANT_FP
            )
            assert health.healthy
            health_text = json.dumps(health.safe_payload())
            for name in ("orders", "order_id", "amount", "region", "created_at"):
                assert name not in health_text
        finally:
            store.close()


class TestStaleEvidenceFailsClosed:
    async def test_stale_snapshot_blocks_resolution(self, tmp_path: Path) -> None:
        """A trusted snapshot fingerprint different from the active bundle
        source snapshot fails resolution before any work happens."""
        db_path = provision_db(tmp_path)
        result = await run_production_discovery(
            make_discoverer(db_path), make_production_config()
        )
        snapshot = result.snapshot
        assert snapshot is not None
        bundle = bundle_from_snapshot(snapshot)
        scope = make_tenant_scope()
        policy_scope = make_policy_scope(scope)
        definition = make_view_definition(bundle.descriptor, policy_scope, scope)
        registry = make_registry(bundle, definition)

        stale = registry.resolve(
            "sales_view",
            make_resolution_context(
                scope=scope,
                policy_scope=policy_scope,
                snapshot=snapshot,
                bundle=bundle,
                snapshot_fingerprint="sha256:" + "99" * 32,
            ),
        )
        assert stale.kind == "unavailable"
        assert stale.issues[0].code == "snapshot_stale"

    async def test_stale_bundle_fingerprint_blocks_resolution(
        self, tmp_path: Path
    ) -> None:
        """A trusted bundle fingerprint different from the active bundle is
        refused before any member projection."""
        db_path = provision_db(tmp_path)
        result = await run_production_discovery(
            make_discoverer(db_path), make_production_config()
        )
        snapshot = result.snapshot
        assert snapshot is not None
        bundle = bundle_from_snapshot(snapshot)
        scope = make_tenant_scope()
        policy_scope = make_policy_scope(scope)
        definition = make_view_definition(bundle.descriptor, policy_scope, scope)
        registry = make_registry(bundle, definition)

        stale = registry.resolve(
            "sales_view",
            make_resolution_context(
                scope=scope,
                policy_scope=policy_scope,
                snapshot=snapshot,
                bundle=bundle,
                bundle_fingerprint="sha256:" + "88" * 32,
            ),
        )
        assert stale.kind == "unavailable"
        assert stale.issues[0].code == "bundle_stale"

    async def test_stale_snapshot_blocks_activation_preserving_active(
        self, tmp_path: Path
    ) -> None:
        """A bundle built from a newer snapshot cannot activate while the
        production context still references the older active snapshot."""
        db_path = provision_db(tmp_path)
        first = await run_production_discovery(
            make_discoverer(db_path), make_production_config()
        )
        snapshot_v1 = first.snapshot
        assert snapshot_v1 is not None
        bundle_v1 = bundle_from_snapshot(snapshot_v1, version="1.0.0")
        context_v1 = make_production_context(snapshot_v1)
        catalog = InMemorySemanticBundleCatalog()
        assert catalog.publish(bundle_v1, production=context_v1).success
        assert catalog.activate(
            bundle_v1.bundle_id, bundle_v1.model_version, production=context_v1
        ).success

        # A fresh discovery run observes a changed catalog: one more order
        # row moves the row-count statistic (the schema stays the same), so
        # the new snapshot gets a different fingerprint.
        with sqlite3.connect(db_path) as connection:
            connection.execute(
                "INSERT INTO orders (order_id, customer_id, amount, region, status, created_at)"
                " VALUES (99, 1, 5.0, 'emea', 1, '2026-01-25')"
            )
        second = await run_production_discovery(
            make_discoverer(db_path), make_production_config()
        )
        snapshot_v2 = second.snapshot
        assert snapshot_v2 is not None
        assert snapshot_v2.fingerprint != snapshot_v1.fingerprint
        bundle_v2 = bundle_from_snapshot(snapshot_v2, version="1.1.0")

        # Activating v2 under the *older* context fails closed; v1 stays active.
        rejected = catalog.publish(bundle_v2, production=context_v1)
        assert not rejected.success
        assert "snapshot_stale" in rejected.issue_codes()
        active = catalog.active(bundle_v1.bundle_id)
        assert active is not None
        assert active.fingerprint == bundle_v1.fingerprint

        # Under the fresh context the same bundle activates normally.
        context_v2 = make_production_context(snapshot_v2)
        assert catalog.publish(bundle_v2, production=context_v2).success
        assert catalog.activate(
            bundle_v2.bundle_id, bundle_v2.model_version, production=context_v2
        ).success
        assert catalog.active(bundle_v1.bundle_id) is not None

    async def test_stale_view_checkpoint_rejects_resume_before_adapter(
        self, tmp_path: Path
    ) -> None:
        """A checkpoint recorded under a different view cannot resume; the
        adapter never executes."""
        store = SQLiteStateStore(tmp_path / "durable.db")
        try:
            db_path = provision_db(tmp_path)
            result = await run_production_discovery(
                make_discoverer(db_path), make_production_config()
            )
            snapshot = result.snapshot
            assert snapshot is not None
            bundle = bundle_from_snapshot(snapshot)
            scope = make_tenant_scope()
            policy_scope = make_policy_scope(scope)
            definition = make_view_definition(bundle.descriptor, policy_scope, scope)
            registry = make_registry(bundle, definition)
            resolution = registry.resolve(
                "sales_view",
                make_resolution_context(
                    scope=scope,
                    policy_scope=policy_scope,
                    snapshot=snapshot,
                    bundle=bundle,
                ),
            )
            assert resolution.kind == "resolved"
            projection = resolution.projection
            assert projection is not None

            make_checkpoint(
                store,
                workflow_id="wf-stale-view",
                request_id="req-stale-view",
                tenant_scope_fingerprint=scope.scope_fingerprint,
                compatibility_fingerprints={"view": "sha256:" + "c1" * 32},
            )
            adapter = CountingAdapter(
                dialect="sqlite",
                db_path=db_path,
                allowed_objects=frozenset({"orders"}),
                allowed_columns=FIELDS,
                max_rows=100,
            )
            runtime = make_runtime(
                db_path=db_path,
                projection=projection,
                scope=scope,
                policy_scope=policy_scope,
                adapter=adapter,
                state_store=store,
            )
            outcome = await runtime.execute(
                make_request("req-stale-view", "wf-stale-view")
            )
            assert outcome.status == OutcomeStatus.REJECTED
            assert outcome.error is not None
            assert outcome.error.code == ErrorCode.STALE_CHECKPOINT
            assert outcome.error.details["key"] == "view"
            assert adapter.executions == 0
        finally:
            store.close()

    async def test_stale_ir_evidence_rejects_before_adapter_execution(
        self, tmp_path: Path
    ) -> None:
        """A checkpoint whose IR derivation changed is refused at EXECUTE
        before any adapter work."""
        store = SQLiteStateStore(tmp_path / "durable.db")
        try:
            db_path = provision_db(tmp_path)
            result = await run_production_discovery(
                make_discoverer(db_path), make_production_config()
            )
            snapshot = result.snapshot
            assert snapshot is not None
            bundle = bundle_from_snapshot(snapshot)
            scope = make_tenant_scope()
            policy_scope = make_policy_scope(scope)
            definition = make_view_definition(bundle.descriptor, policy_scope, scope)
            registry = make_registry(bundle, definition)
            resolution = registry.resolve(
                "sales_view",
                make_resolution_context(
                    scope=scope,
                    policy_scope=policy_scope,
                    snapshot=snapshot,
                    bundle=bundle,
                ),
            )
            assert resolution.kind == "resolved"
            projection = resolution.projection
            assert projection is not None

            make_checkpoint(
                store,
                workflow_id="wf-stale-ir",
                request_id="req-stale-ir",
                tenant_scope_fingerprint=scope.scope_fingerprint,
                current_stage=WorkflowStage.INTENT,
                events=(
                    WorkflowEvent(
                        event_id="ev-original",
                        workflow_id="wf-stale-ir",
                        from_status=WorkflowStatus.RUNNING,
                        to_status=WorkflowStatus.RUNNING,
                        metadata={
                            "ir_version": "1",
                            "ir_fingerprint": "sha256:" + "b0" * 32,
                        },
                    ),
                ),
            )
            adapter = CountingAdapter(
                dialect="sqlite",
                db_path=db_path,
                allowed_objects=frozenset({"orders"}),
                allowed_columns=FIELDS,
                max_rows=100,
            )
            provider = FakeModelProvider(default_response=INTENT)
            runtime = make_runtime(
                db_path=db_path,
                projection=projection,
                scope=scope,
                policy_scope=policy_scope,
                adapter=adapter,
                state_store=store,
                provider=provider,
            )
            outcome = await runtime.execute(
                make_request("req-stale-ir", "wf-stale-ir")
            )
            assert outcome.status == OutcomeStatus.REJECTED
            assert outcome.error is not None
            assert outcome.error.code == ErrorCode.STALE_CHECKPOINT
            assert adapter.executions == 0  # refused before any adapter work
            assert provider.call_count == 1  # intent is re-derived on resume
        finally:
            store.close()


class TestHostOwnedRetentionAndFallback:
    async def test_cleanup_expires_retained_and_active_snapshots(
        self, tmp_path: Path
    ) -> None:
        """Retention is host-owned: expired records are removed entirely and
        an expired active snapshot stops resolving until rediscovered."""
        db_path = provision_db(tmp_path)
        result = await run_production_discovery(
            make_discoverer(db_path), make_production_config()
        )
        snapshot = result.snapshot
        assert snapshot is not None

        ledger = SnapshotLedger()
        ledger.register(
            snapshot,
            tenant_scope_fingerprint=TENANT_FP,
            retained_for_seconds=10.0,
        )
        assert ledger.activate(
            snapshot.fingerprint, tenant_scope_fingerprint=TENANT_FP
        ).activated
        assert ledger.active("sales", TENANT_FP) is not None

        later = snapshot.freshness.discovered_at + timedelta(seconds=60)
        assert ledger.cleanup_expired(now=later) == 1
        assert ledger.active("sales", TENANT_FP) is None
        assert ledger.records() == ()

    async def test_partial_snapshot_registers_but_never_activates(
        self, tmp_path: Path
    ) -> None:
        """Bounded/partial snapshots are retained as evidence but never
        activate by default."""
        db_path = provision_db(tmp_path)
        config = make_production_config()
        config = ProductionDiscoveryConfig(
            authorization=config.authorization,
            bounds=MetadataDiscoveryConfig(
                allowed_objects=frozenset({"orders"}),
                allowed_fields=FIELDS,
                max_fields_per_object=1,
                include_statistics=True,
            ),
            sensitive_name_markers=SENSITIVE_MARKERS,
        )
        result = await run_production_discovery(make_discoverer(db_path), config)
        assert result.outcome.outcome is DiscoveryOutcomeCategory.PARTIAL
        snapshot = result.snapshot
        assert snapshot is not None

        ledger = SnapshotLedger()
        ledger.register(snapshot, tenant_scope_fingerprint=TENANT_FP)
        activation = ledger.activate(
            snapshot.fingerprint, tenant_scope_fingerprint=TENANT_FP
        )
        assert not activation.activated
        assert activation.reason == "snapshot_partial"
        assert ledger.active("sales", TENANT_FP) is None

    def test_manual_bundle_fallback_without_ledger_or_registry(self) -> None:
        """A manually curated Bundle publishes and activates without any
        discovery snapshot, ledger, or distributed metadata registry."""
        descriptor = SemanticDescriptor(
            descriptor_id="manual_descriptor",
            version=1,
            source_id="sales",
            catalog_fingerprint=None,
            entities=(
                SemanticEntityDescriptor(
                    entity_id="orders",
                    label="Orders",
                    fields=(
                        SemanticFieldDescriptor(
                            field_id="order_id",
                            label="Order id",
                            data_type="identifier",
                        ),
                        SemanticFieldDescriptor(
                            field_id="amount",
                            label="Order amount",
                            data_type="number",
                            allowed_aggregations=frozenset({"sum", "avg"}),
                        ),
                    ),
                ),
            ),
        )
        bundle = SemanticModelBundle(
            bundle_id="manual_model",
            model_version="1.0.0",
            descriptor=descriptor,
            sources=(
                SemanticSourceReference(
                    reference_id="src-manual",
                    source_id="sales",
                    catalog_fingerprint=None,
                ),
            ),
            provenance=BundleProvenance(
                owner_reference="team-analytics",
                quality=BundleQualityStatus.VALIDATED,
            ),
        )
        catalog = InMemorySemanticBundleCatalog()
        # No production context: the manual fallback path needs no snapshot.
        assert catalog.publish(bundle).success
        assert catalog.activate(bundle.bundle_id, bundle.model_version).success

    async def test_failure_never_replaces_active_snapshot(self, tmp_path: Path) -> None:
        """A failed discovery run records a failure outcome but the active
        snapshot keeps resolving; health turns unhealthy for review."""
        db_path = provision_db(tmp_path)
        result = await run_production_discovery(
            make_discoverer(db_path), make_production_config()
        )
        snapshot = result.snapshot
        assert snapshot is not None
        ledger = SnapshotLedger()
        ledger.record_outcome(
            result.outcome, source_id="sales", tenant_scope_fingerprint=TENANT_FP
        )
        activate_in_ledger(ledger, snapshot)
        active_before = ledger.active("sales", TENANT_FP)
        assert active_before is not None
        assert active_before.fingerprint == snapshot.fingerprint

        # A later unauthorized run fails; the active snapshot is preserved.
        denied = await run_production_discovery(
            SqlMetadataDiscoverer(
                dialect="sqlite",
                db_path=db_path,
                source_id="sales",
                allowed_objects=frozenset(),
            ),
            make_production_config(),
        )
        assert denied.outcome.outcome is DiscoveryOutcomeCategory.UNAUTHORIZED
        ledger.record_outcome(
            denied.outcome, source_id="sales", tenant_scope_fingerprint=TENANT_FP
        )
        assert ledger.active("sales", TENANT_FP) is not None
        assert ledger.active("sales", TENANT_FP) is active_before

        health = discovery_health(
            ledger, source_id="sales", tenant_scope_fingerprint=TENANT_FP
        )
        assert not health.healthy
        assert health.last_outcome is DiscoveryOutcomeCategory.UNAUTHORIZED


class TestSafeOperationalEvidence:
    async def test_unauthorized_discovery_normalizes_without_leakage(
        self, tmp_path: Path
    ) -> None:
        """Unauthorized discovery becomes a bounded safe outcome; driver
        text, DSNs, and raw errors never cross the boundary."""
        db_path = provision_db(tmp_path)
        denied = await run_production_discovery(
            SqlMetadataDiscoverer(
                dialect="sqlite",
                db_path=db_path,
                source_id="sales",
                allowed_objects=frozenset(),
            ),
            make_production_config(),
        )
        assert denied.outcome.outcome is DiscoveryOutcomeCategory.UNAUTHORIZED
        assert denied.outcome.error_category == "unauthorized"
        assert denied.snapshot is None
        payload_text = json.dumps(denied.outcome.safe_payload())
        for material in ("no objects are authorized", "sqlite", "sales.db", "Traceback"):
            assert material not in payload_text

    async def test_health_evidence_reports_failure_without_names(
        self, tmp_path: Path
    ) -> None:
        """Health evidence after a failure carries the bounded category and
        never member names or raw values."""
        ledger = SnapshotLedger()
        ledger.record_outcome(
            DiscoveryOutcome(
                outcome=DiscoveryOutcomeCategory.UNAVAILABLE,
                object_count=0,
                field_count=0,
                statistic_count=0,
                duration_seconds=0.0,
                error_category="unavailable",
            ),
            source_id="sales",
            tenant_scope_fingerprint=TENANT_FP,
        )
        health = discovery_health(
            ledger, source_id="sales", tenant_scope_fingerprint=TENANT_FP
        )
        assert not health.healthy
        assert health.last_outcome is DiscoveryOutcomeCategory.UNAVAILABLE
        assert health.last_error_category == "unavailable"
        assert health.snapshot_fingerprint is None
        payload_text = json.dumps(health.safe_payload())
        for name in ("orders", "order_id", "amount", "region", "created_at"):
            assert name not in payload_text
