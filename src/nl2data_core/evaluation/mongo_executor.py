"""MongoDB fake-driver case executor: governed IR execution on a fixture.

Each case IR is routed through the same deterministic path as the
workflow runner (IR validation, governance, authorization, structured
MQL execution, result protection) with the MongoDB IR compiler bound as
the runner's ``ir_compiler``.  Only protected evidence - fingerprints
and scalar rows - is returned; native clients and driver errors never
leave this boundary.
"""

from __future__ import annotations

from nl2data.models import OutcomeStatus, QueryRequest
from nl2data_core.adapters.mongodb.adapter import MongoQueryAdapter
from nl2data_core.adapters.mongodb.compile import compile_mongo_ir
from nl2data_core.adapters.mongodb.models import MongoAdapterConfig, MongoProfile
from nl2data_core.evaluation.models import CaseEvidence, EvaluationRunContext
from nl2data_core.fixtures.mongo import MongoFixtureProfile
from nl2data_core.governance.models import EffectiveLimits, PolicyScope
from nl2data_core.planning.ir.models import SemanticQueryIR
from nl2data_core.planning.models import PhysicalBinding
from nl2data_core.planning.validation import AuthorizedView
from nl2data_core.workflow.runner import QueryExecutionRunner, StaticPlanResolver


class MongoCaseExecutor:
    """Executes one case IR against the fake MongoDB fixture profile.

    The adapter is scoped to the IR itself: the bound collection and the
    IR's field ids, so the guarded path can never read outside the
    semantic scope.  The fake executor is injected so no driver or service
    is required.
    """

    def __init__(
        self,
        *,
        policy_scope: PolicyScope,
        view: AuthorizedView,
        binding: PhysicalBinding | None = None,
        effective_limits: EffectiveLimits | None = None,
        max_rows: int = 100,
    ) -> None:
        self._policy_scope = policy_scope
        self._view = view
        self._binding = binding
        self._effective_limits = effective_limits or EffectiveLimits()
        self._max_rows = max_rows

    async def execute(
        self,
        ir: SemanticQueryIR,
        fixture: MongoFixtureProfile,
        context: EvaluationRunContext,
    ) -> CaseEvidence:
        binding = self._binding
        if binding is None:
            raise ValueError("a MongoDB case IR requires a physical binding")
        adapter = MongoQueryAdapter(
            config=MongoAdapterConfig(
                profile=MongoProfile.FAKE,
                allowed_collections=frozenset({binding.object_id}),
                allowed_fields=frozenset(ir.field_ids()),
                max_rows=self._max_rows,
            ),
            executor=fixture.executor,
        )
        runner = QueryExecutionRunner(
            adapter=adapter,
            policy_scope=self._policy_scope,
            view=self._view,
            plan_resolver=StaticPlanResolver(ir),
            binding=binding,
            effective_limits=self._effective_limits,
            ir_compiler=lambda ir: compile_mongo_ir(ir, binding=binding),
        )
        outcome = await runner.execute(
            QueryRequest(request_id=f"eval-mongo-{ir.ir_id}", prompt="evaluation case")
        )
        if outcome.status == OutcomeStatus.SUCCEEDED and outcome.result is not None:
            return CaseEvidence(
                ir_fingerprint=ir.fingerprint,
                result_fingerprint=outcome.result.fingerprint,
                columns=outcome.result.column_names,
                rows=outcome.result.rows,
            )
        return CaseEvidence(ir_fingerprint=ir.fingerprint, error=outcome.error)
