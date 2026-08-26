"""MongoDB governance integration: governed runner denials stop before driver.

Covers task 4.4 at runner level: the governed workflow (IR validation,
governance, authorization, compilation, adapter guard) denies out-of-scope
and tenant-unsatisfied queries with a REJECTED outcome, and the driver is
never reached.  A spy executor records every driver call.
"""

from __future__ import annotations

from nl2data import ErrorCode, OutcomeStatus, QueryRequest
from nl2data_core.governance.models import PolicyScope
from nl2data_core.planning.ir.models import (
    IRFilter,
    IROrdering,
    IRProvenance,
    IRSelection,
    SemanticQueryIR,
)
from nl2data_core.planning.models import ColumnBinding, PhysicalBinding
from nl2data_core.planning.validation import AuthorizedView
from nl2data_core.workflow.runner import QueryExecutionRunner, StaticPlanResolver
from nl2data_mongodb.adapter import MongoQueryAdapter
from nl2data_mongodb.compile import compile_mongo_ir
from nl2data_mongodb.config import MongoAdapterConfig, MongoProfile
from nl2data_mongodb.fake import FakeMongoExecutor
from nl2data_mongodb.fixtures import MONGO_SEED
from nl2data_mongodb.normalize import predicate_fingerprint

FIELDS = frozenset(
    {"order_id", "customer_id", "amount", "region", "status", "created_at"}
)


class SpyExecutor(FakeMongoExecutor):
    """Records every driver call so tests can prove none happened."""

    def __init__(self) -> None:
        super().__init__(MONGO_SEED)
        self.calls: list[str] = []

    def find_documents(self, **kwargs):
        self.calls.append("find")
        return super().find_documents(**kwargs)

    def aggregate_documents(self, **kwargs):
        self.calls.append("aggregate")
        return super().aggregate_documents(**kwargs)

    def count_documents(self, **kwargs):
        self.calls.append("count")
        return super().count_documents(**kwargs)


def make_binding(**overrides) -> PhysicalBinding:
    values = {
        "object_id": "orders",
        "dialect": "mongo",
        "column_bindings": tuple(
            ColumnBinding(field_id=field, physical_name=field) for field in FIELDS
        ),
    }
    values.update(overrides)
    return PhysicalBinding(**values)


def make_ir(**overrides) -> SemanticQueryIR:
    values = {
        "ir_id": "mongo-governance-ir",
        "source_id": "sales",
        "root_entity_id": "order",
        "selections": (
            IRSelection(selection_id="s1", field_id="order_id", alias="oid"),
            IRSelection(selection_id="s2", field_id="amount", alias="amt"),
        ),
        "filters": (
            IRFilter(filter_id="f1", field_id="region", operator="eq", value="emea"),
        ),
        "orderings": (
            IROrdering(ordering_id="o1", field_id="amount", direction="desc"),
        ),
        "limit": 3,
        "provenance": IRProvenance(source_id="sales", root_entity_id="order"),
    }
    values.update(overrides)
    return SemanticQueryIR(**values)


def make_runner(spy: SpyExecutor, **overrides) -> QueryExecutionRunner:
    adapter = MongoQueryAdapter(
        config=MongoAdapterConfig(
            profile=MongoProfile.FAKE,
            allowed_collections=frozenset({"orders"}),
            allowed_fields=FIELDS,
        ),
        executor=spy,
    )
    values = {
        "adapter": adapter,
        "policy_scope": PolicyScope(
            policy_id="mongo-policy",
            source_ids=frozenset({"sales"}),
            resource_ids=frozenset({"orders"}),
            operation_ids=frozenset({"select"}),
            field_ids=FIELDS,
        ),
        "view": AuthorizedView(
            source_id="sales",
            root_entity_ids=frozenset({"order"}),
            field_ids=FIELDS,
        ),
        "plan_resolver": StaticPlanResolver(make_ir()),
        "binding": make_binding(),
        "ir_compiler": lambda ir: compile_mongo_ir(ir, binding=make_binding()),
    }
    values.update(overrides)
    return QueryExecutionRunner(**values)


