"""Shared compiler-governance boundary contract tests (DDS-019).

Covers task 5.1: the immutable compilation context, safe serialization of
compilation/guard/lineage evidence (sensitive material excluded), stable
evidence fingerprints, the mandatory guard ordering, and pre-execution
rejection of stale identities (IR version/fingerprint, adapter profile,
artifact guard, capabilities, obligations, bounds, and authorization).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from nl2data_core.adapters.models import AdapterCapabilities, AsyncMode, ValidationContext
from nl2data_core.adapters.sql.adapter import SqlQueryAdapter
from nl2data_core.adapters.sql.compile import SQLCompileError, compile_sql
from nl2data_core.adapters.sql.models import sql_artifact_fingerprint
from nl2data_core.compilation.contract import (
    ArtifactGuardResult,
    CompilationContext,
    CompilationEvidence,
    ResultLineageEvidence,
    artifact_guard_evidence_fingerprint,
    compilation_evidence_fingerprint,
    result_lineage_fingerprint,
    verify_pre_execution_guard,
)
from nl2data_core.governance.authorization import AuthorizationIssuer
from nl2data_core.governance.models import (
    EffectiveLimits,
    PolicyScope,
)
from nl2data_core.planning.ir.fixtures import golden_binding, golden_ir
from nl2data_core.planning.ir.models import IRViewReference, SemanticQueryIR
from nl2data_core.planning.ir.validation import validate_ir, verify_ir_fingerprint
from nl2data_core.planning.validation import AuthorizedView
from nl2data_core.workflow.contract import (
    REQUIRED_GATES,
    RuntimeGateError,
    WorkflowGate,
    WorkflowStage,
    validate_stage_entry,
)
from nl2data_core.workflow.models import WorkflowStage as RuntimeStage

DIGEST = "sha256:" + "ab" * 32
SECOND = "sha256:" + "cd" * 32
UTC_2026 = datetime(2026, 1, 1, tzinfo=UTC)


def plain_ir() -> SemanticQueryIR:
    """The golden IR without the extension capability noise (DDS-019).

    Golden IR carries ``customer_risk_flag`` via its extension; the plain
    IR drops the extension so its required capabilities are exactly the
    set the SQL adapter declares and the guard boundary can verify.
    """
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


def sql_capabilities() -> AdapterCapabilities:
    """The capability profile the SQL adapter declares (sqlite profile)."""
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


def sql_context(ir: SemanticQueryIR, **overrides) -> CompilationContext:
    """A valid SQL compilation context for the given IR."""
    return CompilationContext(
        ir=ir,
        adapter_capabilities=sql_capabilities(),
        effective_limits=EffectiveLimits(max_rows=1_000),
        mandatory_filter_fingerprints=ir.filter_fingerprints(),
        compiler_context=golden_binding(),
        **overrides,
    )


def sql_chain(
    ir: SemanticQueryIR | None = None,
) -> tuple[CompilationContext, CompilationEvidence, ArtifactGuardResult, str]:
    """A fully verified SQL chain: context, evidence, guard, artifact."""
    ir = ir or plain_ir()
    context = sql_context(ir)
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
        field_bindings={
            column.physical_name: column.field_id
            for column in golden_binding().column_bindings
        },
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


def issue_authorization(
    context: CompilationContext,
    evidence: CompilationEvidence,
    guard: ArtifactGuardResult,
    *,
    issuer: AuthorizationIssuer | None = None,
    **overrides,
):
    """Issue an authorization bound to the given evidence/guard chain."""
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
    return (issuer or AuthorizationIssuer()).issue(**kwargs)


class TestCompilationContextImmutable:
    def test_context_is_frozen_and_rejects_unknown_fields(self) -> None:
        context = sql_context(plain_ir())
        with pytest.raises((TypeError, ValidationError)):
            context.purpose = "changed"  # type: ignore[misc]
        with pytest.raises(ValidationError):
            CompilationContext(
                ir=plain_ir(),
                adapter_capabilities=sql_capabilities(),
                compiler_context=golden_binding(),
                unexpected_field="smuggled",  # type: ignore[call-arg]
            )

    def test_bundle_parts_must_be_supplied_together(self) -> None:
        with pytest.raises(ValueError):
            sql_context(plain_ir(), bundle_id="bundle-1")
        with pytest.raises(ValueError):
            sql_context(
                plain_ir(), bundle_id="bundle-1", bundle_version="1.0.0"
            )
        context = sql_context(
            plain_ir(),
            bundle_id="bundle-1",
            bundle_version="1.0.0",
            bundle_fingerprint=DIGEST,
        )
        assert context.bundle_fingerprint == DIGEST

    def test_bound_view_requires_fingerprint_and_reference(self) -> None:
        bound_view = AuthorizedView(
            source_id="acme_warehouse",
            field_ids=plain_ir().field_ids(),
            view_id="view-1",
            view_version=1,
            view_fingerprint=DIGEST,
        )
        with pytest.raises(ValueError):
            sql_context(plain_ir(), view=bound_view)
        with pytest.raises(ValueError):
            sql_context(plain_ir(), view=bound_view, view_fingerprint=DIGEST)
        reference = IRViewReference(
            view_id="view-1", view_version=1, view_fingerprint=DIGEST
        )
        context = sql_context(
            plain_ir(),
            view=bound_view,
            view_fingerprint=DIGEST,
            view_reference=reference,
        )
        assert context.view is bound_view

    def test_unbound_view_keeps_legacy_shape(self) -> None:
        view = AuthorizedView(
            source_id="acme_warehouse", field_ids=plain_ir().field_ids()
        )
        context = sql_context(plain_ir(), view=view)
        assert not context.view.view_bound  # type: ignore[union-attr]
        assert context.view_fingerprint is None

    def test_bundle_version_is_an_identifier_not_an_int(self) -> None:
        with pytest.raises(ValidationError):
            sql_context(
                plain_ir(),
                bundle_id="bundle-1",
                bundle_version=1,  # type: ignore[arg-type]
                bundle_fingerprint=DIGEST,
            )

    def test_compiler_rejects_an_ir_different_from_context(self) -> None:
        context = sql_context(plain_ir())
        with pytest.raises(SQLCompileError, match="does not match"):
            compile_sql(golden_ir(), context=context)


class TestEvidenceSafeSerialization:
    def test_sql_evidence_serializes_without_raw_artifacts(self) -> None:
        context, evidence, guard, artifact = sql_chain()
        dumped = evidence.model_dump_json()
        assert artifact  # the raw SQL exists only outside the evidence
        assert "SELECT" not in dumped
        assert "orders_table" not in dumped
        assert "shipped" not in dumped
        assert json.loads(dumped)["artifact_fingerprint"].startswith("sha256:")
        assert guard.artifact_fingerprint == evidence.artifact_fingerprint

    def test_evidence_never_carries_credential_or_identity_keys(self) -> None:
        context, evidence, guard, artifact = sql_chain()
        payload = json.loads(evidence.model_dump_json())
        for key in payload:
            lowered = key.lower()
            assert "credential" not in lowered
            assert "password" not in lowered
            assert "secret" not in lowered
            assert "token" not in lowered
            assert "uri" not in lowered
            assert "tenant_id" not in lowered
            assert "tenant_name" not in lowered
            assert "connection" not in lowered
            assert "driver" not in lowered

    def test_evidence_carries_only_bounded_references(self) -> None:
        context, evidence, guard, artifact = sql_chain()
        for name in (
            "ir_fingerprint",
            "view_fingerprint",
            "bundle_fingerprint",
            "policy_fingerprint",
            "tenant_scope_fingerprint",
            "artifact_fingerprint",
        ):
            value = getattr(evidence, name)
            if value is not None:
                assert value.startswith("sha256:")
        assert evidence.max_rows == 1_000
        assert evidence.compiler_identity == "sql-compiler"
        assert evidence.adapter_type == "sql"

    def test_lineage_serializes_without_result_values(self) -> None:
        context, evidence, guard, artifact = sql_chain()
        lineage = ResultLineageEvidence(
            result_fingerprint=DIGEST,
            artifact_fingerprint=guard.artifact_fingerprint,
            guard_fingerprint=guard.fingerprint,
            ir_fingerprint=evidence.ir_fingerprint,
            view_fingerprint=evidence.view_fingerprint,
            bundle_fingerprint=evidence.bundle_fingerprint,
            policy_fingerprint=evidence.policy_fingerprint,
            authorization_id="authz-1",
            adapter_type=evidence.adapter_type,
            compiler_identity=evidence.compiler_identity,
            compiler_version=evidence.compiler_version,
        )
        dumped = lineage.model_dump_json()
        assert "rows" not in dumped
        assert "result_value" not in dumped
        assert "SELECT" not in dumped

    def test_guard_result_serializes_without_raw_sql(self) -> None:
        context, evidence, guard, artifact = sql_chain()
        dumped = guard.model_dump_json()
        assert "SELECT" not in dumped
        assert json.loads(dumped)["fingerprint"].startswith("sha256:")


class TestEvidenceFingerprintStability:
    def test_compilation_evidence_fingerprint_is_stable(self) -> None:
        context, evidence, guard, artifact = sql_chain()
        first = compilation_evidence_fingerprint(evidence)
        second = compilation_evidence_fingerprint(
            evidence.model_copy(deep=True)
        )
        assert first == second
        changed = evidence.model_copy(update={"artifact_fingerprint": SECOND})
        assert compilation_evidence_fingerprint(changed) != first

    def test_artifact_guard_evidence_fingerprint_is_stable(self) -> None:
        context, evidence, guard, artifact = sql_chain()
        first = artifact_guard_evidence_fingerprint(guard)
        second = artifact_guard_evidence_fingerprint(
            guard.model_copy(deep=True)
        )
        assert first == second
        changed = guard.model_copy(update={"bounded_rows": 50})
        assert artifact_guard_evidence_fingerprint(changed) != first

    def test_result_lineage_fingerprint_is_stable(self) -> None:
        context, evidence, guard, artifact = sql_chain()
        lineage = ResultLineageEvidence(
            result_fingerprint=DIGEST,
            artifact_fingerprint=guard.artifact_fingerprint,
            guard_fingerprint=guard.fingerprint,
            ir_fingerprint=evidence.ir_fingerprint,
            authorization_id="authz-1",
            adapter_type="sql",
            compiler_identity="sql-compiler",
            compiler_version="1.0.0",
        )
        first = result_lineage_fingerprint(lineage)
        second = result_lineage_fingerprint(lineage.model_copy(deep=True))
        assert first == second
        changed = lineage.model_copy(update={"result_fingerprint": SECOND})
        assert result_lineage_fingerprint(changed) != first


class TestGuardOrdering:
    def test_compile_requires_plan_validation_gate(self) -> None:
        with pytest.raises(RuntimeGateError) as excinfo:
            validate_stage_entry(WorkflowStage.COMPILE, gate_evidence={})
        assert "requires current evidence" in str(excinfo.value)
        validate_stage_entry(
            WorkflowStage.COMPILE,
            gate_evidence={WorkflowGate.PLAN_VALIDATION: DIGEST},
        )

    def test_guard_requires_compilation_gate_not_plan_validation(self) -> None:
        # A later gate cannot substitute for the compilation evidence.
        with pytest.raises(RuntimeGateError):
            validate_stage_entry(
                WorkflowStage.GUARD,
                gate_evidence={WorkflowGate.PLAN_VALIDATION: DIGEST},
            )
        validate_stage_entry(
            WorkflowStage.GUARD,
            gate_evidence={WorkflowGate.COMPILATION: DIGEST},
        )

    def test_execute_requires_all_eight_gates(self) -> None:
        assert len(REQUIRED_GATES[WorkflowStage.EXECUTE]) == 8
        complete = {
            WorkflowGate.TENANT_SCOPE,
            WorkflowGate.PLAN_VALIDATION,
            WorkflowGate.COMPILATION,
            WorkflowGate.ARTIFACT_GUARD,
            WorkflowGate.GOVERNANCE,
            WorkflowGate.ARTIFACT_VALIDATION,
            WorkflowGate.AUTHORIZATION,
            WorkflowGate.DEADLINE,
        }
        # Seven of eight gates still fail closed before adapter execution.
        for missing in complete:
            partial = {gate: DIGEST for gate in complete if gate is not missing}
            with pytest.raises(RuntimeGateError) as excinfo:
                validate_stage_entry(WorkflowStage.EXECUTE, gate_evidence=partial)
            assert missing.value in str(excinfo.value)
        validate_stage_entry(
            WorkflowStage.EXECUTE,
            gate_evidence={gate: DIGEST for gate in complete},
        )

    def test_stages_before_compile_require_no_gates(self) -> None:
        for stage in (
            RuntimeStage.INITIALIZE,
            RuntimeStage.MEMORY,
            RuntimeStage.INTENT,
            RuntimeStage.PLAN,
            RuntimeStage.VALIDATE,
        ):
            assert stage not in REQUIRED_GATES
            validate_stage_entry(stage, gate_evidence={})


class TestStaleIdentityRejection:
    def test_valid_chain_verifies_with_empty_reasons(self) -> None:
        context, evidence, guard, artifact = sql_chain()
        authorization = issue_authorization(context, evidence, guard)
        reasons = verify_pre_execution_guard(
            context=context,
            evidence=evidence,
            guard=guard,
            authorization=authorization,
        )
        assert reasons == ()

    def test_stale_ir_version_is_rejected(self) -> None:
        context, evidence, guard, artifact = sql_chain()
        stale = evidence.model_copy(update={"ir_version": 2})
        reasons = verify_pre_execution_guard(
            context=context,
            evidence=stale,
            guard=guard,
            authorization=issue_authorization(context, evidence, guard),
        )
        assert any("IR version mismatch" in reason for reason in reasons)

    def test_stale_ir_fingerprint_is_rejected(self) -> None:
        context, evidence, guard, artifact = sql_chain()
        stale = evidence.model_copy(update={"ir_fingerprint": SECOND})
        reasons = verify_pre_execution_guard(
            context=context,
            evidence=stale,
            guard=guard,
            authorization=issue_authorization(context, evidence, guard),
        )
        assert any("does not match the current IR" in reason for reason in reasons)

    def test_adapter_profile_mismatch_is_rejected(self) -> None:
        context, evidence, guard, artifact = sql_chain()
        stale = evidence.model_copy(update={"adapter_type": "mongodb"})
        reasons = verify_pre_execution_guard(
            context=context,
            evidence=stale,
            guard=guard,
            authorization=issue_authorization(context, evidence, guard),
        )
        assert any("adapter profile" in reason for reason in reasons)

    def test_rejected_guard_is_rejected(self) -> None:
        context, evidence, guard, artifact = sql_chain()
        rejected = guard.model_copy(update={"accepted": False})
        reasons = verify_pre_execution_guard(
            context=context,
            evidence=evidence,
            guard=rejected,
            authorization=issue_authorization(context, evidence, guard),
        )
        assert "artifact guard rejected the artifact" in reasons

    def test_guard_artifact_mismatch_is_rejected(self) -> None:
        context, evidence, guard, artifact = sql_chain()
        mismatched = guard.model_copy(update={"artifact_fingerprint": SECOND})
        reasons = verify_pre_execution_guard(
            context=context,
            evidence=evidence,
            guard=mismatched,
            authorization=issue_authorization(context, evidence, guard),
        )
        assert any("does not match the compiled artifact" in reason for reason in reasons)

    def test_unsupported_capability_is_rejected(self) -> None:
        context, evidence, guard, artifact = sql_chain()
        narrowed = context.model_copy(
            update={
                "adapter_capabilities": context.adapter_capabilities.model_copy(
                    update={"features": frozenset({"read_only"})}
                )
            }
        )
        reasons = verify_pre_execution_guard(
            context=narrowed,
            evidence=evidence,
            guard=guard,
            authorization=issue_authorization(context, evidence, guard),
        )
        assert any("adapter lacks required capability" in reason for reason in reasons)

    def test_missing_obligation_is_rejected(self) -> None:
        context, evidence, guard, artifact = sql_chain()
        extra = context.model_copy(
            update={"mandatory_filter_fingerprints": frozenset({SECOND})}
        )
        reasons = verify_pre_execution_guard(
            context=extra,
            evidence=evidence,
            guard=guard,
            authorization=issue_authorization(context, evidence, guard),
        )
        assert any("not enforced by the guarded artifact" in reason for reason in reasons)

    def test_unbounded_guard_is_rejected(self) -> None:
        context, evidence, guard, artifact = sql_chain()
        unbounded = guard.model_copy(update={"bounded_rows": 5_000})
        reasons = verify_pre_execution_guard(
            context=context,
            evidence=evidence,
            guard=unbounded,
            authorization=issue_authorization(context, evidence, guard),
        )
        assert any("exceeds the effective limits" in reason for reason in reasons)

    def test_missing_authorization_is_rejected(self) -> None:
        context, evidence, guard, artifact = sql_chain()
        reasons = verify_pre_execution_guard(
            context=context, evidence=evidence, guard=guard, authorization=None
        )
        assert "execution authorization is missing" in reasons

    def test_expired_authorization_is_rejected(self) -> None:
        context, evidence, guard, artifact = sql_chain()
        past_clock = AuthorizationIssuer(clock=lambda: UTC_2026)
        authorization = issue_authorization(
            context, evidence, guard, issuer=past_clock
        )
        reasons = verify_pre_execution_guard(
            context=context,
            evidence=evidence,
            guard=guard,
            authorization=authorization,
            now=datetime(2026, 1, 2, tzinfo=UTC),
        )
        assert any("authorization has expired" in reason for reason in reasons)

    def test_authorization_artifact_mismatch_is_rejected(self) -> None:
        context, evidence, guard, artifact = sql_chain()
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

    def test_authorization_ir_mismatch_is_rejected(self) -> None:
        context, evidence, guard, artifact = sql_chain()
        authorization = issue_authorization(
            context, evidence, guard, ir_fingerprint=SECOND
        )
        reasons = verify_pre_execution_guard(
            context=context,
            evidence=evidence,
            guard=guard,
            authorization=authorization,
        )
        assert any("not bound to the compiled IR" in reason for reason in reasons)

    def test_authorization_adapter_mismatch_is_rejected(self) -> None:
        context, evidence, guard, artifact = sql_chain()
        authorization = issue_authorization(
            context, evidence, guard, adapter_type="mongodb"
        )
        reasons = verify_pre_execution_guard(
            context=context,
            evidence=evidence,
            guard=guard,
            authorization=authorization,
        )
        assert any("does not match the adapter" in reason for reason in reasons)


class TestFingerprintIntegrity:
    def test_plain_ir_is_self_consistent(self) -> None:
        ir = plain_ir()
        assert verify_ir_fingerprint(ir) is True
        assert validate_ir(ir).valid is True

    def test_artifact_fingerprint_matches_evidence_identity(self) -> None:
        context, evidence, guard, artifact = sql_chain()
        assert evidence.artifact_fingerprint == sql_artifact_fingerprint(
            artifact, "sqlite"
        )
