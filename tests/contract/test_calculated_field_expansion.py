"""Contract tests for calculated-field compile-time expansion (v4.2).

Covers D1 (deterministic adapter-native expansion: declared output-type
enforcement via CAST, zero-division policy behavior including the SQLite
fail-closed compile-time rejection), D6 (evidence hash records: sorted by
name, selection-order invariant, expression-free), and D12 (the
expansion-identity drift guard mirroring the planner-identity guard).
"""

from __future__ import annotations

import json

import pytest
from tests.contract.test_compiler_governance_boundaries import (
    issue_authorization,
    sql_chain,
)

from nl2data.errors import ErrorCode, NL2DataError
from nl2data_core.adapters.models import AdapterCapabilities
from nl2data_core.adapters.sql.compile import SQLCompileError, compile_sql
from nl2data_core.canonical import strict_sha256_fingerprint
from nl2data_core.compilation.contract import (
    EXPANSION_IDENTITY_VERSIONING,
    CompilationContext,
    compilation_evidence_fingerprint,
    verify_pre_execution_guard,
)
from nl2data_core.compilation.expansion import (
    EXPANSION_IDENTITY,
    ExpansionError,
    calculated_field_hashes,
    expand_mongo,
    expand_sql,
    resolve_calculated_fields,
    zero_division_supported,
)
from nl2data_core.governance.models import EffectiveLimits
from nl2data_core.planning.ir.models import (
    IRProvenance,
    IRSelection,
    SemanticQueryIR,
)
from nl2data_core.planning.models import (
    ColumnBinding,
    EntityBinding,
    PhysicalBinding,
)
from nl2data_core.views.models import CalculatedField, ExprNode
from nl2data_mongodb.compile import compile_mongo

# -- helpers -----------------------------------------------------------------


def _leaf(field_id: str) -> ExprNode:
    return ExprNode(op="field", field_id=field_id)


def _margin(**overrides) -> CalculatedField:
    """margin = revenue - cost, declared int."""
    payload: dict = {
        "name": "margin",
        "label": "Margin",
        "expression": ExprNode(op="sub", left=_leaf("revenue"), right=_leaf("cost")),
        "output_type": "int",
        "requires": ("revenue", "cost"),
    }
    payload.update(overrides)
    return CalculatedField.model_validate(payload)


def _ratio(**overrides) -> CalculatedField:
    """ratio = revenue / cost, declared float."""
    payload: dict = {
        "name": "ratio",
        "label": "Ratio",
        "expression": ExprNode(op="div", left=_leaf("revenue"), right=_leaf("cost")),
        "output_type": "float",
        "requires": ("revenue", "cost"),
    }
    payload.update(overrides)
    return CalculatedField.model_validate(payload)


def _binding(dialect: str = "sqlite") -> PhysicalBinding:
    return PhysicalBinding(
        object_id="orders_table",
        dialect=dialect,
        column_bindings=(
            ColumnBinding(field_id="revenue", physical_name="revenue", entity_id="orders"),
            ColumnBinding(field_id="cost", physical_name="cost", entity_id="orders"),
        ),
        entity_bindings=(EntityBinding(entity_id="orders", physical_name="orders_table"),),
    )


def _ir(**overrides) -> SemanticQueryIR:
    """A minimal rows IR selecting the margin calculated field."""
    payload: dict = {
        "ir_id": "ir-test",
        "source_id": "src",
        "root_entity_id": "orders",
        "selections": (
            IRSelection(selection_id="s1", field_id="margin"),
            IRSelection(selection_id="s2", field_id="revenue"),
        ),
        "limit": 100,
        "provenance": IRProvenance(source_id="src", root_entity_id="orders"),
        "required_capabilities": ("calculated-fields",),
        "extensions": (),
    }
    payload.update(overrides)
    return SemanticQueryIR.model_validate(payload)


