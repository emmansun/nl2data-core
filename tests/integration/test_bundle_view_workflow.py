"""Integration tests for bundle-backed Semantic View resolution.

Covers the bundle-to-View-to-IR binding end to end: the catalog's active
validated snapshot resolves with bundle identity in projection and
provenance, bundle scope is required and fails closed, activation and
rollback invalidate resolved-view, IR, and workflow checkpoint evidence
before adapter execution, and Memory records recorded under one active
bundle are revalidated as stale when the active bundle changes - before
the model provider is ever invoked.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from nl2data import ErrorCode, OutcomeStatus, QueryContext, QueryRequest
from nl2data.errors import ErrorCategory, NL2DataError
from nl2data_core.adapters.sql.adapter import SqlQueryAdapter
from nl2data_core.ai.context import SemanticReference
from nl2data_core.ai.fake import FakeModelProvider
from nl2data_core.ai.models import StructuredIntent
from nl2data_core.ai.plan_builder import build_ir_from_intent
from nl2data_core.bundles import (
    BundleProvenance,
    BundleQualityStatus,
    InMemorySemanticBundleCatalog,
    SemanticModelBundle,
    SemanticSourceReference,
)
from nl2data_core.canonical import sha256_fingerprint
from nl2data_core.fixtures import SQLiteFixtureProfile
from nl2data_core.governance.models import PolicyScope
from nl2data_core.memory.inmemory import InMemoryMemoryProvider
from nl2data_core.planning.ir.models import IRViewReference
from nl2data_core.planning.ir.validation import validate_ir
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
    CalculatedField,
    ExprNode,
    ResolutionContext,
    ResolvedViewProjection,
    SemanticDescriptor,
    SemanticEntityDescriptor,
    SemanticFieldDescriptor,
    SemanticViewDefinition,
    ViewMemberRestrictions,
    ViewProvenance,
    ViewRegistry,
)
from nl2data_core.workflow.models import WorkflowStage, WorkflowState, WorkflowStatus
from nl2data_core.workflow.runner import QueryExecutionRunner, StaticPlanResolver
from nl2data_core.workflow.runtime import DeterministicWorkflowRuntime
from nl2data_core.workflow.sqlite_store import SQLiteStateStore

FIELDS = frozenset({"order_id", "customer_id", "amount", "region", "status", "created_at"})

#: (field id, data type) pairs for the fixture semantic descriptor.
VIEW_FIELD_TYPES = (
    ("order_id", "identifier"),
    ("customer_id", "identifier"),
    ("amount", "number"),
    ("region", "text"),
    ("status", "text"),
    ("created_at", "datetime"),
)

REFERENCES = {
    "order_id": SemanticReference(field_id="order_id", label="Order id"),
    "amount": SemanticReference(
        field_id="amount",
        label="Order amount",
        allowed_aggregations=frozenset({"sum", "avg", "min", "max"}),
    ),
    "region": SemanticReference(field_id="region", label="Region"),
    "status": SemanticReference(field_id="status", label="Status"),
    "created_at": SemanticReference(field_id="created_at", label="Created at"),
}

BINDING = PhysicalBinding(
    object_id="orders",
    dialect="sqlite",
    column_bindings=(
        ColumnBinding(field_id="order_id", physical_name="order_id"),
        ColumnBinding(field_id="amount", physical_name="amount"),
        ColumnBinding(field_id="region", physical_name="region"),
        ColumnBinding(field_id="status", physical_name="status"),
        ColumnBinding(field_id="created_at", physical_name="created_at"),
    ),
)

VALID_INTENT = {
    "intent": {
        "source_id": "sales",
        "root_entity_id": "order",
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

FIXED_ISSUED = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
ENFORCEMENT = "sha256:" + "e1" * 32


def fp(byte: str) -> str:
    """A valid ``sha256:<hex>`` fingerprint filled with one repeated byte."""
    return "sha256:" + byte * 32


class CountingAdapter(SqlQueryAdapter):
    """Adapter that counts executions (the external work boundary)."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.executions = 0

    async def execute(self, validated, context):
        self.executions += 1
        return await super().execute(validated, context)


