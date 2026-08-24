"""IR-native golden compilation tests (DDS-019).

Proves the canonical IR drives both compilers directly: SQL and MongoDB
compile the golden IR into stable physical artifacts, logical fingerprint
identity stays frozen while physical artifact fingerprints stay distinct
per backend, physical bindings never enter the canonical IR, and the
legacy ``SemanticQueryPlan`` surface and the plan <-> IR bridge are absent.
"""

from __future__ import annotations

import importlib
import json

import pytest

from nl2data_core.adapters.models import (
    CompilerArtifactEvidence,
    ValidatedArtifact,
    ValidationContext,
)
from nl2data_core.adapters.mongodb.adapter import MongoQueryAdapter
from nl2data_core.adapters.mongodb.compile import (
    COMPILER_IDENTITY as MONGO_COMPILER_IDENTITY,
)
from nl2data_core.adapters.mongodb.compile import (
    COMPILER_VERSION as MONGO_COMPILER_VERSION,
)
from nl2data_core.adapters.mongodb.compile import compile_mongo_ir
from nl2data_core.adapters.mongodb.models import MongoAdapterConfig
from nl2data_core.adapters.sql.adapter import SqlQueryAdapter
from nl2data_core.adapters.sql.compile import (
    COMPILER_IDENTITY as SQL_COMPILER_IDENTITY,
)
from nl2data_core.adapters.sql.compile import (
    COMPILER_VERSION as SQL_COMPILER_VERSION,
)
from nl2data_core.adapters.sql.compile import SQLCompileError, compile_ir
from nl2data_core.planning.ir.fixtures import (
    GOLDEN_FINGERPRINT,
    golden_binding,
    golden_ir,
)
from nl2data_core.planning.ir.models import SemanticQueryIR
from nl2data_core.planning.ir.validation import validate_ir, verify_ir_fingerprint


class TestLegacySurfaceAbsent:
    def test_legacy_plan_model_is_absent(self) -> None:
        models = importlib.import_module("nl2data_core.planning.models")
        for legacy in (
            "SemanticQueryPlan",
            "SemanticSelection",
            "SemanticFilter",
            "SemanticOrdering",
            "PlanLineage",
            "PlanValidationIssue",
            "PlanValidationResult",
        ):
            assert not hasattr(models, legacy), f"{legacy} must be removed"

    def test_compat_bridge_module_is_absent(self) -> None:
        with pytest.raises(ImportError):
            importlib.import_module("nl2data_core.planning.ir.compat")

    def test_ir_package_exports_no_bridge_symbols(self) -> None:
        ir_package = importlib.import_module("nl2data_core.planning.ir")
        assert not hasattr(ir_package, "plan_to_ir")
        assert not hasattr(ir_package, "ir_to_plan")


class TestGoldenIRCompilation:
    def test_golden_ir_compiles_to_stable_sql(self) -> None:
        sql = compile_ir(golden_ir(), binding=golden_binding())
        assert "GROUP BY region" in sql
        assert "ORDER BY total_amount DESC" in sql
        assert "LIMIT 100" in sql
        assert "FROM orders_table" in sql

    def test_sql_compiler_identity_constants(self) -> None:
        assert SQL_COMPILER_IDENTITY == "sql-compiler"
        assert SQL_COMPILER_VERSION == "1.0.0"

    def test_sql_compiler_fails_closed_without_binding(self) -> None:
        with pytest.raises(SQLCompileError):
            compile_ir(golden_ir(), binding=None)

    def test_mongo_spec_id_is_derived_from_ir_fingerprint(self) -> None:
        ir = golden_ir()
        wire = json.loads(compile_mongo_ir(ir, binding=golden_binding()))
        assert wire["spec_id"] == f"mongo-{ir.fingerprint[-16:]}"
        assert wire["operation"] == "aggregate"
        assert wire["collection"] == "orders_table"

    def test_mongo_compiler_identity_constants(self) -> None:
        assert MONGO_COMPILER_IDENTITY == "mongodb-compiler"
        assert MONGO_COMPILER_VERSION == "1.0.0"


class TestBindingIsCompilerContext:
    def test_binding_never_enters_the_ir(self) -> None:
        ir = golden_ir()
        assert "binding" not in SemanticQueryIR.model_fields
        assert "orders_table" not in ir.serialize_canonical()
        assert "sqlite" not in ir.serialize_canonical()
        assert verify_ir_fingerprint(ir) is True

    def test_golden_ir_passes_structural_validation(self) -> None:
        assert validate_ir(golden_ir()).valid is True