class TestGovernedMongoExecution:
    async def test_governed_mongo_query_succeeds(self) -> None:
        spy = SpyExecutor()
        outcome = await make_runner(spy).execute(
            QueryRequest(request_id="mongo-gov-1", prompt="orders")
        )
        assert outcome.status == OutcomeStatus.SUCCEEDED
        assert outcome.error is None
        assert outcome.result is not None
        assert sorted(outcome.result.column_names) == ["amt", "oid"]
        assert outcome.result.rows == ((180.0, 18), (170.0, 17), (160.0, 16))
        assert spy.calls == ["find"]

    async def test_policy_scope_denial_stops_before_driver(self) -> None:
        spy = SpyExecutor()
        scope = PolicyScope(
            policy_id="mongo-policy",
            source_ids=frozenset({"sales"}),
            resource_ids=frozenset({"orders"}),
            operation_ids=frozenset({"select"}),
            field_ids=FIELDS - {"amount"},
        )
        runner = make_runner(spy, policy_scope=scope)
        outcome = await runner.execute(
            QueryRequest(request_id="mongo-gov-2", prompt="orders")
        )
        assert outcome.status == OutcomeStatus.REJECTED
        assert outcome.result is None
        assert outcome.error is not None
        assert outcome.error.code == ErrorCode.GOVERNANCE_DENIED
        assert spy.calls == []

    async def test_resource_denial_stops_before_driver(self) -> None:
        spy = SpyExecutor()
        scope = PolicyScope(
            policy_id="mongo-policy",
            source_ids=frozenset({"sales"}),
            resource_ids=frozenset({"customers"}),
            operation_ids=frozenset({"select"}),
            field_ids=FIELDS,
        )
        runner = make_runner(spy, policy_scope=scope)
        outcome = await runner.execute(
            QueryRequest(request_id="mongo-gov-3", prompt="orders")
        )
        assert outcome.status == OutcomeStatus.REJECTED
        assert outcome.error is not None
        assert outcome.error.code == ErrorCode.GOVERNANCE_DENIED
        assert spy.calls == []

    async def test_adapter_scope_denial_stops_before_driver(self) -> None:
        spy = SpyExecutor()
        adapter = MongoQueryAdapter(
            config=MongoAdapterConfig(
                profile=MongoProfile.FAKE,
                allowed_collections=frozenset(),  # fail closed
                allowed_fields=FIELDS,
            ),
            executor=spy,
        )
        runner = make_runner(spy, adapter=adapter)
        outcome = await runner.execute(
            QueryRequest(request_id="mongo-gov-4", prompt="orders")
        )
        assert outcome.status == OutcomeStatus.REJECTED
        assert outcome.result is None
        assert outcome.error is not None
        assert outcome.error.code == ErrorCode.MONGO_REJECTED
        assert spy.calls == []

    async def test_tenant_profile_denial_stops_before_driver(self) -> None:
        spy = SpyExecutor()
        adapter = MongoQueryAdapter(
            config=MongoAdapterConfig(
                profile=MongoProfile.FAKE,
                allowed_collections=frozenset({"orders"}),
                allowed_fields=FIELDS,
                tenant_profile="pooled",
                required_obligation_fingerprint=predicate_fingerprint(
                    "region", "$eq", "apac"
                ),
            ),
            executor=spy,
        )
        runner = make_runner(spy, adapter=adapter)
        outcome = await runner.execute(
            QueryRequest(request_id="mongo-gov-5", prompt="orders")
        )
        assert outcome.status == OutcomeStatus.REJECTED
        assert outcome.result is None
        assert outcome.error is not None
        assert outcome.error.code == ErrorCode.MONGO_REJECTED
        assert spy.calls == []

    async def test_isolated_routing_denial_stops_before_driver(self) -> None:
        spy = SpyExecutor()
        adapter = MongoQueryAdapter(
            config=MongoAdapterConfig(
                profile=MongoProfile.FAKE,
                allowed_collections=frozenset({"orders"}),
                allowed_fields=FIELDS,
                tenant_profile="schema_isolated",
            ),
            executor=spy,
        )
        runner = make_runner(spy, adapter=adapter)
        outcome = await runner.execute(
            QueryRequest(request_id="mongo-gov-6", prompt="orders")
        )
        assert outcome.status == OutcomeStatus.REJECTED
        assert outcome.error is not None
        assert outcome.error.code == ErrorCode.MONGO_REJECTED
        assert spy.calls == []
