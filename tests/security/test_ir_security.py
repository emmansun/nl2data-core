"""Security tests for the canonical IR boundary (DDS-019).

Model/provider output cannot bypass IR validation to reach an adapter:
compilers fail closed on tampered fingerprints, the governed runner and
runtime reject invalid IR before adapter execution, and the IR layer
has no database, LLM, HTTP, or vendor dependency.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from nl2data import ErrorCode, OutcomeStatus, QueryContext, QueryRequest
from nl2data_core.adapters.models import (
    AdapterCapabilities,
    ExecutionResult,
    ParsedArtifact,
    ValidatedArtifact,
    ValidationContext,
)
from nl2data_core.adapters.sql.adapter import SqlQueryAdapter
from nl2data_core.adapters.sql.compile import SQLCompileError, compile_ir
from nl2data_core.ai.context import SemanticReference
from nl2data_core.ai.fake import FakeModelProvider
from nl2data_core.ai.models import StructuredIntent
from nl2data_core.ai.plan_builder import build_ir_from_intent
from nl2data_core.fixtures import SQLiteFixtureProfile
from nl2data_core.governance.models import PolicyScope
from nl2data_core.planning.ir.fixtures import golden_binding, golden_ir
from nl2data_core.planning.ir.models import IRExtension, SemanticQueryIR
from nl2data_core.planning.models import ColumnBinding, PhysicalBinding
from nl2data_core.planning.validation import AuthorizedView
from nl2data_core.workflow.runner import QueryExecutionRunner, StaticPlanResolver
from nl2data_core.workflow.runtime import DeterministicWorkflowRuntime
from nl2data_mongodb.compile import MongoCompileError, compile_mongo_ir

SRC_ROOT = Path(__file__).resolve().parents[2] / "src"
IR_DIR = SRC_ROOT / "nl2data_core" / "planning" / "ir"

#: Optional backends the IR layer must never load or import.
OPTIONAL_BACKENDS = [
    "sqlglot",
    "sqlalchemy",
    "pymongo",
    "psycopg",
    "asyncpg",
    "openai",
    "langchain",
    "httpx",
    "requests",
    "fastapi",
    "flask",
    "redis",
    "boto3",
    "grpc",
    "opentelemetry",
]

FIELDS = frozenset({"order_id", "customer_id", "amount", "region", "status", "created_at"})

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


class CountingAdapter:
    """Delegating adapter counting executions for branch assertions."""

    def __init__(self, inner: SqlQueryAdapter) -> None:
        self._inner = inner
        self.execution_count = 0

    def capabilities(self) -> AdapterCapabilities:
        return self._inner.capabilities()

    def parse(self, query: str, context: ValidationContext) -> ParsedArtifact:
        return self._inner.parse(query, context)

    def validate(self, artifact: ParsedArtifact, context: ValidationContext) -> ValidatedArtifact:
        return self._inner.validate(artifact, context)

    async def execute(
        self, artifact: ValidatedArtifact, context: ValidationContext
    ) -> ExecutionResult:
        self.execution_count += 1
        return await self._inner.execute(artifact, context)

    async def close(self) -> None:
        await self._inner.close()


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


def make_adapter(tmp_path: Path) -> SqlQueryAdapter:
    return SqlQueryAdapter(
        dialect="sqlite",
        db_path=tmp_path / "fixture.db",
        allowed_objects=frozenset({"orders"}),
        allowed_columns=FIELDS,
        max_rows=100,
    )


def make_execution(tmp_path: Path, *, adapter, **overrides) -> QueryExecutionRunner:
    fixture = SQLiteFixtureProfile(db_path=tmp_path / "fixture.db")
    fixture.provision()
    values = {
        "adapter": adapter,
        "policy_scope": make_policy_scope(),
        "view": make_view(),
        "plan_resolver": StaticPlanResolver(None),
    }
    values.update(overrides)
    return QueryExecutionRunner(**values)


def make_runtime(tmp_path: Path, *, provider=None, execution=None, **overrides):
    values = {
        "provider": provider or FakeModelProvider(default_response=VALID_INTENT),
        "execution": execution or make_execution(tmp_path, adapter=make_adapter(tmp_path)),
        "semantic_references": REFERENCES,
        "binding": BINDING,
    }
    values.update(overrides)
    return DeterministicWorkflowRuntime(**values)


def bypass_request() -> QueryRequest:
    return QueryRequest(
        request_id="req-bypass",
        prompt="top 10 order amounts in emea",
        context=QueryContext(request_id="req-bypass", workflow_id="wf-bypass"),
    )


def duplicate_filter_ir() -> object:
    """An IR that passes view validation but fails canonical IR validation.

    ``validate_ir`` enforces filter id uniqueness, so the only gate that
    can catch the duplicate is the canonical IR validation.
    """
    intent = StructuredIntent.model_validate(
        {
            **VALID_INTENT["intent"],
            "intent_id": "intent-bypass",
            "request_id": "req-bypass",
        }
    )
    ir = build_ir_from_intent(intent, catalog_fingerprint=None)
    return ir.model_copy(update={"filters": (ir.filters[0], ir.filters[0])})


class TestCompilerFailClosed:
    def test_sql_compiler_rejects_tampered_fingerprint(self) -> None:
        tampered = golden_ir().model_copy(update={"fingerprint": "sha256:" + "0" * 64})
        with pytest.raises(SQLCompileError) as excinfo:
            compile_ir(tampered, binding=golden_binding())
        assert excinfo.value.code == ErrorCode.SQL_REJECTED
        assert "fingerprint" in excinfo.value.message

    def test_mongo_compiler_rejects_tampered_fingerprint(self) -> None:
        tampered = golden_ir().model_copy(update={"fingerprint": "sha256:" + "0" * 64})
        with pytest.raises(MongoCompileError) as excinfo:
            compile_mongo_ir(tampered, binding=golden_binding())
        assert excinfo.value.code == ErrorCode.MONGO_REJECTED
        assert "fingerprint" in excinfo.value.message

    def test_native_values_cannot_reach_a_compiler(self) -> None:
        from nl2data_core.planning.ir.models import IRFilter

        #: Native objects are rejected at the model boundary, so a compiler
        #: can never be handed an IR carrying driver-native filter values.
        with pytest.raises(ValidationError):
            IRFilter(filter_id="f1", field_id="region", operator="eq", value={"$oid": "x"})

    def test_compilers_reject_structurally_invalid_ir(self) -> None:
        invalid = golden_ir().model_copy(update={"limit": None})
        with pytest.raises(SQLCompileError):
            compile_ir(invalid, binding=golden_binding())
        with pytest.raises(MongoCompileError):
            compile_mongo_ir(invalid, binding=golden_binding())

    def test_extensions_cannot_carry_raw_query_material(self) -> None:
        with pytest.raises(ValidationError):
            IRExtension(
                extension_id="e1",
                kind="risk",
                payload={"mql": "db.orders.find({})"},
            )
        with pytest.raises(ValidationError):
            IRExtension(
                extension_id="e1",
                kind="risk",
                payload={"note": "DELETE FROM orders"},
            )


class TestNoIRBypass:
    async def test_runner_rejects_ir_invalid_before_adapter(self, tmp_path: Path) -> None:
        adapter = CountingAdapter(make_adapter(tmp_path))
        execution = make_execution(
            tmp_path,
            adapter=adapter,
            plan_resolver=StaticPlanResolver(duplicate_filter_ir()),
        )
        outcome = await execution.execute(bypass_request())
        assert outcome.status == OutcomeStatus.REJECTED
        assert outcome.result is None
        assert outcome.error is not None
        assert outcome.error.code == ErrorCode.PLAN_VALIDATION_FAILED
        assert "duplicate_filter" in outcome.error.details["issue_codes"]
        assert adapter.execution_count == 0

    async def test_runtime_rejects_ir_invalid_intent_before_adapter(
        self, tmp_path: Path
    ) -> None:
        adapter = CountingAdapter(make_adapter(tmp_path))
        duplicate = VALID_INTENT["intent"]["filters"][0]
        provider = FakeModelProvider(
            default_response={
                "intent": {**VALID_INTENT["intent"], "filters": [duplicate, duplicate]}
            }
        )
        runtime = make_runtime(
            tmp_path,
            provider=provider,
            execution=make_execution(tmp_path, adapter=adapter),
        )
        outcome = await runtime.execute(bypass_request())
        #: The IR-invalid plan never reaches the adapter: no artifact, no SQL.
        assert outcome.status in (OutcomeStatus.REJECTED, OutcomeStatus.FAILED)
        assert outcome.result is None
        assert adapter.execution_count == 0
        assert provider.call_count == 1

    async def test_unbounded_ir_rejected_before_adapter(self, tmp_path: Path) -> None:
        adapter = CountingAdapter(make_adapter(tmp_path))
        intent = StructuredIntent.model_validate(
            {
                **VALID_INTENT["intent"],
                "intent_id": "intent-unbounded",
                "request_id": "req-unbounded",
            }
        )
        ir = build_ir_from_intent(intent, catalog_fingerprint=None)
        #: The IR builder itself rejects unbounded intents, so the bounded
        #: IR is stripped of its limit to exercise the governed boundary.
        ir = ir.model_copy(update={"limit": None})
        execution = make_execution(
            tmp_path,
            adapter=adapter,
            plan_resolver=StaticPlanResolver(ir),
        )
        outcome = await execution.execute(bypass_request())
        assert outcome.status == OutcomeStatus.REJECTED
        assert outcome.error is not None
        assert outcome.error.code == ErrorCode.PLAN_VALIDATION_FAILED
        assert "unbounded_limit" in outcome.error.details["issue_codes"]
        assert adapter.execution_count == 0


class TestIRLayerImportBoundary:
    def test_importing_ir_loads_no_optional_backends(self) -> None:
        script = textwrap.dedent(
            """
            import nl2data_core.planning.ir  # noqa: F401
            import sys
            print(",".join(sorted({name.split('.')[0] for name in sys.modules})))
            """
        )
        env = {**os.environ, "PYTHONPATH": str(SRC_ROOT)}
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            check=True,
            env=env,
        )
        loaded = set(result.stdout.strip().split(","))
        assert "nl2data_core" in loaded
        for backend in OPTIONAL_BACKENDS:
            assert backend not in loaded, f"optional backend loaded by IR layer: {backend}"

    def test_ir_modules_never_import_optional_backends(self) -> None:
        offenders: list[str] = []
        for module_path in sorted(IR_DIR.glob("*.py")):
            tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
            imported: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imported.add(alias.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module.split(".")[0])
            for backend in OPTIONAL_BACKENDS:
                if backend in imported:
                    offenders.append(f"{module_path.name} -> {backend}")
        assert offenders == [], f"forbidden imports found: {offenders}"


class TestLegacyShapedPayloadsFailClosed:
    """Payloads shaped like the removed plan or physical artifacts cannot become IR.

    After the legacy removal there is no translator: a payload that looks
    like a ``SemanticQueryPlan``, a compiled SQL/MQL artifact, or a physical
    binding is rejected at the IR model boundary, so it can never reach a
    compiler or adapter.
    """

    LEGACY_PLAN_SHAPE = {
        "plan_id": "plan-legacy-1",
        "source_id": "acme_warehouse",
        "root_entity_id": "orders",
        "selections": [{"selection_id": "s1", "field_id": "region"}],
        "filters": [
            {"filter_id": "f1", "field_id": "region", "operator": "eq", "value": "north"}
        ],
        "limit": 10,
        "lineage": {"catalog_fingerprint": "sha256:" + "ab" * 32},
    }

    def test_legacy_plan_shape_is_rejected_at_the_ir_boundary(self) -> None:
        with pytest.raises(ValidationError):
            SemanticQueryIR.model_validate(self.LEGACY_PLAN_SHAPE)
        with pytest.raises(ValidationError):
            SemanticQueryIR.from_canonical_json(json.dumps(self.LEGACY_PLAN_SHAPE))

    def test_physical_artifact_shapes_are_rejected_at_the_ir_boundary(self) -> None:
        for payload in (
            {"sql": "SELECT region FROM orders_table LIMIT 10", "dialect": "sqlite"},
            {
                "spec_id": "mongo-1",
                "operation": "aggregate",
                "collection": "orders_table",
                "pipeline": [{"$limit": 10}],
            },
            {
                "object_id": "orders_table",
                "dialect": "sqlite",
                "column_bindings": [{"field_id": "region", "physical_name": "region"}],
            },
        ):
            with pytest.raises(ValidationError):
                SemanticQueryIR.model_validate(payload)
            with pytest.raises(ValidationError):
                SemanticQueryIR.from_canonical_json(json.dumps(payload))

    def test_legacy_fields_cannot_be_smuggled_into_a_valid_ir(self) -> None:
        payload = json.loads(golden_ir().serialize_canonical())
        payload["plan_id"] = "plan-legacy-1"
        payload["lineage"] = {"catalog_fingerprint": "sha256:" + "ab" * 32}
        with pytest.raises(ValidationError):
            SemanticQueryIR.model_validate(payload)
        with pytest.raises(ValidationError):
            SemanticQueryIR.from_canonical_json(json.dumps(payload))