class TestCrossBackendIdentity:
    def test_same_logical_facts_in_sql_and_mql(self) -> None:
        sql = compile_ir(golden_ir(), binding=golden_binding())
        mql = json.loads(compile_mongo_ir(golden_ir(), binding=golden_binding()))
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
        sql = compile_ir(ir, binding=golden_binding())
        mql = compile_mongo_ir(ir, binding=golden_binding())
        assert verify_ir_fingerprint(ir) is True
        assert len(sql) > 0 and len(mql) > 0
        #: The Mongo spec id is derived from the canonical logical fingerprint.
        assert f"mongo-{ir.fingerprint[-16:]}" in mql


CTX = ValidationContext()


def _sql_validated_artifact(sql: str) -> ValidatedArtifact:
    """Run compiled SQL through the guarded adapter lifecycle (no database)."""
    adapter = SqlQueryAdapter(
        dialect="sqlite",
        allowed_objects=frozenset({"orders_table"}),
        allowed_columns=frozenset({"region", "total_amount", "status"}),
        max_rows=1_000,
    )
    return adapter.validate(adapter.parse(sql, CTX), CTX)


def _mongo_validated_artifact(wire: str) -> ValidatedArtifact:
    """Run compiled MQL through the guarded adapter lifecycle (fake profile)."""
    adapter = MongoQueryAdapter(
        config=MongoAdapterConfig(
            allowed_collections=frozenset({"orders_table"}),
            allowed_fields=frozenset({"region", "total_amount", "status"}),
            max_limit=1_000,
        )
    )
    return adapter.validate(adapter.parse(wire, CTX), CTX)


class TestGoldenFingerprintIdentity:
    """Golden logical and physical identity across backends.

    The frozen golden IR carries one stable logical fingerprint; each
    backend artifact carries its own stable physical fingerprint, and
    compiler evidence links both artifacts to the single IR identity.
    """

    def test_golden_ir_fingerprint_is_stable_and_verified(self) -> None:
        ir = golden_ir()
        assert ir.fingerprint == GOLDEN_FINGERPRINT
        assert verify_ir_fingerprint(ir) is True

    def test_artifact_fingerprints_are_stable_per_backend(self) -> None:
        sql_first = _sql_validated_artifact(compile_ir(golden_ir(), binding=golden_binding()))
        sql_second = _sql_validated_artifact(compile_ir(golden_ir(), binding=golden_binding()))
        assert sql_first.fingerprint == sql_second.fingerprint
        mongo_first = _mongo_validated_artifact(
            compile_mongo_ir(golden_ir(), binding=golden_binding())
        )
        mongo_second = _mongo_validated_artifact(
            compile_mongo_ir(golden_ir(), binding=golden_binding())
        )
        assert mongo_first.fingerprint == mongo_second.fingerprint

    def test_physical_artifact_fingerprints_are_distinct(self) -> None:
        sql = _sql_validated_artifact(compile_ir(golden_ir(), binding=golden_binding()))
        mongo = _mongo_validated_artifact(
            compile_mongo_ir(golden_ir(), binding=golden_binding())
        )
        assert sql.fingerprint.startswith("sha256:")
        assert mongo.fingerprint.startswith("sha256:")
        assert sql.fingerprint != mongo.fingerprint

    def test_compiler_evidence_links_distinct_artifacts_to_one_ir(self) -> None:
        ir = golden_ir()
        evidence = (
            CompilerArtifactEvidence(
                ir_version=ir.ir_version,
                ir_fingerprint=ir.fingerprint,
                compiler_identity=SQL_COMPILER_IDENTITY,
                compiler_version=SQL_COMPILER_VERSION,
                adapter_type="sql",
                artifact_fingerprint=_sql_validated_artifact(
                    compile_ir(ir, binding=golden_binding())
                ).fingerprint,
            ),
            CompilerArtifactEvidence(
                ir_version=ir.ir_version,
                ir_fingerprint=ir.fingerprint,
                compiler_identity=MONGO_COMPILER_IDENTITY,
                compiler_version=MONGO_COMPILER_VERSION,
                adapter_type="mongodb",
                artifact_fingerprint=_mongo_validated_artifact(
                    compile_mongo_ir(ir, binding=golden_binding())
                ).fingerprint,
            ),
        )
        assert evidence[0].ir_fingerprint == evidence[1].ir_fingerprint == GOLDEN_FINGERPRINT
        assert evidence[0].artifact_fingerprint != evidence[1].artifact_fingerprint
        assert {entry.adapter_type for entry in evidence} == {"sql", "mongodb"}