class RetryableFailingAdapter(CountingAdapter):
    """An adapter that always fails with a retryable structured error."""

    async def execute(self, validated, context):
        self.executions += 1
        raise NL2DataError(
            ErrorCategory.ADAPTER,
            ErrorCode.SQL_EXECUTION_FAILED,
            "fixture adapter failure",
            retryable=True,
        )


# -- Semantic View fixtures -----------------------------------------------


def make_field(field_id: str, data_type: str, **overrides) -> SemanticFieldDescriptor:
    values = {
        "field_id": field_id,
        "label": field_id.replace("_", " ").title(),
        "data_type": data_type,
        "allowed_aggregations": (
            frozenset({"sum", "avg", "min", "max"}) if data_type == "number" else frozenset()
        ),
    }
    values.update(overrides)
    return SemanticFieldDescriptor(**values)


def make_descriptor(**overrides) -> SemanticDescriptor:
    values = {
        "descriptor_id": "sales_descriptor",
        "version": 1,
        "source_id": "sales",
        "catalog_fingerprint": fp("ab"),
        "entities": (
            SemanticEntityDescriptor(
                entity_id="order",
                label="Order",
                fields=tuple(
                    make_field(field_id, data_type) for field_id, data_type in VIEW_FIELD_TYPES
                ),
            ),
        ),
    }
    values.update(overrides)
    return SemanticDescriptor(**values)


def make_calculated_field(**overrides) -> CalculatedField:
    values = {
        "name": "double_amount",
        "label": "Double amount",
        "expression": ExprNode(
            op="mul",
            left=ExprNode(op="field", field_id="amount"),
            right=ExprNode(op="const", const=2),
        ),
        "output_type": "float",
        "requires": ("amount",),
    }
    values.update(overrides)
    return CalculatedField(**values)


def make_view_definition(
    *,
    descriptor: SemanticDescriptor,
    tenant_scope_fingerprint: str | None,
    policy_fingerprint: str | None,
    **overrides,
) -> SemanticViewDefinition:
    values = {
        "view_id": "sales_view",
        "version": 1,
        "descriptor_id": descriptor.descriptor_id,
        "allowed_purposes": frozenset({"analytics", "ops"}),
        "restrictions": ViewMemberRestrictions(
            allowed_operations=frozenset({"select", "order"})
        ),
        "bound_tenant_scope_fingerprint": tenant_scope_fingerprint,
        "bound_policy_fingerprint": policy_fingerprint,
        "bound_principal_authorization_fingerprints": frozenset({fp("ff")}),
        "provenance": ViewProvenance(
            descriptor_fingerprint=descriptor.fingerprint,
            resolver_version=1,
        ),
    }
    values.update(overrides)
    return SemanticViewDefinition(**values)


def make_resolution_context(
    *, scope: TenantScopeContext, policy_scope: PolicyScope, purpose: str, **overrides
) -> ResolutionContext:
    values = {
        "tenant_scope_fingerprint": scope.scope_fingerprint,
        "principal_authorization_fingerprint": fp("ff"),
        "purpose": purpose,
        "policy_fingerprint": policy_scope.policy_fingerprint,
        "catalog_fingerprint": fp("ab"),
    }
    values.update(overrides)
    return ResolutionContext(**values)


# -- bundle fixtures -------------------------------------------------------


def make_bundle(
    *, descriptor: SemanticDescriptor, version: str = "1.0.0", **overrides
) -> SemanticModelBundle:
    values = {
        "bundle_id": "sales_model",
        "model_version": version,
        "descriptor": descriptor,
        "sources": (
            SemanticSourceReference(
                reference_id="src-sales", source_id="sales", catalog_fingerprint=fp("ab")
            ),
        ),
        "provenance": BundleProvenance(
            owner_reference="team-analytics",
            quality=BundleQualityStatus.VALIDATED,
        ),
    }
    values.update(overrides)
    return SemanticModelBundle(**values)


def make_catalog_with_active(bundle: SemanticModelBundle) -> InMemorySemanticBundleCatalog:
    catalog = InMemorySemanticBundleCatalog()
    assert catalog.publish(bundle).success
    assert catalog.activate(bundle.bundle_id, bundle.model_version).success
    return catalog


