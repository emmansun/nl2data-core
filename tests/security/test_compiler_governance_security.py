"""Security tests for the compiler-governance boundary (DDS-019).

Proves compiler output cannot grant authority or bypass policy, view, or
tenant restrictions: compilation alone never authorizes execution, the
artifact guard rejects unauthorized fields/objects and unbounded or
obligation-incomplete artifacts on both backends, view membership is
enforced before any compiler runs, and tenant-scoped governance and
authorization rechecking deny mismatched or missing tenant evidence.
"""

from __future__ import annotations

import json

import pytest

from nl2data_core.adapters.models import (
    AdapterCapabilities,
    AsyncMode,
    ValidationContext,
)
from nl2data_core.adapters.sql.adapter import SqlQueryAdapter
from nl2data_core.adapters.sql.compile import SQLCompileError, compile_sql
from nl2data_core.adapters.sql.guard import SQLGuardError
from nl2data_core.compilation.contract import (
    ArtifactGuardResult,
    CompilationContext,
    CompilationEvidence,
    CompileResult,
    verify_pre_execution_guard,
)
from nl2data_core.governance.authorization import (
    AuthorizationIssuer,
    AuthorizationVerifier,
)
from nl2data_core.governance.decisions import PolicyEvaluator
from nl2data_core.governance.models import (
    EffectiveLimits,
    GovernanceDecision,
    GovernanceFacts,
    PolicyScope,
)
from nl2data_core.planning.ir.fixtures import golden_binding
from nl2data_core.planning.ir.models import (
    IRFilter,
    IRProvenance,
    IRResultShape,
    IRSelection,
    IRViewReference,
    SemanticQueryIR,
)
from nl2data_core.planning.ir.validation import validate_ir
from nl2data_core.planning.validation import AuthorizedView
from nl2data_mongodb.adapter import MongoQueryAdapter
from nl2data_mongodb.compile import MongoCompileError, compile_mongo
from nl2data_mongodb.config import MongoAdapterConfig
from nl2data_mongodb.models import MongoAdapterError

TENANT_A = "sha256:" + "11" * 32
TENANT_B = "sha256:" + "22" * 32
DIGEST = "sha256:" + "ab" * 32
BINDING = golden_binding()
FIELD_BINDINGS = {
    column.physical_name: column.field_id for column in BINDING.column_bindings
}


def aggregate_ir() -> SemanticQueryIR:
    """A single-aggregate IR with one mandatory filter (capability-safe)."""
    return SemanticQueryIR(
        ir_id="ir-sec-agg",
        source_id="acme_warehouse",
        root_entity_id="orders",
        selections=(
            IRSelection(
                selection_id="s1", field_id="total_amount", alias=None, aggregation="sum"
            ),
        ),
        filters=(IRFilter(filter_id="f1", field_id="status", operator="eq", value="shipped"),),
        limit=100,
        result_shape=IRResultShape(kind="scalar"),
        provenance=IRProvenance(
            source_id="acme_warehouse",
            root_entity_id="orders",
            catalog_fingerprint="sha256:" + "ab" * 32,
            policy_view_fingerprint="sha256:" + "cd" * 32,
        ),
        required_capabilities=("aggregation",),
    )


def sql_capabilities() -> AdapterCapabilities:
    return AdapterCapabilities(
        adapter_type="sql",
        query_language="sql",
        async_mode=AsyncMode.THREAD_OFFLOAD,
        features=frozenset(
            {
                "read_only",
                "single_statement",
                "bounded_results",
                "ast_validation",
                "aggregation",
                "ordering",
                "list_ops",
                "contains",
                "cte",
                "grouping",
                "union",
            }
        ),
    )


def mongo_capabilities() -> AdapterCapabilities:
    return AdapterCapabilities(
        adapter_type="mongodb",
        query_language="mql",
        async_mode=AsyncMode.THREAD_OFFLOAD,
        features=frozenset(
            {
                "read_only",
                "structured_mql",
                "no_javascript",
                "bounded_results",
                "allowlist_validation",
                "tenant_obligations",
                "aggregation",
                "ordering",
                "list_ops",
                "fake",
            }
        ),
    )


def sql_adapter() -> SqlQueryAdapter:
    return SqlQueryAdapter(
        dialect="sqlite",
        allowed_objects=frozenset({"orders_table"}),
        allowed_columns=frozenset({"region", "total_amount", "status"}),
        max_rows=1_000,
    )