def _capabilities(adapter_type: str = "sql") -> AdapterCapabilities:
    return AdapterCapabilities(
        adapter_type=adapter_type,
        query_language="sql" if adapter_type == "sql" else "mql",
        async_mode="native",
        features=frozenset(
            {"select", "filter", "aggregation", "calculated-fields"}
        ),
    )


def _context(
    ir: SemanticQueryIR,
    binding: PhysicalBinding,
    *,
    definitions: tuple[CalculatedField, ...] | None = (),
    **overrides,
) -> CompilationContext:
    payload: dict = {
        "ir": ir,
        "adapter_capabilities": _capabilities("sql"),
        "effective_limits": EffectiveLimits(max_rows=1_000),
        "mandatory_filter_fingerprints": ir.filter_fingerprints(),
        "compiler_context": binding,
        "calculated_fields": definitions,
        "expansion_identity": EXPANSION_IDENTITY,
    }
    payload.update(overrides)
    return CompilationContext.model_validate(payload)


def _mongo_context(ir: SemanticQueryIR, binding: PhysicalBinding, **overrides):
    context = _context(ir, binding, **overrides)
    return context.model_copy(
        update={
            "adapter_capabilities": _capabilities("mongodb"),
        }
    )


class TestResolveCalculatedFields:
    """Resolution is the sorted intersection of selections and declarations."""

    def test_only_referenced_definitions_resolve(self) -> None:
        ir = _ir()
        context = _context(ir, _binding(), definitions=(_margin(), _ratio()))
        resolved = resolve_calculated_fields(ir, context)
        assert list(resolved) == ["margin"]
        assert resolved["margin"].output_type == "int"

    def test_resolution_is_selection_order_invariant(self) -> None:
        first = _ir()
        second = _ir(
            selections=(
                IRSelection(selection_id="s2", field_id="revenue"),
                IRSelection(selection_id="s1", field_id="margin"),
            ),
        )
        definitions = (_margin(), _ratio())
        context = _context(first, _binding(), definitions=definitions)
        assert list(resolve_calculated_fields(first, context)) == ["margin"]
        assert resolve_calculated_fields(second, context) == resolve_calculated_fields(
            first, context
        )

    def test_unknown_names_are_ignored(self) -> None:
        ir = _ir(
            selections=(
                IRSelection(selection_id="s1", field_id="mystery"),
                IRSelection(selection_id="s2", field_id="revenue"),
            ),
        )
        context = _context(ir, _binding(), definitions=(_margin(),))
        assert resolve_calculated_fields(ir, context) == {}

    def test_tampered_definition_fails_revalidation_cf_001(self) -> None:
        """model_construct bypasses the validators, so the expanded payload
        re-validation is the only thing standing between a tampered
        definition and the artifact (defense-in-depth, D1/CF_001)."""
        tampered = CalculatedField.model_construct(
            name="bad",
            label="Bad",
            expression=ExprNode.model_construct(
                op="pow", field_id=None, const=None, left=None, right=None
            ),
            output_type="int",
            requires=(),
        )
        assert tampered.expression.op not in {"field", "const", "add", "sub", "mul", "div"}
        with pytest.raises(ExpansionError) as excinfo:
            expand_sql(
                tampered,
                binding=_binding(),
                dialect="sqlite",
                resolve_leaf=lambda field_id: field_id,
            )
        assert excinfo.value.code == ErrorCode.CALCULATED_FIELD_REJECTED


