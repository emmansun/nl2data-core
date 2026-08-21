"""SQLite case executor: governed plan execution against a bound fixture.

Each case plan is routed through the same deterministic path as the
workflow runner (plan validation, governance, authorization, SQL
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
from nl2data_core.planning.models import SemanticQueryPlan
from nl2data_core.planning.validation import AuthorizedView
from nl2data_core.workflow.runner import QueryExecutionRunner, StaticPlanResolver


class SqliteCaseExecutor:
    """Executes one case plan against a SQLite fixture profile.

    The adapter is scoped to the plan itself: the bound object and the
    plan's field ids, so the guarded path can never read outside the
    semantic scope.
    """

    def __init__(
        self,
        *,
        policy_scope: PolicyScope,
        view: AuthorizedView,
        effective_limits: EffectiveLimits | None = None,
        max_rows: int = 100,
    ) -> None:
        self._policy_scope = policy_scope
        self._view = view
        self._effective_limits = effective_limits or EffectiveLimits()
        self._max_rows = max_rows

    async def execute(
        self,
        plan: SemanticQueryPlan,
        fixture: SQLiteFixtureProfile,
        context: EvaluationRunContext,
    ) -> CaseEvidence:
        if plan.binding is None:
            raise ValueError("a SQLite case plan requires a physical binding")
        adapter = SqlQueryAdapter(
            dialect="sqlite",
            db_path=fixture.db_path,
            allowed_objects=frozenset({plan.binding.object_id}),
            allowed_columns=frozenset(plan.field_ids()),
            max_rows=self._max_rows,
        )
        runner = QueryExecutionRunner(
            adapter=adapter,
            policy_scope=self._policy_scope,
            view=self._view,
            plan_resolver=StaticPlanResolver(plan),
            effective_limits=self._effective_limits,
        )
        outcome = await runner.execute(
            QueryRequest(request_id=f"eval-{plan.plan_id}", prompt="evaluation case")
        )
        if outcome.status == OutcomeStatus.SUCCEEDED and outcome.result is not None:
            return CaseEvidence(
                plan_fingerprint=plan.fingerprint,
                result_fingerprint=outcome.result.fingerprint,
                columns=outcome.result.column_names,
                rows=outcome.result.rows,
            )
        return CaseEvidence(plan_fingerprint=plan.fingerprint, error=outcome.error)