def registry_from_active(
    catalog: InMemorySemanticBundleCatalog,
    *,
    descriptor: SemanticDescriptor,
    scope: TenantScopeContext,
    policy_scope: PolicyScope,
) -> ViewRegistry:
    bundle = catalog.active("sales_model")
    assert bundle is not None
    definition = make_view_definition(
        descriptor=descriptor,
        tenant_scope_fingerprint=scope.scope_fingerprint,
        policy_fingerprint=policy_scope.policy_fingerprint,
    )
    return ViewRegistry(descriptors=[descriptor], views=[definition], bundle=bundle)


def resolve_active(
    registry: ViewRegistry,
    *,
    catalog: InMemorySemanticBundleCatalog,
    scope: TenantScopeContext,
    policy_scope: PolicyScope,
    purpose: str,
) -> ResolvedViewProjection:
    bundle = catalog.active("sales_model")
    assert bundle is not None
    context = make_resolution_context(
        scope=scope,
        policy_scope=policy_scope,
        purpose=purpose,
        bundle_fingerprint=bundle.fingerprint,
    )
    outcome = registry.resolve("sales_view", context)
    assert outcome.kind == "resolved", outcome.issue_codes()
    assert outcome.projection is not None
    return outcome.projection


# -- governed workflow fixtures -------------------------------------------


def make_policy_scope(**overrides) -> PolicyScope:
    values = {
        "policy_id": "fixture-policy",
        "source_ids": frozenset({"sales"}),
        "resource_ids": frozenset({"orders"}),
        "operation_ids": frozenset({"select"}),
        "field_ids": FIELDS,
    }
    values.update(overrides)
    return PolicyScope(**values)


def make_view(**overrides) -> AuthorizedView:
    values = {
        "source_id": "sales",
        "root_entity_ids": frozenset({"order"}),
        "field_ids": FIELDS,
    }
    values.update(overrides)
    return AuthorizedView(**values)


def make_adapter(tmp_path: Path, cls=CountingAdapter) -> CountingAdapter:
    return cls(
        dialect="sqlite",
        db_path=tmp_path / "fixture.db",
        allowed_objects=frozenset({"orders"}),
        allowed_columns=FIELDS,
        max_rows=100,
    )


def make_tenant_scope(tenant_id: str = "acme", **overrides) -> TenantScopeContext:
    values = {
        "tenant": TenantContext(
            tenant_id=tenant_id,
            environment="prod",
            isolation_profile=IsolationProfile.SCHEMA_ISOLATED,
            enforcement_fingerprint=ENFORCEMENT,
        ),
        "subject": SubjectContext(
            principal_id="alice",
            roles=frozenset({"analyst"}),
            entitlement_revision=EntitlementRevision(
                revision_id="rev-1", issued_at=FIXED_ISSUED
            ),
        ),
    }
    values.update(overrides)
    return TenantScopeContext(**values)


def make_execution(
    tmp_path: Path,
    *,
    adapter: CountingAdapter,
    tenant_scope: TenantScopeContext | None = None,
    **overrides,
) -> QueryExecutionRunner:
    fixture = SQLiteFixtureProfile(db_path=tmp_path / "fixture.db")
    fixture.provision()
    values = {
        "adapter": adapter,
        "policy_scope": make_policy_scope(),
        "view": make_view(),
        "plan_resolver": StaticPlanResolver(None),
    }
    if tenant_scope is not None:
        values["tenant_context"] = tenant_scope
        values["policy_scope"] = make_policy_scope(
            tenant_scope_fingerprint=tenant_scope.scope_fingerprint,
            isolation_profile=tenant_scope.tenant.isolation_profile.value,
        )
    values.update(overrides)
    return QueryExecutionRunner(**values)


def make_runtime(
    tmp_path: Path,
    *,
    execution: QueryExecutionRunner,
    state_store: SQLiteStateStore | None = None,
    provider: FakeModelProvider | None = None,
    **overrides,
) -> DeterministicWorkflowRuntime:
    values = {
        "provider": provider or FakeModelProvider(default_response=VALID_INTENT),
        "execution": execution,
        "semantic_references": REFERENCES,
        "binding": BINDING,
        "state_store": state_store,
    }
    values.update(overrides)
    return DeterministicWorkflowRuntime(**values)