class TestSqlExpansion:
    """D1: SQL output is adapter-native with declared output-type CASTs."""

    def test_int_output_is_cast_enforced(self) -> None:
        expanded = expand_sql(
            _margin(),
            binding=_binding(),
            dialect="sqlite",
            resolve_leaf=lambda field_id: field_id,
        )
        assert expanded == "CAST((revenue - cost) AS INTEGER)"

    def test_null_policy_guards_the_division_sqlite(self) -> None:
        expanded = expand_sql(
            _ratio(),
            binding=_binding(),
            dialect="sqlite",
            resolve_leaf=lambda field_id: field_id,
        )
        assert expanded == (
            "CAST((CASE WHEN (cost) = 0 THEN NULL "
            "ELSE (CAST(revenue AS REAL) / CAST(cost AS REAL)) END) AS REAL)"
        )

    def test_error_policy_is_unguarded_true_division_postgres(self) -> None:
        expanded = expand_sql(
            _ratio(zero_division_policy="error"),
            binding=_binding("postgres"),
            dialect="postgres",
            resolve_leaf=lambda field_id: field_id,
        )
        assert expanded == (
            "CAST((CAST(revenue AS DOUBLE PRECISION) "
            "/ CAST(cost AS DOUBLE PRECISION)) AS DOUBLE PRECISION)"
        )

    def test_artifact_carries_the_expanded_selection(self) -> None:
        ir = _ir()
        result = compile_sql(ir, context=_context(ir, _binding(), definitions=(_margin(),)))
        assert (
            "CAST((revenue - cost) AS INTEGER) AS margin" in result.artifact
        )
        assert result.artifact.endswith("LIMIT 100")

    def test_aggregation_applies_to_the_expanded_expression(self) -> None:
        ir = _ir(
            selections=(
                IRSelection(selection_id="s1", field_id="margin", aggregation="sum"),
            ),
            required_capabilities=("aggregation", "calculated-fields"),
            result_shape={"kind": "grouped_rows"},
        )
        result = compile_sql(ir, context=_context(ir, _binding(), definitions=(_margin(),)))
        assert "SUM(CAST((revenue - cost) AS INTEGER)) AS margin" in result.artifact

    def test_sqlite_rejects_unenforceable_error_policy(self) -> None:
        """SQLite yields NULL for division by zero, so the error policy
        would silently degrade; the compile fails closed instead (N1)."""
        ir = _ir(selections=(IRSelection(selection_id="s1", field_id="ratio"),))
        with pytest.raises(SQLCompileError, match="cannot enforce"):
            compile_sql(
                ir,
                context=_context(
                    ir,
                    _binding(),
                    definitions=(_ratio(zero_division_policy="error"),),
                ),
            )

    def test_zero_division_support_table(self) -> None:
        assert zero_division_supported("null", "sqlite") is True
        assert zero_division_supported("error", "sqlite") is False
        assert zero_division_supported("error", "postgres") is True
        assert zero_division_supported("error", "mongodb") is True


