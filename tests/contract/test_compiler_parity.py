"""SQL/MongoDB compiler-governance parity tests (DDS-019).

Covers task 5.2: the same IR compiled by both compilers produces distinct
artifacts linked to one logical identity; capability mismatches fail
closed at the compiler or the shared guard boundary; mandatory filter
obligations and result bounds are enforced on both backends; execution
authorization mismatches are rejected before execution; and protected
result lineage links both backends to the same decision chain.
"""

from __future__ import annotations

import json

import pytest

from nl2data_core.adapters.models import AdapterCapabilities, AsyncMode, ValidationContext
from nl2data_core.adapters.mongodb.adapter import MongoQueryAdapter
from nl2data_core.adapters.mongodb.compile import MongoCompileError, compile_mongo
from nl2data_core.adapters.mongodb.models import MongoAdapterConfig, MongoAdapterError
from nl2data_core.adapters.sql.adapter import SqlQueryAdapter
from nl2data_core.adapters.sql.compile import compile_sql
from nl2data_core.adapters.sql.guard import SQLGuardError
from nl2data_core.compilation.contract import (
    ArtifactGuardResult,
    CompilationContext,
    CompilationEvidence,
    ResultLineageEvidence,
    compilation_evidence_fingerprint,
    result_lineage_fingerprint,
    verify_pre_execution_guard,
)
from nl2data_core.governance.authorization import AuthorizationIssuer
from nl2data_core.governance.models import EffectiveLimits, PolicyScope
from nl2data_core.planning.ir.fixtures import golden_binding, golden_ir
from nl2data_core.planning.ir.models import (
    IRFilter,
    IRProvenance,
    IRResultShape,
    IRSelection,
    SemanticQueryIR,
)

SECOND = "sha256:" + "cd" * 32
BINDING = golden_binding()
FIELD_BINDINGS = {
    column.physical_name: column.field_id for column in BINDING.column_bindings
}


