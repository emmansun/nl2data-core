"""SQLite case executor: governed IR execution against a bound fixture.

Each case IR is routed through the same deterministic path as the
workflow runner (IR validation, governance, authorization, SQL
execution, result protection) but only protected evidence - fingerprints
and scalar rows - is returned to the evaluation runner.  Raw prompts,
native clients, and unrestricted provider errors never leave this
boundary.
"""

from __future__ import annotations

from nl2data.models import OutcomeStatus, QueryRequest
from nl2data_core.adapters.sql.adapter import SqlQueryAdapter
from nl2data_core.evaluation.models import CaseEvidence, EvaluationRunContext
from nl2data_core.fixtures.sqlite import SQLiteFixtureProfile
from nl2data_core.governance.models import EffectiveLimits, PolicyScope
from nl2data_core.planning.ir.models import SemanticQueryIR
from nl2data_core.planning.models import PhysicalBinding
from nl2data_core.planning.validation import AuthorizedView
from nl2data_core.workflow.runner import QueryExecutionRunner, StaticPlanResolver


class SqliteCaseExecutor:
    """Executes one case IR against a SQLite fixture profile.

    The adapter is scoped to the IR itself: the bound object and the
    IR's field ids, so the guarded path can never read outside the
    semantic scope.
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
        fixture: SQLiteFixtureProfile,
        context: EvaluationRunContext,
    ) -> CaseEvidence:
        binding = self._binding
        if binding is None:
            raise ValueError("a SQLite case IR requires a physical binding")
        adapter = SqlQueryAdapter(
            dialect="sqlite",
            db_path=fixture.db_path,
            allowed_objects=frozenset({binding.object_id}),
            allowed_columns=frozenset(ir.field_ids()),
            max_rows=self._max_rows,
        )
        runner = QueryExecutionRunner(
            adapter=adapter,
            policy_scope=self._policy_scope,
            view=self._view,
            plan_resolver=StaticPlanResolver(ir),
            binding=binding,
            effective_limits=self._effective_limits,
        )
        outcome = await runner.execute(
            QueryRequest(request_id=f"eval-{ir.ir_id}", prompt="evaluation case")
        )
        if outcome.status == OutcomeStatus.SUCCEEDED and outcome.result is not None:
            return CaseEvidence(
                ir_fingerprint=ir.fingerprint,
                result_fingerprint=outcome.result.fingerprint,
                columns=outcome.result.column_names,
                rows=outcome.result.rows,
            )
        return CaseEvidence(ir_fingerprint=ir.fingerprint, error=outcome.error)