class TestMongoExpansion:
    """D1: MQL output uses $cond guards and native $divide semantics."""

    def _mongo_expanded(self, calculated: CalculatedField) -> dict:
        return expand_mongo(
            calculated,
            binding=_binding(),
            resolve_leaf=lambda field_id: field_id,
        )

    def test_arithmetic_expands_to_mql_operators(self) -> None:
        assert self._mongo_expanded(_margin()) == {
            "$toLong": {"$subtract": ["$revenue", "$cost"]}
        }

    def test_null_policy_guards_the_division(self) -> None:
        assert self._mongo_expanded(_ratio()) == {
            "$toDouble": {
                "$cond": [
                    {"$eq": ["$cost", 0]},
                    None,
                    {"$divide": ["$revenue", "$cost"]},
                ]
            }
        }

    def test_error_policy_is_unguarded_divide(self) -> None:
        assert self._mongo_expanded(_ratio(zero_division_policy="error")) == {
            "$toDouble": {"$divide": ["$revenue", "$cost"]}
        }

    def test_row_level_cf_selection_forces_the_aggregate_pipeline(self) -> None:
        ir = _ir()
        binding = _binding()
        context = _mongo_context(ir, binding, definitions=(_margin(),))
        result = compile_mongo(ir, context=context)
        spec = json.loads(result.artifact)
        assert spec["operation"] == "aggregate"
        project = next(stage["$project"] for stage in spec["pipeline"] if "$project" in stage)
        assert project["margin"] == {
            "$toLong": {"$subtract": ["$revenue", "$cost"]}
        }

    def test_cf_less_queries_stay_row_level_find(self) -> None:
        ir = _ir(selections=(IRSelection(selection_id="s2", field_id="revenue"),))
        binding = _binding()
        context = _mongo_context(ir, binding, definitions=(_margin(),))
        result = compile_mongo(ir, context=context)
        spec = json.loads(result.artifact)
        assert spec["operation"] == "find"

    def test_aggregated_cf_selection_uses_the_accumulator(self) -> None:
        ir = _ir(
            selections=(
                IRSelection(selection_id="s1", field_id="margin", aggregation="sum"),
            ),
            required_capabilities=("aggregation", "calculated-fields"),
            result_shape={"kind": "grouped_rows"},
        )
        binding = _binding()
        context = _mongo_context(ir, binding, definitions=(_margin(),))
        result = compile_mongo(ir, context=context)
        spec = json.loads(result.artifact)
        group = next(stage["$group"] for stage in spec["pipeline"] if "$group" in stage)
        assert group["margin"] == {
            "$sum": {"$toLong": {"$subtract": ["$revenue", "$cost"]}}
        }

    def test_count_cf_excludes_null_expansion_results(self) -> None:
        ir = _ir(
            selections=(
                IRSelection(selection_id="s1", field_id="ratio", aggregation="count"),
            ),
            required_capabilities=("aggregation", "calculated-fields"),
            result_shape={"kind": "grouped_rows"},
        )
        context = _mongo_context(ir, _binding(), definitions=(_ratio(),))
        spec = json.loads(compile_mongo(ir, context=context).artifact)
        group = next(stage["$group"] for stage in spec["pipeline"] if "$group" in stage)
        assert group["ratio"] == {
            "$sum": {
                "$cond": [
                    {
                        "$eq": [
                            {
                                "$toDouble": {
                                    "$cond": [
                                        {"$eq": ["$cost", 0]},
                                        None,
                                        {"$divide": ["$revenue", "$cost"]},
                                    ]
                                }
                            },
                            None,
                        ]
                    },
                    0,
                    1,
                ]
            }
        }

    def test_compiler_rejects_fabricated_expansion_identity(self) -> None:
        ir = _ir()
        context = _mongo_context(ir, _binding(), definitions=(_margin(),)).model_copy(
            update={"expansion_identity": "fabricated-compiler-v1"}
        )
        with pytest.raises(NL2DataError, match="identity"):
            compile_mongo(ir, context=context)

    def test_error_policy_compiles_for_mongodb(self) -> None:
        """MongoDB can raise on a zero denominator, so the error policy
        compiles; the CF_005 mapping belongs to the execution layer."""
        ir = _ir(selections=(IRSelection(selection_id="s1", field_id="ratio"),))
        binding = _binding()
        context = _mongo_context(
            ir, binding, definitions=(_ratio(zero_division_policy="error"),)
        )
        assert compile_mongo(ir, context=context).artifact

    def test_mongo_compiler_rejects_tampered_definitions(self) -> None:
        ir = _ir(
            selections=(
                IRSelection(selection_id="s1", field_id="bad"),
                IRSelection(selection_id="s2", field_id="revenue"),
            ),
        )
        tampered = CalculatedField.model_construct(
            name="bad",
            label="Bad",
            expression=ExprNode.model_construct(
                op="pow", field_id=None, const=None, left=None, right=None
            ),
            output_type="int",
            requires=(),
        )
        # model_copy bypasses validation, simulating a definition that
        # slipped past the bundle boundary and reaches the compiler.
        context = _mongo_context(ir, _binding(), definitions=(_margin(),))
        context = context.model_copy(update={"calculated_fields": (tampered,)})
        with pytest.raises(NL2DataError) as excinfo:
            compile_mongo(ir, context=context)
        assert excinfo.value.code == ErrorCode.CALCULATED_FIELD_REJECTED


