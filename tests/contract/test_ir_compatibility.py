"""Compatibility bridge and golden compilation tests (DDS-019).

Proves existing plan callers keep governed validation, fingerprints, and
adapter behavior across the plan <-> IR boundary: ``plan_to_ir`` is lossless
for the logical core (filter fingerprints stay stable), physical bindings
never enter the canonical IR, and SQL and MongoDB compile the golden plan
and the golden IR into logically identical physical artifacts.
"""

from __future__ import annotations

import json

from nl2data_core.adapters.mongodb.compile import (
    COMPILER_IDENTITY as MONGO_COMPILER_IDENTITY,
)
from nl2data_core.adapters.mongodb.compile import (
    COMPILER_VERSION as MONGO_COMPILER_VERSION,
)
from nl2data_core.adapters.mongodb.compile import (
    compile_mongo_ir,
    compile_mongo_plan,
)
from nl2data_core.adapters.sql.compile import (
    COMPILER_IDENTITY as SQL_COMPILER_IDENTITY,
)
from nl2data_core.adapters.sql.compile import (
    COMPILER_VERSION as SQL_COMPILER_VERSION,
)
from nl2data_core.adapters.sql.compile import (
    SQLCompileError,
    compile_ir,
    compile_plan,
)
from nl2data_core.planning.ir.compat import ir_to_plan, plan_to_ir
from nl2data_core.planning.ir.fixtures import golden_ir, golden_plan
from nl2data_core.planning.ir.models import SemanticQueryIR
from nl2data_core.planning.ir.validation import validate_ir, verify_ir_fingerprint


class TestPlanToIR:
    def test_logical_core_is_preserved(self) -> None:
        plan = golden_plan()
        ir = plan_to_ir(plan)
        assert [s.selection_id for s in ir.selections] == [s.selection_id for s in plan.selections]
        assert [s.field_id for s in ir.selections] == [s.field_id for s in plan.selections]
        assert [s.alias for s in ir.selections] == [s.alias for s in plan.selections]
        assert [s.aggregation for s in ir.selections] == [s.aggregation for s in plan.selections]
        assert [(f.filter_id, f.field_id, f.operator, f.value) for f in ir.filters] == [
            (f.filter_id, f.field_id, f.operator, f.value) for f in plan.filters
        ]
        assert [(o.ordering_id, o.field_id, o.direction) for o in ir.orderings] == [
            (o.ordering_id, o.field_id, o.direction) for o in plan.orderings
        ]
        assert ir.limit == plan.limit
        assert ir.source_id == plan.source_id
        assert ir.root_entity_id == plan.root_entity_id
        assert ir.provenance.source_id == plan.lineage.source_id
        assert ir.provenance.root_entity_id == plan.lineage.root_entity_id
        assert ir.provenance.catalog_fingerprint == plan.lineage.catalog_fingerprint
        assert ir.provenance.policy_view_fingerprint == plan.lineage.policy_view_fingerprint

    def test_filter_fingerprints_stay_stable(self) -> None:
        plan = golden_plan()
        ir = plan_to_ir(plan)
        assert ir.filter_fingerprints() == plan.filter_fingerprints()

    def test_derived_shape_and_groupings(self) -> None:
        ir = plan_to_ir(golden_plan())
        assert ir.result_shape.kind == "grouped_rows"
        assert [(g.field_id,) for g in ir.groupings] == [("region",)]
        plain_plan = golden_plan().model_copy(
            update={"selections": golden_plan().selections[:1]}
        )
        plain = plan_to_ir(plain_plan)
        assert plain.result_shape.kind == "rows"
        assert plain.groupings == ()

    def test_required_capabilities_are_derived(self) -> None:
        assert plan_to_ir(golden_plan()).required_capabilities == (
            "aggregation",
            "grouping",
            "list_ops",
            "ordering",
        )

    def test_ir_id_is_derived_from_plan_fingerprint(self) -> None:
        plan = golden_plan()
        assert plan_to_ir(plan).ir_id == f"ir-{plan.fingerprint[-16:]}"

    def test_translation_is_deterministic(self) -> None:
        assert plan_to_ir(golden_plan()).fingerprint == plan_to_ir(golden_plan()).fingerprint

    def test_binding_never_enters_the_ir(self) -> None:
        ir = plan_to_ir(golden_plan())
        assert "binding" not in SemanticQueryIR.model_fields
        assert "orders_table" not in ir.serialize_canonical()
        assert "sqlite" not in ir.serialize_canonical()
        assert verify_ir_fingerprint(ir) is True

    def test_translated_ir_passes_structural_validation(self) -> None:
        assert validate_ir(plan_to_ir(golden_plan())).valid is True


class TestIRToPlan:
    def test_lossy_mapping_preserves_compiler_input(self) -> None:
        ir = golden_ir()
        plan = ir_to_plan(ir, binding=golden_plan().binding)
        assert plan.plan_id == f"plan-{ir.ir_id}"
        assert plan.binding is not None
        assert plan.binding.object_id == "orders_table"
        assert [(s.selection_id, s.field_id, s.aggregation) for s in plan.selections] == [
            (s.selection_id, s.field_id, s.aggregation) for s in ir.selections
        ]
        assert [(f.filter_id, f.field_id, f.operator, f.value) for f in plan.filters] == [
            (f.filter_id, f.field_id, f.operator, f.value) for f in ir.filters
        ]
        assert plan.limit == ir.limit
        assert plan.lineage.source_id == ir.provenance.source_id
        assert plan.lineage.root_entity_id == ir.provenance.root_entity_id
        #: Time context and extensions are dropped by design.
        assert not hasattr(plan, "time_context")
        assert not hasattr(plan, "extensions")

    def test_ir_to_plan_compiles_identically_to_plan(self) -> None:
        plan = ir_to_plan(golden_ir(), binding=golden_plan().binding)
        assert compile_plan(plan) == compile_plan(golden_plan())

    def test_binding_is_caller_supplied(self) -> None:
        assert ir_to_plan(golden_ir()).binding is None
        bound = ir_to_plan(golden_ir(), binding=golden_plan().binding)
        assert bound.binding is not None