def aggregate_ir() -> SemanticQueryIR:
    """A single-aggregate IR; capabilities are within both adapter profiles."""
    return SemanticQueryIR(
        ir_id="ir-parity-agg",
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


def grouped_ir() -> SemanticQueryIR:
    """The golden IR minus extension noise; requires ``grouping``."""
    ir = golden_ir()
    return SemanticQueryIR(
        ir_id=ir.ir_id,
        source_id=ir.source_id,
        root_entity_id=ir.root_entity_id,
        selections=ir.selections,
        filters=ir.filters,
        groupings=ir.groupings,
        orderings=ir.orderings,
        limit=ir.limit,
        time_context=ir.time_context,
        result_shape=ir.result_shape,
        provenance=ir.provenance,
        required_capabilities=("aggregation", "grouping", "list_ops", "ordering"),
    )


def contains_ir() -> SemanticQueryIR:
    """A plain IR with a ``contains`` filter; SQL-only capability."""
    return SemanticQueryIR(
        ir_id="ir-parity-contains",
        source_id="acme_warehouse",
        root_entity_id="orders",
        selections=(
            IRSelection(selection_id="s1", field_id="region", alias=None, aggregation="none"),
        ),
        filters=(
            IRFilter(filter_id="f1", field_id="status", operator="contains", value="ship"),
        ),
        limit=100,
        result_shape=IRResultShape(kind="rows"),
        provenance=IRProvenance(
            source_id="acme_warehouse",
            root_entity_id="orders",
            catalog_fingerprint="sha256:" + "ab" * 32,
            policy_view_fingerprint="sha256:" + "cd" * 32,
        ),
        required_capabilities=("contains",),
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


def sql_context(ir: SemanticQueryIR, **overrides) -> CompilationContext:
    kwargs = dict(
        ir=ir,
        adapter_capabilities=sql_capabilities(),
        effective_limits=EffectiveLimits(max_rows=1_000),
        mandatory_filter_fingerprints=ir.filter_fingerprints(),
        compiler_context=BINDING,
    )
    kwargs.update(overrides)
    return CompilationContext(**kwargs)


def mongo_context(ir: SemanticQueryIR, **overrides) -> CompilationContext:
    kwargs = dict(
        ir=ir,
        adapter_capabilities=mongo_capabilities(),
        effective_limits=EffectiveLimits(max_rows=1_000),
        mandatory_filter_fingerprints=ir.filter_fingerprints(),
        compiler_context=BINDING,
    )
    kwargs.update(overrides)
    return CompilationContext(**kwargs)


def sql_chain(
    ir: SemanticQueryIR,
    *,
    context: CompilationContext | None = None,
) -> tuple[CompilationContext, CompilationEvidence, ArtifactGuardResult, str]:
    context = context or sql_context(ir)
    result = compile_sql(ir, context=context)
    adapter = SqlQueryAdapter(
        dialect="sqlite",
        allowed_objects=frozenset({"orders_table"}),
        allowed_columns=frozenset({"region", "total_amount", "status"}),
        max_rows=1_000,
    )
    vctx = ValidationContext(
        snapshot_fingerprint=ir.provenance.catalog_fingerprint,
        required_obligation_fingerprints=ir.filter_fingerprints(),
        field_bindings=FIELD_BINDINGS,
    )
    parsed = adapter.parse(result.artifact, vctx)
    validated = adapter.validate(parsed, vctx)
    guard = ArtifactGuardResult(
        accepted=True,
        fingerprint=validated.fingerprint,
        guard_identity="sql-artifact-guard",
        artifact_fingerprint=parsed.fingerprint,
        obligations_verified=validated.obligations_verified,
        bounded_rows=validated.bounded_rows,
    )
    return context, result.evidence, guard, result.artifact


def mongo_chain(
    ir: SemanticQueryIR,
    *,
    context: CompilationContext | None = None,
) -> tuple[CompilationContext, CompilationEvidence, ArtifactGuardResult, str]:
    context = context or mongo_context(ir)
    result = compile_mongo(ir, context=context)
    adapter = MongoQueryAdapter(
        config=MongoAdapterConfig(
            allowed_collections=frozenset({"orders_table"}),
            allowed_fields=frozenset({"region", "total_amount", "status"}),
            max_limit=1_000,
        )
    )
    vctx = ValidationContext(
        snapshot_fingerprint=ir.provenance.catalog_fingerprint,
        required_obligation_fingerprints=ir.filter_fingerprints(),
        field_bindings=FIELD_BINDINGS,
    )
    parsed = adapter.parse(result.artifact, vctx)
    validated = adapter.validate(parsed, vctx)
    guard = ArtifactGuardResult(
        accepted=True,
        fingerprint=validated.fingerprint,
        guard_identity="mongodb-artifact-guard",
        artifact_fingerprint=parsed.fingerprint,
        obligations_verified=validated.obligations_verified,
        bounded_rows=validated.bounded_rows,
    )
    return context, result.evidence, guard, result.artifact


def issue_authorization(
    context: CompilationContext,
    evidence: CompilationEvidence,
    guard: ArtifactGuardResult,
    **overrides,
):
    scope = PolicyScope(
        policy_id="policy-1",
        source_ids=frozenset({context.ir.source_id}),
        resource_ids=frozenset({context.ir.root_entity_id}),
        operation_ids=frozenset({"select"}),
        field_ids=context.ir.field_ids(),
    )
    kwargs = dict(
        policy_scope=scope,
        adapter_type=evidence.adapter_type,
        source_id=evidence.source_id,
        operation="select",
        artifact_fingerprint=guard.fingerprint,
        ir_fingerprint=evidence.ir_fingerprint,
        effective_limits=context.effective_limits,
    )
    kwargs.update(overrides)
    return AuthorizationIssuer().issue(**kwargs)


def drop_sql_filter(sql: str) -> str:
    """An adversarial artifact that silently drops the WHERE clause."""
    return sql.replace(" WHERE status = 'shipped'", "")


def drop_mongo_filter(wire: str) -> str:
    """An adversarial spec that silently drops every filter predicate."""
    payload = json.loads(wire)
    payload.pop("filter", None)
    if payload.get("pipeline"):
        payload["pipeline"] = [
            stage for stage in payload["pipeline"] if "$match" not in stage
        ]
    return json.dumps(payload)


class TestCrossBackendEvidence:
    def test_same_ir_produces_distinct_artifacts_shared_identity(self) -> None:
        ir = aggregate_ir()
        sql_context_, sql_evidence, sql_guard, sql_artifact = sql_chain(ir)
        mongo_context_, mongo_evidence, mongo_guard, mongo_artifact = mongo_chain(ir)
        assert sql_evidence.ir_fingerprint == mongo_evidence.ir_fingerprint == ir.fingerprint
        assert sql_evidence.adapter_type == "sql"
        assert mongo_evidence.adapter_type == "mongodb"
        assert sql_artifact != mongo_artifact
        assert sql_evidence.artifact_fingerprint != mongo_evidence.artifact_fingerprint
        assert (
            sql_evidence.artifact_fingerprint == sql_guard.artifact_fingerprint
        )
        assert (
            mongo_evidence.artifact_fingerprint == mongo_guard.artifact_fingerprint
        )

    def test_evidence_fingerprints_are_distinct_per_backend(self) -> None:
        ir = aggregate_ir()
        _, sql_evidence, _, _ = sql_chain(ir)
        _, mongo_evidence, _, _ = mongo_chain(ir)
        assert compilation_evidence_fingerprint(
            sql_evidence
        ) != compilation_evidence_fingerprint(mongo_evidence)


class TestCapabilityMismatch:
    def test_contains_compiles_on_sql_but_mongo_compiler_fails_closed(self) -> None:
        ir = contains_ir()
        result = compile_sql(ir, context=sql_context(ir))
        assert "LIKE" in result.artifact
        with pytest.raises(MongoCompileError):
            compile_mongo(ir, context=mongo_context(ir))

    def test_mongo_guard_boundary_rejects_contains_capability(self) -> None:
        # Even a Mongo artifact that somehow carried the contains predicate
        # is rejected before execution: the adapter never declared the
        # capability, so the shared boundary fails closed.
        ir = contains_ir()
        context = mongo_context(ir)
        evidence = CompilationEvidence(
            ir_version=ir.ir_version,
            ir_fingerprint=ir.fingerprint,
            source_id=ir.source_id,
            operation="select",
            adapter_type="mongodb",
            compiler_identity="mongodb-compiler",
            compiler_version="1.0.0",
            artifact_fingerprint=SECOND,
        )
        guard = ArtifactGuardResult(
            accepted=True,
            fingerprint=SECOND,
            guard_identity="mongodb-artifact-guard",
            artifact_fingerprint=SECOND,
            obligations_verified=ir.filter_fingerprints(),
            bounded_rows=ir.limit,
        )
        authorization = issue_authorization(context, evidence, guard)
        reasons = verify_pre_execution_guard(
            context=context,
            evidence=evidence,
            guard=guard,
            authorization=authorization,
        )
        assert "adapter lacks required capability 'contains'" in reasons

    def test_grouped_ir_passes_sql_but_mongo_boundary_rejects_grouping(self) -> None:
        ir = grouped_ir()
        sql_context_, sql_evidence, sql_guard, _ = sql_chain(ir)
        assert sql_guard.accepted
        sql_reasons = verify_pre_execution_guard(
            context=sql_context_,
            evidence=sql_evidence,
            guard=sql_guard,
            authorization=issue_authorization(sql_context_, sql_evidence, sql_guard),
        )
        assert sql_reasons == ()
        # The Mongo compiler and guard accept the grouped artifact, but the
        # capability boundary rejects it: grouping is not declared.
        mongo_context_, mongo_evidence, mongo_guard, _ = mongo_chain(ir)
        assert mongo_guard.accepted
        mongo_reasons = verify_pre_execution_guard(
            context=mongo_context_,
            evidence=mongo_evidence,
            guard=mongo_guard,
            authorization=issue_authorization(mongo_context_, mongo_evidence, mongo_guard),
        )
        assert "adapter lacks required capability 'grouping'" in mongo_reasons


class TestMandatoryFilterParity:
    def test_obligations_verified_on_both_backends(self) -> None:
        ir = aggregate_ir()
        expected = ir.filter_fingerprints()
        assert expected
        _, _, sql_guard, _ = sql_chain(ir)
        _, _, mongo_guard, _ = mongo_chain(ir)
        assert sql_guard.obligations_verified == expected
        assert mongo_guard.obligations_verified == expected

    def test_dropped_filter_rejected_on_both_backends(self) -> None:
        ir = aggregate_ir()
        sql_context_, sql_evidence, sql_guard, sql_artifact = sql_chain(ir)
        mongo_context_, mongo_evidence, mongo_guard, mongo_artifact = mongo_chain(ir)
        assert verify_pre_execution_guard(
            context=sql_context_,
            evidence=sql_evidence,
            guard=sql_guard,
            authorization=issue_authorization(sql_context_, sql_evidence, sql_guard),
        ) == ()
        assert verify_pre_execution_guard(
            context=mongo_context_,
            evidence=mongo_evidence,
            guard=mongo_guard,
            authorization=issue_authorization(
                mongo_context_, mongo_evidence, mongo_guard
            ),
        ) == ()

        sql_adapter = SqlQueryAdapter(
            dialect="sqlite",
            allowed_objects=frozenset({"orders_table"}),
            allowed_columns=frozenset({"region", "total_amount", "status"}),
            max_rows=1_000,
        )
        tampered_sql = drop_sql_filter(sql_artifact)
        with pytest.raises(SQLGuardError):
            sql_adapter.validate(
                sql_adapter.parse(tampered_sql, ValidationContext()),
                ValidationContext(
                    required_obligation_fingerprints=ir.filter_fingerprints(),
                    field_bindings=FIELD_BINDINGS,
                ),
            )

        mongo_adapter = MongoQueryAdapter(
            config=MongoAdapterConfig(
                allowed_collections=frozenset({"orders_table"}),
                allowed_fields=frozenset({"region", "total_amount", "status"}),
                max_limit=1_000,
            )
        )
        tampered_mql = drop_mongo_filter(mongo_artifact)
        with pytest.raises(MongoAdapterError):
            mongo_adapter.validate(
                mongo_adapter.parse(tampered_mql, ValidationContext()),
                ValidationContext(
                    required_obligation_fingerprints=ir.filter_fingerprints(),
                    field_bindings=FIELD_BINDINGS,
                ),
            )

    def test_bound_rows_carried_on_both_backends(self) -> None:
        ir = aggregate_ir()
        _, _, sql_guard, _ = sql_chain(ir)
        _, _, mongo_guard, _ = mongo_chain(ir)
        assert sql_guard.bounded_rows == ir.limit == 100
        assert mongo_guard.bounded_rows == ir.limit == 100


class TestBoundsParity:
    def test_guard_bound_above_effective_limits_rejected_on_both(self) -> None:
        ir = aggregate_ir()
        tight = EffectiveLimits(max_rows=50)
        sql_context_, sql_evidence, sql_guard, _ = sql_chain(
            ir, context=sql_context(ir, effective_limits=tight)
        )
        mongo_context_, mongo_evidence, mongo_guard, _ = mongo_chain(
            ir, context=mongo_context(ir, effective_limits=tight)
        )
        sql_reasons = verify_pre_execution_guard(
            context=sql_context_,
            evidence=sql_evidence,
            guard=sql_guard,
            authorization=issue_authorization(sql_context_, sql_evidence, sql_guard),
        )
        mongo_reasons = verify_pre_execution_guard(
            context=mongo_context_,
            evidence=mongo_evidence,
            guard=mongo_guard,
            authorization=issue_authorization(
                mongo_context_, mongo_evidence, mongo_guard
            ),
        )
        assert any("exceeds the effective limits" in r for r in sql_reasons)
        assert any("exceeds the effective limits" in r for r in mongo_reasons)


class TestAuthorizationMismatchParity:
    def test_valid_authorization_verifies_on_both_backends(self) -> None:
        ir = aggregate_ir()
        sql_context_, sql_evidence, sql_guard, _ = sql_chain(ir)
        mongo_context_, mongo_evidence, mongo_guard, _ = mongo_chain(ir)
        assert verify_pre_execution_guard(
            context=sql_context_,
            evidence=sql_evidence,
            guard=sql_guard,
            authorization=issue_authorization(sql_context_, sql_evidence, sql_guard),
        ) == ()
        assert verify_pre_execution_guard(
            context=mongo_context_,
            evidence=mongo_evidence,
            guard=mongo_guard,
            authorization=issue_authorization(
                mongo_context_, mongo_evidence, mongo_guard
            ),
        ) == ()

    def test_wrong_artifact_authorization_rejected_on_both(self) -> None:
        ir = aggregate_ir()
        sql_context_, sql_evidence, sql_guard, _ = sql_chain(ir)
        mongo_context_, mongo_evidence, mongo_guard, _ = mongo_chain(ir)
        for context, evidence, guard in (
            (sql_context_, sql_evidence, sql_guard),
            (mongo_context_, mongo_evidence, mongo_guard),
        ):
            authorization = issue_authorization(
                context, evidence, guard, artifact_fingerprint=SECOND
            )
            reasons = verify_pre_execution_guard(
                context=context,
                evidence=evidence,
                guard=guard,
                authorization=authorization,
            )
            assert any(
                "not bound to the guarded artifact" in reason for reason in reasons
            )

    def test_wrong_ir_authorization_rejected_on_both(self) -> None:
        ir = aggregate_ir()
        sql_context_, sql_evidence, sql_guard, _ = sql_chain(ir)
        mongo_context_, mongo_evidence, mongo_guard, _ = mongo_chain(ir)
        for context, evidence, guard in (
            (sql_context_, sql_evidence, sql_guard),
            (mongo_context_, mongo_evidence, mongo_guard),
        ):
            authorization = issue_authorization(
                context, evidence, guard, ir_fingerprint=SECOND
            )
            reasons = verify_pre_execution_guard(
                context=context,
                evidence=evidence,
                guard=guard,
                authorization=authorization,
            )
            assert any(
                "not bound to the compiled IR" in reason for reason in reasons
            )


class TestProtectedResultLineageParity:
    def _lineage(
        self, context: CompilationContext, evidence: CompilationEvidence, guard: ArtifactGuardResult
    ) -> ResultLineageEvidence:
        return ResultLineageEvidence(
            result_fingerprint="sha256:" + "ef" * 32,
            artifact_fingerprint=guard.artifact_fingerprint,
            guard_fingerprint=guard.fingerprint,
            ir_fingerprint=evidence.ir_fingerprint,
            view_fingerprint=evidence.view_fingerprint,
            bundle_fingerprint=evidence.bundle_fingerprint,
            policy_fingerprint=evidence.policy_fingerprint,
            authorization_id="authz-parity-1",
            adapter_type=evidence.adapter_type,
            compiler_identity=evidence.compiler_identity,
            compiler_version=evidence.compiler_version,
        )

    def test_lineage_links_both_backends_to_one_ir(self) -> None:
        ir = aggregate_ir()
        sql_context_, sql_evidence, sql_guard, _ = sql_chain(ir)
        mongo_context_, mongo_evidence, mongo_guard, _ = mongo_chain(ir)
        sql_lineage = self._lineage(sql_context_, sql_evidence, sql_guard)
        mongo_lineage = self._lineage(mongo_context_, mongo_evidence, mongo_guard)
        assert sql_lineage.ir_fingerprint == mongo_lineage.ir_fingerprint == ir.fingerprint
        assert sql_lineage.adapter_type == "sql"
        assert mongo_lineage.adapter_type == "mongodb"
        assert sql_lineage.artifact_fingerprint != mongo_lineage.artifact_fingerprint
        assert sql_lineage.guard_fingerprint != mongo_lineage.guard_fingerprint
        assert result_lineage_fingerprint(sql_lineage) != result_lineage_fingerprint(
            mongo_lineage
        )

    def test_lineage_serializes_fingerprints_only(self) -> None:
        ir = aggregate_ir()
        sql_context_, sql_evidence, sql_guard, _ = sql_chain(ir)
        lineage = self._lineage(sql_context_, sql_evidence, sql_guard)
        dumped = json.loads(lineage.model_dump_json())
        assert "rows" not in dumped
        assert "result_value" not in dumped
        for key, value in dumped.items():
            if key in {
                "result_fingerprint",
                "artifact_fingerprint",
                "guard_fingerprint",
                "ir_fingerprint",
                "view_fingerprint",
                "bundle_fingerprint",
                "policy_fingerprint",
            }:
                assert value is None or value.startswith("sha256:")