def make_bundle_runtime(
    tmp_path: Path,
    *,
    adapter: CountingAdapter,
    scope: TenantScopeContext,
    purpose: str,
    catalog: InMemorySemanticBundleCatalog,
    descriptor: SemanticDescriptor,
    state_store: SQLiteStateStore | None = None,
    provider: FakeModelProvider | None = None,
    memory=None,
) -> tuple[DeterministicWorkflowRuntime, ResolvedViewProjection]:
    """A view-bound runtime resolving the catalog's active bundle snapshot."""
    execution = make_execution(tmp_path, adapter=adapter, tenant_scope=scope)
    policy_scope = execution.policy_scope
    assert policy_scope is not None
    registry = registry_from_active(
        catalog, descriptor=descriptor, scope=scope, policy_scope=policy_scope
    )
    projection = resolve_active(
        registry, catalog=catalog, scope=scope, policy_scope=policy_scope, purpose=purpose
    )
    runtime = make_runtime(
        tmp_path,
        execution=execution,
        state_store=state_store,
        provider=provider,
        memory=memory,
        projection=projection,
    )
    return runtime, projection


class TestCalculatedFieldAuthorization:
    def test_runtime_rejects_definition_not_anchored_by_projection(
        self, tmp_path: Path
    ) -> None:
        adapter = make_adapter(tmp_path)
        projection = ResolvedViewProjection.model_validate(
            {
                "view_id": "sales_view",
                "view_version": 1,
                "descriptor_id": "sales_descriptor",
                "source_id": "sales",
                "root_entity_ids": frozenset({"order"}),
                "field_ids": FIELDS,
                "entities": (),
                "provenance": ViewProvenance(
                    descriptor_fingerprint=fp("ab"), resolver_version=1
                ),
            }
        )
        with pytest.raises(ValueError, match="authorized projection"):
            make_runtime(
                tmp_path,
                execution=make_execution(tmp_path, adapter=adapter),
                projection=projection,
                calculated_fields=(make_calculated_field(),),
            )

    def test_runtime_rejects_same_name_with_different_definition(
        self, tmp_path: Path
    ) -> None:
        calculated = make_calculated_field()
        descriptor = make_descriptor(
            entities=(
                SemanticEntityDescriptor(
                    entity_id="order",
                    label="Order",
                    fields=tuple(
                        make_field(
                            field_id,
                            "float" if field_id == "amount" else data_type,
                        )
                        for field_id, data_type in VIEW_FIELD_TYPES
                    ),
                    calculated_fields=(calculated,),
                ),
            )
        )
        scope = make_tenant_scope()
        adapter = make_adapter(tmp_path)
        execution = make_execution(tmp_path, adapter=adapter, tenant_scope=scope)
        bundle = make_bundle(descriptor=descriptor)
        catalog = make_catalog_with_active(bundle)
        registry = registry_from_active(
            catalog,
            descriptor=descriptor,
            scope=scope,
            policy_scope=execution.policy_scope,
        )
        projection = resolve_active(
            registry,
            catalog=catalog,
            scope=scope,
            policy_scope=execution.policy_scope,
            purpose="analytics",
        )
        tampered = make_calculated_field(
            expression=ExprNode(
                op="mul",
                left=ExprNode(op="field", field_id="amount"),
                right=ExprNode(op="const", const=3),
            )
        )
        with pytest.raises(ValueError, match="authorized projection"):
            make_runtime(
                tmp_path,
                execution=execution,
                projection=projection,
                calculated_fields=(tampered,),
            )


def request(request_id: str, workflow_id: str) -> QueryRequest:
    return QueryRequest(
        request_id=request_id,
        prompt="top 10 order amounts in emea",
        context=QueryContext(request_id=request_id, workflow_id=workflow_id),
    )


def memory_request(request_id: str, prompt: str, workflow_id: str) -> QueryRequest:
    context = QueryContext(
        request_id=request_id, workflow_id=workflow_id, conversation_id=workflow_id
    )
    return QueryRequest(request_id=request_id, prompt=prompt, context=context)