class TestRoundTrip:
    def test_round_trip_keeps_the_logical_core(self) -> None:
        original = golden_plan()
        round_tripped = ir_to_plan(plan_to_ir(original), binding=original.binding)
        assert [
            (s.selection_id, s.field_id, s.alias, s.aggregation) for s in round_tripped.selections
        ] == [
            (s.selection_id, s.field_id, s.alias, s.aggregation) for s in original.selections
        ]
        assert [(f.filter_id, f.field_id, f.operator, f.value) for f in round_tripped.filters] == [
            (f.filter_id, f.field_id, f.operator, f.value) for f in original.filters
        ]
        assert [(o.ordering_id, o.field_id, o.direction) for o in round_tripped.orderings] == [
            (o.ordering_id, o.field_id, o.direction) for o in original.orderings
        ]
        assert round_tripped.limit == original.limit
        assert round_tripped.lineage == original.lineage
        assert round_tripped.binding == original.binding

    def test_round_trip_groupings_are_re_derived(self) -> None:
        ir = golden_ir()
        re_derived = plan_to_ir(ir_to_plan(ir, binding=golden_plan().binding))
        assert re_derived.result_shape.kind == "grouped_rows"
        assert [g.field_id for g in re_derived.groupings] == [g.field_id for g in ir.groupings]


class TestGoldenSQLCompilation:
    def test_ir_and_plan_compile_to_identical_sql(self) -> None:
        sql_from_ir = compile_ir(golden_ir(), binding=golden_plan().binding)
        sql_from_plan = compile_plan(golden_plan())
        assert sql_from_ir == sql_from_plan
        assert "GROUP BY region" in sql_from_ir
        assert "ORDER BY total_amount DESC" in sql_from_ir
        assert "LIMIT 100" in sql_from_ir

    def test_sql_compiler_identity_constants(self) -> None:
        assert SQL_COMPILER_IDENTITY == "sql-compiler"
        assert SQL_COMPILER_VERSION == "1.0.0"

    def test_sql_compiler_fails_closed_without_binding(self) -> None:
        import pytest

        plan = ir_to_plan(golden_ir())
        with pytest.raises(SQLCompileError):
            compile_plan(plan)


class TestGoldenMongoCompilation:
    def test_ir_and_plan_compile_to_identical_wire_except_spec_id(self) -> None:
        wire_from_ir = json.loads(compile_mongo_ir(golden_ir(), binding=golden_plan().binding))
        wire_from_plan = json.loads(compile_mongo_plan(golden_plan()))
        assert wire_from_ir["spec_id"] != wire_from_plan["spec_id"]
        shared_keys = (
            "operation",
            "collection",
            "filter",
            "limit",
            "pipeline",
            "sort",
            "projection",
            "skip",
            "tenant_obligation",
            "routing_evidence",
        )
        for key in shared_keys:
            assert wire_from_ir[key] == wire_from_plan[key], key
        assert wire_from_ir["spec_id"] == f"mongo-{golden_ir().fingerprint[-16:]}"
        assert wire_from_plan["spec_id"] == f"mongo-{golden_plan().fingerprint[-16:]}"
        assert wire_from_ir["operation"] == "aggregate"

    def test_mongo_compiler_identity_constants(self) -> None:
        assert MONGO_COMPILER_IDENTITY == "mongodb-compiler"
        assert MONGO_COMPILER_VERSION == "1.0.0"


class TestCrossBackendIdentity:
    def test_same_logical_facts_in_sql_and_mql(self) -> None:
        sql = compile_ir(golden_ir(), binding=golden_plan().binding)
        mql = json.loads(compile_mongo_ir(golden_ir(), binding=golden_plan().binding))
        #: Same logical selection, grouping, ordering, filter, and limit facts.
        assert "GROUP BY region" in sql
        assert "ORDER BY total_amount DESC" in sql
        assert "LIMIT 100" in sql
        pipeline = mql["pipeline"]
        assert pipeline[0] == {
            "$match": {
                "status": {"$eq": "shipped"},
                "region": {"$in": ["north", "south"]},
            }
        }
        assert pipeline[1] == {
            "$group": {"_id": "$region", "order_value": {"$sum": "$total_amount"}}
        }
        assert pipeline[2] == {"$sort": {"order_value": -1}}
        assert pipeline[3] == {"$project": {"region": "$_id", "order_value": 1, "_id": 0}}
        assert pipeline[4] == {"$limit": 100}
        #: The same physical object is targeted by both compilers.
        assert "FROM orders_table" in sql
        assert mql["collection"] == "orders_table"

    def test_logical_fingerprint_links_both_artifacts(self) -> None:
        ir = golden_ir()
        sql = compile_ir(ir, binding=golden_plan().binding)
        mql = compile_mongo_ir(ir, binding=golden_plan().binding)
        assert verify_ir_fingerprint(ir) is True
        assert len(sql) > 0 and len(mql) > 0
        #: The Mongo spec id is derived from the canonical logical fingerprint.
        assert f"mongo-{ir.fingerprint[-16:]}" in mql