class TestEvidenceHashRecords:
    """D6: frozen name+hash records, sorted, selection-order invariant."""

    def test_records_are_sorted_and_expression_free(self) -> None:
        ir = _ir(
            selections=(
                IRSelection(selection_id="s1", field_id="ratio"),
                IRSelection(selection_id="s2", field_id="margin"),
            ),
        )
        context = _context(ir, _binding(), definitions=(_margin(), _ratio()))
        records = calculated_field_hashes(ir, context)
        assert [record.name for record in records] == ["margin", "ratio"]
        assert records[0].hash == _margin().content_hash()
        assert records[1].hash == _ratio().content_hash()
        for record in records:
            assert set(record.model_dump()) == {"name", "hash"}

    def test_records_are_selection_order_invariant(self) -> None:
        definitions = (_margin(), _ratio())
        first = _ir(
            selections=(
                IRSelection(selection_id="s1", field_id="margin"),
                IRSelection(selection_id="s2", field_id="ratio"),
            ),
        )
        second = _ir(
            selections=(
                IRSelection(selection_id="s2", field_id="ratio"),
                IRSelection(selection_id="s1", field_id="margin"),
            ),
        )
        context = _context(first, _binding(), definitions=definitions)
        assert calculated_field_hashes(first, context) == calculated_field_hashes(
            second, context
        )

    def test_no_references_yield_none(self) -> None:
        ir = _ir(selections=(IRSelection(selection_id="s2", field_id="revenue"),))
        context = _context(ir, _binding(), definitions=(_margin(),))
        assert calculated_field_hashes(ir, context) is None

    def test_compiled_evidence_carries_records_and_identity(self) -> None:
        ir = _ir(
            selections=(
                IRSelection(selection_id="s1", field_id="margin"),
                IRSelection(selection_id="s2", field_id="ratio"),
            ),
        )
        context = _context(ir, _binding(), definitions=(_margin(), _ratio()))
        result = compile_sql(ir, context=context)
        assert result.evidence.expansion_identity == EXPANSION_IDENTITY
        records = result.evidence.calculated_field_hashes
        assert records is not None
        assert [record.name for record in records] == ["margin", "ratio"]

    def test_evidence_without_calculated_fields_is_fingerprint_stable(self) -> None:
        """N6: the two v4.2 evidence members are omitted entirely when
        unset, so pre-v4.2 evidence fingerprints stay byte-identical."""
        ir = _ir(selections=(IRSelection(selection_id="s2", field_id="revenue"),))
        context = _context(
            ir,
            _binding(),
            definitions=(_margin(),),
            expansion_identity=None,
        )
        result = compile_sql(ir, context=context)
        evidence = result.evidence
        assert evidence.calculated_field_hashes is None
        assert evidence.expansion_identity is None
        # The pre-v4.2 evidence payload, pinned key by key: adding the new
        # members unconditionally would change this fingerprint.
        legacy_payload = {
            "ir_version": evidence.ir_version,
            "ir_fingerprint": evidence.ir_fingerprint,
            "source_id": evidence.source_id,
            "operation": evidence.operation,
            "field_ids": sorted(evidence.field_ids),
            "view_fingerprint": evidence.view_fingerprint,
            "bundle_fingerprint": evidence.bundle_fingerprint,
            "policy_fingerprint": evidence.policy_fingerprint,
            "tenant_scope_fingerprint": evidence.tenant_scope_fingerprint,
            "purpose": evidence.purpose,
            "adapter_type": evidence.adapter_type,
            "capability_ids": sorted(evidence.capability_ids),
            "required_capabilities": sorted(evidence.required_capabilities),
            "mandatory_filter_fingerprints": sorted(
                evidence.mandatory_filter_fingerprints
            ),
            "max_rows": evidence.max_rows,
            "max_columns": evidence.max_columns,
            "max_execution_seconds": evidence.max_execution_seconds,
            "max_result_bytes": evidence.max_result_bytes,
            "compiler_identity": evidence.compiler_identity,
            "compiler_version": evidence.compiler_version,
            "artifact_fingerprint": evidence.artifact_fingerprint,
            "join_plan_fingerprint": evidence.join_plan_fingerprint,
            "planner_identity": evidence.planner_identity,
        }
        assert compilation_evidence_fingerprint(evidence) == strict_sha256_fingerprint(
            legacy_payload
        )

    def test_set_members_change_the_evidence_fingerprint(self) -> None:
        ir = _ir()
        compiled = compile_sql(
            ir,
            context=_context(ir, _binding(), definitions=(_margin(),)),
        )
        without = compiled.evidence.model_copy(
            update={"calculated_field_hashes": None, "expansion_identity": None}
        )
        assert (
            compilation_evidence_fingerprint(compiled.evidence)
            != compilation_evidence_fingerprint(without)
        )