def make_checkpoint(
    store: SQLiteStateStore,
    *,
    workflow_id: str,
    request_id: str,
    status: WorkflowStatus = WorkflowStatus.RUNNING,
    **overrides,
) -> WorkflowState:
    values = {
        "workflow_id": workflow_id,
        "request_id": request_id,
        "status": status,
        "attempts": 1,
    }
    values.update(overrides)
    state = WorkflowState(**values)
    store.create(state)
    return state


def _resolve_active_projection(
    *,
    descriptor: SemanticDescriptor,
    scope: TenantScopeContext,
    catalog: InMemorySemanticBundleCatalog,
) -> ResolvedViewProjection:
    policy_scope = make_policy_scope(
        tenant_scope_fingerprint=scope.scope_fingerprint,
        isolation_profile=scope.tenant.isolation_profile.value,
    )
    registry = registry_from_active(
        catalog, descriptor=descriptor, scope=scope, policy_scope=policy_scope
    )
    return resolve_active(
        registry, catalog=catalog, scope=scope, policy_scope=policy_scope, purpose="analytics"
    )


class TestBundleBackedResolution:
    def _fixtures(self):
        descriptor = make_descriptor()
        scope = make_tenant_scope()
        policy_scope = make_policy_scope(
            tenant_scope_fingerprint=scope.scope_fingerprint,
            isolation_profile=scope.tenant.isolation_profile.value,
        )
        return descriptor, scope, policy_scope

    def _registry(self, bundle: SemanticModelBundle, scope, policy_scope) -> ViewRegistry:
        definition = make_view_definition(
            descriptor=bundle.descriptor,
            tenant_scope_fingerprint=scope.scope_fingerprint,
            policy_fingerprint=policy_scope.policy_fingerprint,
        )
        return ViewRegistry(
            descriptors=[bundle.descriptor], views=[definition], bundle=bundle
        )

    def test_bundle_backed_resolution_carries_bundle_identity(self) -> None:
        descriptor, scope, policy_scope = self._fixtures()
        bundle = make_bundle(descriptor=descriptor)
        registry = self._registry(bundle, scope, policy_scope)
        assert registry.bundle is bundle
        assert registry.descriptor("sales_descriptor") is bundle.descriptor

        outcome = registry.resolve(
            "sales_view",
            make_resolution_context(
                scope=scope,
                policy_scope=policy_scope,
                purpose="analytics",
                bundle_fingerprint=bundle.fingerprint,
            ),
        )
        assert outcome.kind == "resolved"
        assert outcome.projection is not None
        assert outcome.projection.bundle_id == "sales_model"
        assert outcome.projection.bundle_version == "1.0.0"
        assert outcome.projection.bundle_fingerprint == bundle.fingerprint
        assert outcome.projection.provenance.bundle_id == "sales_model"
        assert outcome.projection.provenance.bundle_fingerprint == bundle.fingerprint

    def test_missing_bundle_fingerprint_fails_closed(self) -> None:
        descriptor, scope, policy_scope = self._fixtures()
        bundle = make_bundle(descriptor=descriptor)
        registry = self._registry(bundle, scope, policy_scope)
        outcome = registry.resolve(
            "sales_view",
            make_resolution_context(scope=scope, policy_scope=policy_scope, purpose="analytics"),
        )
        assert outcome.kind == "unavailable"
        assert "bundle_scope_missing" in outcome.issue_codes()
        assert outcome.projection is None

    def test_stale_bundle_fingerprint_fails_closed(self) -> None:
        descriptor, scope, policy_scope = self._fixtures()
        bundle = make_bundle(descriptor=descriptor)
        registry = self._registry(bundle, scope, policy_scope)
        outcome = registry.resolve(
            "sales_view",
            make_resolution_context(
                scope=scope,
                policy_scope=policy_scope,
                purpose="analytics",
                bundle_fingerprint=fp("00"),
            ),
        )
        assert outcome.kind == "unavailable"
        assert "bundle_stale" in outcome.issue_codes()
        assert outcome.projection is None

    def test_registry_rejects_bundle_with_unregistered_descriptor(self) -> None:
        descriptor, scope, policy_scope = self._fixtures()
        bundle = make_bundle(descriptor=descriptor)
        other = make_descriptor(descriptor_id="other_descriptor")
        definition = make_view_definition(
            descriptor=other,
            tenant_scope_fingerprint=scope.scope_fingerprint,
            policy_fingerprint=policy_scope.policy_fingerprint,
        )
        with pytest.raises(ValueError) as excinfo:
            ViewRegistry(descriptors=[other], views=[definition], bundle=bundle)
        assert "bundle descriptor" in str(excinfo.value)

    def test_descriptor_only_mode_resolves_without_bundle_identity(self) -> None:
        descriptor, scope, policy_scope = self._fixtures()
        definition = make_view_definition(
            descriptor=descriptor,
            tenant_scope_fingerprint=scope.scope_fingerprint,
            policy_fingerprint=policy_scope.policy_fingerprint,
        )
        registry = ViewRegistry(descriptors=[descriptor], views=[definition])
        outcome = registry.resolve(
            "sales_view",
            make_resolution_context(scope=scope, policy_scope=policy_scope, purpose="analytics"),
        )
        assert outcome.kind == "resolved"
        assert outcome.projection is not None
        assert outcome.projection.bundle_id is None
        assert outcome.projection.bundle_version is None
        assert outcome.projection.bundle_fingerprint is None