def mongo_adapter() -> MongoQueryAdapter:
    return MongoQueryAdapter(
        config=MongoAdapterConfig(
            allowed_collections=frozenset({"orders_table"}),
            allowed_fields=frozenset({"region", "total_amount", "status"}),
            max_limit=1_000,
        )
    )


def sql_context(ir: SemanticQueryIR, **overrides) -> CompilationContext:
    return CompilationContext(
        ir=ir,
        adapter_capabilities=sql_capabilities(),
        effective_limits=EffectiveLimits(max_rows=1_000),
        mandatory_filter_fingerprints=ir.filter_fingerprints(),
        compiler_context=BINDING,
        **overrides,
    )


def mongo_context(ir: SemanticQueryIR, **overrides) -> CompilationContext:
    return CompilationContext(
        ir=ir,
        adapter_capabilities=mongo_capabilities(),
        effective_limits=EffectiveLimits(max_rows=1_000),
        mandatory_filter_fingerprints=ir.filter_fingerprints(),
        compiler_context=BINDING,
        **overrides,
    )


def sql_guard_result(ir: SemanticQueryIR) -> ArtifactGuardResult:
    result = compile_sql(ir, context=sql_context(ir))
    vctx = ValidationContext(
        snapshot_fingerprint=ir.provenance.catalog_fingerprint,
        required_obligation_fingerprints=ir.filter_fingerprints(),
        field_bindings=FIELD_BINDINGS,
    )
    adapter = sql_adapter()
    parsed = adapter.parse(result.artifact, vctx)
    validated = adapter.validate(parsed, vctx)
    return ArtifactGuardResult(
        accepted=True,
        fingerprint=validated.fingerprint,
        guard_identity="sql-artifact-guard",
        artifact_fingerprint=parsed.fingerprint,
        obligations_verified=validated.obligations_verified,
        bounded_rows=validated.bounded_rows,
    )


def mongo_guard_result(ir: SemanticQueryIR) -> ArtifactGuardResult:
    result = compile_mongo(ir, context=mongo_context(ir))
    vctx = ValidationContext(
        snapshot_fingerprint=ir.provenance.catalog_fingerprint,
        required_obligation_fingerprints=ir.filter_fingerprints(),
        field_bindings=FIELD_BINDINGS,
    )
    adapter = mongo_adapter()
    parsed = adapter.parse(result.artifact, vctx)
    validated = adapter.validate(parsed, vctx)
    return ArtifactGuardResult(
        accepted=True,
        fingerprint=validated.fingerprint,
        guard_identity="mongodb-artifact-guard",
        artifact_fingerprint=parsed.fingerprint,
        obligations_verified=validated.obligations_verified,
        bounded_rows=validated.bounded_rows,
    )


def tenant_policy() -> PolicyScope:
    return PolicyScope(
        policy_id="policy-tenant",
        source_ids=frozenset({"acme_warehouse"}),
        resource_ids=frozenset({"orders"}),
        operation_ids=frozenset({"select"}),
        field_ids=frozenset({"total_amount", "status"}),
        tenant_scope_fingerprint=TENANT_A,
        isolation_profile="pooled",
    )


def tenant_facts(*, tenant: str | None = TENANT_A) -> GovernanceFacts:
    return GovernanceFacts(
        source_id="acme_warehouse",
        operation="select",
        resource_ids=frozenset({"orders"}),
        field_ids=frozenset({"total_amount", "status"}),
        tenant_scope_fingerprint=tenant,
        isolation_profile="pooled" if tenant is not None else None,
    )


class TestCompilerCannotGrantAuthority:
    def test_compilation_alone_cannot_authorize_execution(self) -> None:
        ir = aggregate_ir()
        for compile_fn, context, guard in (
            (compile_sql, sql_context(ir), sql_guard_result(ir)),
            (compile_mongo, mongo_context(ir), mongo_guard_result(ir)),
        ):
            result = compile_fn(ir, context=context)
            assert guard.accepted
            reasons = verify_pre_execution_guard(
                context=context,
                evidence=result.evidence,
                guard=guard,
                authorization=None,
            )
            assert "execution authorization is missing" in reasons

    def test_compile_result_carries_no_authority_fields(self) -> None:
        assert "authorization" not in CompileResult.model_fields
        assert "decision" not in CompilationEvidence.model_fields
        assert "grant" not in CompilationEvidence.model_fields
        assert "role" not in CompilationEvidence.model_fields

    def test_compiler_cannot_inject_policy_or_tenant_identity(self) -> None:
        ir = aggregate_ir()
        context = sql_context(
            ir, policy_fingerprint=DIGEST, tenant_scope_fingerprint=TENANT_A
        )
        result = compile_sql(ir, context=context)
        assert result.evidence.policy_fingerprint == DIGEST
        assert result.evidence.tenant_scope_fingerprint == TENANT_A
        dumped = result.evidence.model_dump_json()
        assert "tenant_id" not in dumped
        assert "tenant_name" not in dumped
        assert "policy_body" not in dumped