class TestExpansionIdentityGuard:
    """D12: the expansion-identity guard mirrors the planner-identity guard."""

    def verify(self, context, evidence, guard, *, expansion_identity_versioning=None):
        return verify_pre_execution_guard(
            context=context,
            evidence=evidence,
            guard=guard,
            authorization=issue_authorization(context, evidence, guard),
            expansion_identity_versioning=expansion_identity_versioning,
        )

    def test_versioning_flag_defaults_to_inactive(self) -> None:
        assert EXPANSION_IDENTITY_VERSIONING is False

    def test_identity_mismatch_is_rejected(self) -> None:
        context, evidence, guard, _ = sql_chain()
        context = context.model_copy(update={"expansion_identity": EXPANSION_IDENTITY})
        evidence = evidence.model_copy(
            update={"expansion_identity": "deterministic-expression-compiler-v0"}
        )
        reasons = self.verify(context, evidence, guard)
        assert any("expansion identity does not match" in reason for reason in reasons)

    def test_context_without_evidence_identity_is_rejected(self) -> None:
        context, evidence, guard, _ = sql_chain()
        context = context.model_copy(update={"expansion_identity": EXPANSION_IDENTITY})
        reasons = self.verify(context, evidence, guard)
        assert any("evidence lacks the expansion identity" in reason for reason in reasons)

    def test_evidence_without_context_identity_is_rejected(self) -> None:
        context, evidence, guard, _ = sql_chain()
        evidence = evidence.model_copy(update={"expansion_identity": EXPANSION_IDENTITY})
        reasons = self.verify(context, evidence, guard)
        assert any(
            "expansion identity the compilation context does not declare" in reason
            for reason in reasons
        )

    def test_matching_identities_verify(self) -> None:
        context, evidence, guard, _ = sql_chain()
        context = context.model_copy(update={"expansion_identity": EXPANSION_IDENTITY})
        evidence = evidence.model_copy(update={"expansion_identity": EXPANSION_IDENTITY})
        assert self.verify(context, evidence, guard) == ()

    def test_both_unset_verifies_unchanged(self) -> None:
        context, evidence, guard, _ = sql_chain()
        assert context.expansion_identity is None
        assert evidence.expansion_identity is None
        assert self.verify(context, evidence, guard) == ()

    def test_active_versioning_rejects_missing_evidence_identity(self) -> None:
        context, evidence, guard, _ = sql_chain()
        reasons = self.verify(
            context, evidence, guard, expansion_identity_versioning=True
        )
        assert any("expansion identity versioning is active" in reason for reason in reasons)

    def test_active_versioning_accepts_a_matching_identity(self) -> None:
        context, evidence, guard, _ = sql_chain()
        context = context.model_copy(update={"expansion_identity": EXPANSION_IDENTITY})
        evidence = evidence.model_copy(update={"expansion_identity": EXPANSION_IDENTITY})
        assert self.verify(
            context, evidence, guard, expansion_identity_versioning=True
        ) == ()

    def test_drift_reasons_are_human_safe(self) -> None:
        context, evidence, guard, _ = sql_chain()
        context = context.model_copy(update={"expansion_identity": EXPANSION_IDENTITY})
        evidence = evidence.model_copy(
            update={"expansion_identity": "deterministic-expression-compiler-v0"}
        )
        for reason in self.verify(context, evidence, guard):
            assert isinstance(reason, str)
            assert "sha256:" not in reason
            assert "\n" not in reason