class TestBundleToIrBinding:
    """``validate_ir`` binds IR evidence to the bundle-backed projection."""

    def _bound_ir(self, projection: ResolvedViewProjection) -> object:
        intent = StructuredIntent.model_validate(
            {
                "intent_id": "intent-bundle-1",
                "request_id": "req-bundle-ir",
                "source_id": "sales",
                "root_entity_id": "order",
                "selections": [
                    {"selection_id": "s1", "field_id": "order_id"},
                    {"selection_id": "s2", "field_id": "amount"},
                ],
                "limit": 10,
                "confidence": 0.95,
            }
        )
        reference = IRViewReference(
            view_id=projection.view_id,
            view_version=projection.view_version,
            view_fingerprint=projection.fingerprint,
        )
        return build_ir_from_intent(
            intent,
            catalog_fingerprint=projection.catalog_fingerprint,
            view_reference=reference,
        )

    def _catalog(self, descriptor: SemanticDescriptor) -> InMemorySemanticBundleCatalog:
        catalog = make_catalog_with_active(make_bundle(descriptor=descriptor, version="1.0.0"))
        return catalog

    def test_bundle_projection_binds_ir_evidence(self) -> None:
        descriptor = make_descriptor()
        scope = make_tenant_scope()
        policy_scope = make_policy_scope(
            tenant_scope_fingerprint=scope.scope_fingerprint,
            isolation_profile=scope.tenant.isolation_profile.value,
        )
        catalog = self._catalog(descriptor)
        registry = registry_from_active(
            catalog, descriptor=descriptor, scope=scope, policy_scope=policy_scope
        )
        projection = resolve_active(
            registry, catalog=catalog, scope=scope, policy_scope=policy_scope, purpose="analytics"
        )
        view = AuthorizedView.from_projection(projection)
        result = validate_ir(self._bound_ir(projection), view=view)
        assert result.valid, result.issues

    def test_activation_invalidates_ir_evidence(self) -> None:
        descriptor = make_descriptor()
        scope = make_tenant_scope()
        policy_scope = make_policy_scope(
            tenant_scope_fingerprint=scope.scope_fingerprint,
            isolation_profile=scope.tenant.isolation_profile.value,
        )
        catalog = make_catalog_with_active(make_bundle(descriptor=descriptor, version="1.0.0"))
        registry_v1 = registry_from_active(
            catalog, descriptor=descriptor, scope=scope, policy_scope=policy_scope
        )
        projection_v1 = resolve_active(
            registry_v1,
            catalog=catalog,
            scope=scope,
            policy_scope=policy_scope,
            purpose="analytics",
        )
        ir_v1 = self._bound_ir(projection_v1)
        view_v1 = AuthorizedView.from_projection(projection_v1)
        assert validate_ir(ir_v1, view=view_v1).valid

        #: Activation switches the snapshot; old IR evidence is stale.
        v2 = make_bundle(descriptor=descriptor, version="2.0.0")
        assert catalog.publish(v2).success
        assert catalog.activate("sales_model", "2.0.0").success
        registry_v2 = registry_from_active(
            catalog, descriptor=descriptor, scope=scope, policy_scope=policy_scope
        )
        projection_v2 = resolve_active(
            registry_v2,
            catalog=catalog,
            scope=scope,
            policy_scope=policy_scope,
            purpose="analytics",
        )
        assert projection_v2.fingerprint != projection_v1.fingerprint
        view_v2 = AuthorizedView.from_projection(projection_v2)
        stale = validate_ir(ir_v1, view=view_v2)
        assert not stale.valid
        assert "view_reference_mismatch" in stale.issue_codes()

        #: Fresh IR bound to the new snapshot validates.
        assert validate_ir(self._bound_ir(projection_v2), view=view_v2).valid