class TestGuardRejectsUnauthorizedArtifacts:
    def test_sql_artifact_with_unauthorized_column_is_rejected(self) -> None:
        adapter = sql_adapter()
        with pytest.raises(SQLGuardError):
            adapter.validate(
                adapter.parse(
                    "SELECT total_amount, secret FROM orders_table "
                    "WHERE status = 'shipped' LIMIT 5",
                    ValidationContext(),
                ),
                ValidationContext(),
            )

    def test_sql_artifact_with_unauthorized_object_is_rejected(self) -> None:
        adapter = sql_adapter()
        with pytest.raises(SQLGuardError):
            adapter.validate(
                adapter.parse(
                    "SELECT total_amount FROM secret_table LIMIT 5",
                    ValidationContext(),
                ),
                ValidationContext(),
            )

    def test_mongo_spec_with_unauthorized_field_is_rejected(self) -> None:
        adapter = mongo_adapter()
        payload = {
            "spec_id": "spec-sec-1",
            "operation": "find",
            "collection": "orders_table",
            "filter": {"secret_field": {"$eq": 1}},
            "projection": {"total_amount": 1},
            "limit": 5,
        }
        with pytest.raises(MongoAdapterError):
            adapter.validate(
                adapter.parse(json.dumps(payload), ValidationContext()),
                ValidationContext(),
            )

    def test_mongo_spec_with_unauthorized_collection_is_rejected(self) -> None:
        adapter = mongo_adapter()
        payload = {
            "spec_id": "spec-sec-2",
            "operation": "find",
            "collection": "secret_table",
            "filter": {"status": {"$eq": "shipped"}},
            "projection": {"total_amount": 1},
            "limit": 5,
        }
        with pytest.raises(MongoAdapterError):
            adapter.validate(
                adapter.parse(json.dumps(payload), ValidationContext()),
                ValidationContext(),
            )


class TestCompilerCannotBypassPolicy:
    def test_sql_artifact_omitting_mandatory_filter_is_rejected(self) -> None:
        ir = aggregate_ir()
        result = compile_sql(ir, context=sql_context(ir))
        tampered = result.artifact.replace(" WHERE status = 'shipped'", "")
        adapter = sql_adapter()
        vctx = ValidationContext(
            required_obligation_fingerprints=ir.filter_fingerprints(),
            field_bindings=FIELD_BINDINGS,
        )
        with pytest.raises(SQLGuardError):
            adapter.validate(adapter.parse(tampered, vctx), vctx)

    def test_mongo_artifact_omitting_mandatory_filter_is_rejected(self) -> None:
        ir = aggregate_ir()
        result = compile_mongo(ir, context=mongo_context(ir))
        payload = json.loads(result.artifact)
        payload.pop("filter", None)
        payload["pipeline"] = [
            stage for stage in payload["pipeline"] if "$match" not in stage
        ]
        adapter = mongo_adapter()
        vctx = ValidationContext(
            required_obligation_fingerprints=ir.filter_fingerprints(),
            field_bindings=FIELD_BINDINGS,
        )
        with pytest.raises(MongoAdapterError):
            adapter.validate(
                adapter.parse(json.dumps(payload), vctx), vctx
            )

    def test_unbounded_sql_artifact_is_rejected(self) -> None:
        adapter = sql_adapter()
        with pytest.raises(SQLGuardError):
            adapter.validate(
                adapter.parse(
                    "SELECT total_amount FROM orders_table WHERE status = 'shipped'",
                    ValidationContext(),
                ),
                ValidationContext(),
            )

    def test_unbounded_mongo_spec_is_rejected(self) -> None:
        adapter = mongo_adapter()
        payload = {
            "spec_id": "spec-sec-3",
            "operation": "find",
            "collection": "orders_table",
            "filter": {"status": {"$eq": "shipped"}},
            "projection": {"total_amount": 1},
        }
        with pytest.raises(MongoAdapterError):
            adapter.validate(
                adapter.parse(json.dumps(payload), ValidationContext()),
                ValidationContext(),
            )

    def test_ir_without_limit_fails_closed_in_both_compilers(self) -> None:
        ir = aggregate_ir()
        unbounded = SemanticQueryIR(
            ir_id=ir.ir_id,
            source_id=ir.source_id,
            root_entity_id=ir.root_entity_id,
            selections=ir.selections,
            filters=ir.filters,
            limit=None,
            result_shape=ir.result_shape,
            provenance=ir.provenance,
            required_capabilities=ir.required_capabilities,
        )
        with pytest.raises(SQLCompileError):
            compile_sql(unbounded, context=sql_context(unbounded))
        with pytest.raises(MongoCompileError):
            compile_mongo(unbounded, context=mongo_context(unbounded))