class TestBundleEvidenceInvalidation:
    async def test_activation_invalidates_checkpoint_evidence_before_adapter(
        self, tmp_path: Path
    ) -> None:
        """The failing first run records v1 evidence; the v2 activation is
        refused before any adapter work."""
        store = SQLiteStateStore(tmp_path / "durable.db")
        try:
            scope = make_tenant_scope()
            descriptor = make_descriptor()
            catalog = InMemorySemanticBundleCatalog()
            v1 = make_bundle(descriptor=descriptor, version="1.0.0")
            v2 = make_bundle(descriptor=descriptor, version="2.0.0")
            assert catalog.publish(v1).success
            assert catalog.publish(v2).success
            assert catalog.activate("sales_model", "1.0.0").success

            #: Run 1 - the failing adapter records v1 view evidence.
            failing = make_adapter(tmp_path, RetryableFailingAdapter)
            runtime_v1, projection_v1 = make_bundle_runtime(
                tmp_path,
                adapter=failing,
                scope=scope,
                purpose="analytics",
                state_store=store,
                catalog=catalog,
                descriptor=descriptor,
            )
            first = await runtime_v1.execute(request("req-bundle-ev", "wf-bundle-ev"))
            assert first.status == OutcomeStatus.REJECTED
            assert first.error is not None
            assert first.error.code == ErrorCode.RETRY_EXHAUSTED

            checkpoint = store.get_checkpoint(
                "wf-bundle-ev",
                "req-bundle-ev",
                tenant_scope_fingerprint=scope.scope_fingerprint,
            )
            assert checkpoint is not None
            assert "view" in checkpoint.compatibility_fingerprints
            newest = checkpoint.events[-1]
            assert newest.metadata.get("view_id") == "sales_view"
            assert newest.metadata.get("view_version") == "1"
            assert newest.metadata.get("view_fingerprint") == projection_v1.fingerprint

            #: Run 2 - activation switches the snapshot; v1 evidence is stale.
            assert catalog.activate("sales_model", "2.0.0").success
            adapter_v2 = make_adapter(tmp_path)
            runtime_v2, projection_v2 = make_bundle_runtime(
                tmp_path,
                adapter=adapter_v2,
                scope=scope,
                purpose="analytics",
                state_store=store,
                catalog=catalog,
                descriptor=descriptor,
            )
            assert projection_v2.fingerprint != projection_v1.fingerprint
            second = await runtime_v2.execute(request("req-bundle-ev", "wf-bundle-ev"))
            assert second.status == OutcomeStatus.REJECTED
            assert second.error is not None
            assert second.error.code == ErrorCode.STALE_CHECKPOINT
            assert adapter_v2.executions == 0  # refused before any adapter work
        finally:
            store.close()

    async def test_rollback_restores_prior_evidence_for_resume(self, tmp_path: Path) -> None:
        """A pre-execution checkpoint under v1 resumes only under v1.

        Activation under v2 rejects the v1 evidence before any adapter work;
        after rollback the v1 evidence matches again and execution resumes
        to success.
        """
        store = SQLiteStateStore(tmp_path / "durable.db")
        try:
            scope = make_tenant_scope()
            descriptor = make_descriptor()
            catalog = InMemorySemanticBundleCatalog()
            v1 = make_bundle(descriptor=descriptor, version="1.0.0")
            v2 = make_bundle(descriptor=descriptor, version="2.0.0")
            assert catalog.publish(v1).success
            assert catalog.publish(v2).success
            assert catalog.activate("sales_model", "1.0.0").success

            projection_v1 = _resolve_active_projection(
                descriptor=descriptor, scope=scope, catalog=catalog
            )
            view_compat = sha256_fingerprint(
                {
                    "view_id": projection_v1.view_id,
                    "view_version": projection_v1.view_version,
                    "view_fingerprint": projection_v1.fingerprint,
                }
            )
            make_checkpoint(
                store,
                workflow_id="wf-bundle-rb",
                request_id="req-bundle-rb",
                current_stage=WorkflowStage.INTENT,
                tenant_scope_fingerprint=scope.scope_fingerprint,
                compatibility_fingerprints={
                    "view": view_compat,
                    "semantic": projection_v1.fingerprint,
                },
            )

            #: Activation under v2 rejects the v1 evidence before any adapter.
            assert catalog.activate("sales_model", "2.0.0").success
            adapter_v2 = make_adapter(tmp_path)
            runtime_v2, projection_v2 = make_bundle_runtime(
                tmp_path,
                adapter=adapter_v2,
                scope=scope,
                purpose="analytics",
                state_store=store,
                catalog=catalog,
                descriptor=descriptor,
            )
            assert projection_v2.fingerprint != projection_v1.fingerprint
            second = await runtime_v2.execute(request("req-bundle-rb", "wf-bundle-rb"))
            assert second.status == OutcomeStatus.REJECTED
            assert second.error is not None
            assert second.error.code == ErrorCode.STALE_CHECKPOINT
            assert adapter_v2.executions == 0

            #: Rollback restores v1; the checkpoint resumes to success.
            assert catalog.rollback("sales_model").success
            adapter_v3 = make_adapter(tmp_path)
            runtime_v3, projection_v3 = make_bundle_runtime(
                tmp_path,
                adapter=adapter_v3,
                scope=scope,
                purpose="analytics",
                state_store=store,
                catalog=catalog,
                descriptor=descriptor,
            )
            assert projection_v3.fingerprint == projection_v1.fingerprint
            third = await runtime_v3.execute(request("req-bundle-rb", "wf-bundle-rb"))
            assert third.status == OutcomeStatus.SUCCEEDED
            assert adapter_v3.executions == 1
        finally:
            store.close()


class TestBundleMemoryRevalidation:
    async def test_bundle_change_requests_clarification_before_provider(
        self, tmp_path: Path
    ) -> None:
        """Memory recorded under one active bundle is stale under another."""
        scope = make_tenant_scope()
        descriptor = make_descriptor()
        provider = FakeModelProvider(default_response=VALID_INTENT)
        memory = InMemoryMemoryProvider()

        catalog = make_catalog_with_active(make_bundle(descriptor=descriptor, version="1.0.0"))
        runtime_a, projection_a = make_bundle_runtime(
            tmp_path,
            adapter=make_adapter(tmp_path),
            scope=scope,
            purpose="analytics",
            provider=provider,
            memory=memory,
            catalog=catalog,
            descriptor=descriptor,
        )
        first = await runtime_a.execute(
            memory_request("r1", "top 10 order amounts in emea", "wf-bundle-mem")
        )
        assert first.status == OutcomeStatus.SUCCEEDED
        assert provider.call_count == 1

        v2 = make_bundle(descriptor=descriptor, version="2.0.0")
        assert catalog.publish(v2).success
        assert catalog.activate("sales_model", "2.0.0").success
        runtime_b, projection_b = make_bundle_runtime(
            tmp_path,
            adapter=make_adapter(tmp_path),
            scope=scope,
            purpose="analytics",
            provider=provider,
            memory=memory,
            catalog=catalog,
            descriptor=descriptor,
        )
        assert projection_b.fingerprint != projection_a.fingerprint

        second = await runtime_b.execute(
            memory_request("r2", "same query but only for apac", "wf-bundle-mem")
        )
        assert second.status == OutcomeStatus.CLARIFICATION
        assert second.clarification is not None
        assert "stale" in second.clarification.question
        assert provider.call_count == 1  # the model is never invoked