class TestViewMembershipBypassRejected:
    def test_ir_outside_view_is_rejected_by_validation(self) -> None:
        ir = aggregate_ir()
        view = AuthorizedView(
            source_id="acme_warehouse", field_ids=frozenset({"other_field"})
        )
        validation = validate_ir(ir, view=view)
        assert not validation.valid
        assert "field_out_of_scope" in validation.issue_codes()

    def test_compilers_fail_closed_on_view_violation(self) -> None:
        ir = aggregate_ir()
        view = AuthorizedView(
            source_id="acme_warehouse", field_ids=frozenset({"other_field"})
        )
        with pytest.raises(SQLCompileError):
            compile_sql(ir, context=sql_context(ir, view=view))
        with pytest.raises(MongoCompileError):
            compile_mongo(ir, context=mongo_context(ir, view=view))

    def test_ir_not_referencing_bound_view_is_rejected(self) -> None:
        ir = aggregate_ir()
        view = AuthorizedView(
            source_id="acme_warehouse",
            field_ids=ir.field_ids(),
            view_id="view-1",
            view_version=1,
            view_fingerprint=DIGEST,
        )
        reference = IRViewReference(
            view_id="view-1", view_version=1, view_fingerprint=DIGEST
        )
        context = sql_context(
            ir, view=view, view_fingerprint=DIGEST, view_reference=reference
        )
        # The IR itself carries no view reference: the bound view requires
        # the exact resolved identity and the compiler fails closed.
        assert ir.provenance.view_reference is None
        with pytest.raises(SQLCompileError):
            compile_sql(ir, context=context)


class TestTenantRestrictionsEnforced:
    def test_governance_denies_tenant_scope_mismatch(self) -> None:
        result = PolicyEvaluator().evaluate(
            facts=tenant_facts(tenant=TENANT_B), scope=tenant_policy()
        )
        assert result.decision == GovernanceDecision.DENY
        assert any(
            "tenant scope fingerprint does not match" in reason
            for reason in result.reasons
        )

    def test_governance_denies_missing_tenant_scope(self) -> None:
        result = PolicyEvaluator().evaluate(
            facts=tenant_facts(tenant=None), scope=tenant_policy()
        )
        assert result.decision == GovernanceDecision.DENY
        assert any(
            "requires a trusted tenant scope fingerprint" in reason
            for reason in result.reasons
        )

    def test_authorization_rechecked_when_tenant_scope_changes(self) -> None:
        authorization = AuthorizationIssuer().issue(
            policy_scope=tenant_policy(),
            adapter_type="sql",
            source_id="acme_warehouse",
            operation="select",
            artifact_fingerprint=DIGEST,
            tenant_scope_fingerprint=TENANT_A,
            isolation_profile="pooled",
        )
        result = AuthorizationVerifier().verify(
            authorization,
            artifact_fingerprint=DIGEST,
            adapter_type="sql",
            source_id="acme_warehouse",
            operation="select",
            tenant_scope_fingerprint=TENANT_B,
            isolation_profile="pooled",
        )
        assert not result.verified
        assert any(
            "tenant scope fingerprint does not match" in reason
            for reason in result.reasons
        )

    def test_tenant_bound_authorization_verifies_unchanged(self) -> None:
        authorization = AuthorizationIssuer().issue(
            policy_scope=tenant_policy(),
            adapter_type="sql",
            source_id="acme_warehouse",
            operation="select",
            artifact_fingerprint=DIGEST,
            tenant_scope_fingerprint=TENANT_A,
            isolation_profile="pooled",
        )
        result = AuthorizationVerifier().verify(
            authorization,
            artifact_fingerprint=DIGEST,
            adapter_type="sql",
            source_id="acme_warehouse",
            operation="select",
            tenant_scope_fingerprint=TENANT_A,
            isolation_profile="pooled",
        )
        assert result.verified
        assert result.reasons == ()
