# Quickstart

> **Reader**: application developers. **Prerequisites**:
> [Installation](installation.md) completed. This guide is **tested**: the
> code below runs in CI as a smoke check and uses only supported public
> imports from `nl2data` — never internal `nl2data_core` modules.

## 1. Compose a facade

The public entry point is the `NL2Data` facade (or the `create_facade`
factory). You compose it with a typed `CompositionProfile`:

```python
from nl2data import CompositionProfile, create_facade

facade = create_facade(composition=CompositionProfile())
```

An empty profile is safe: the facade reports that no executable runtime is
configured and never loads optional backends. Constructing a facade never
imports database, LLM, HTTP, or telemetry modules.

## 2. Initialize and submit a first query

```python
import asyncio

from nl2data import (
    CompositionProfile,
    ErrorCode,
    NL2Data,
    OutcomeStatus,
    QueryRequest,
    create_facade,
)


async def main() -> None:
    facade = create_facade(composition=CompositionProfile())
    await facade.initialize()

    outcome = await facade.aquery(
        QueryRequest(request_id="req-1", prompt="How many orders shipped yesterday?")
    )

    # Without a configured runtime the facade returns an explicit,
    # protected not-configured outcome instead of fabricating a result.
    assert outcome.status == OutcomeStatus.NOT_CONFIGURED
    assert outcome.error is not None
    assert outcome.error.code == ErrorCode.NOT_CONFIGURED

    await facade.close()


asyncio.run(main())
```

Every query returns a protected `QueryOutcome`; internal details never
cross the public boundary. Unexpected runtime failures map to a safe
failed outcome.

## 3. Bind an executable runtime

To execute real work you bind either a pre-built transport-neutral
`WorkflowRuntimePort` or the deterministic composition parts (query
adapter, policy scope, authorized view, plan resolver, provider, state
store, tenant context — all optional). `WorkflowRuntimePort` is a public
protocol you can implement or receive from your own composition layer:

```python
import asyncio

from nl2data import (
    CancellationRequest,
    CancellationResult,
    CancellationStatus,
    CompositionProfile,
    OutcomeStatus,
    QueryOutcome,
    QueryRequest,
    QueryResult,
    WorkflowHandle,
    WorkflowRuntimePort,
    WorkflowStage,
    WorkflowStatus,
    create_facade,
    as_error_record,
)


class EchoRuntime(WorkflowRuntimePort):
    """Minimal deterministic runtime: returns one protected result row."""

    def is_configured(self) -> bool:
        return True

    async def execute(
        self,
        request: QueryRequest,
        *,
        cancellation: CancellationRequest | None = None,
    ) -> QueryOutcome:
        if cancellation is not None:
            return QueryOutcome(
                status=OutcomeStatus.FAILED,
                request_id=request.request_id,
                error=as_error_record(RuntimeError("cancelled before execution")),
            )
        return QueryOutcome(
            status=OutcomeStatus.SUCCEEDED,
            request_id=request.request_id,
            workflow_id=f"wf-{request.request_id}",
            result=QueryResult(
                result_id=f"res-{request.request_id}",
                column_names=("count",),
                rows=((1,),),
            ),
        )

    def get_workflow(
        self, workflow_id: str, *, tenant_scope_fingerprint: str | None = None
    ) -> WorkflowHandle | None:
        return WorkflowHandle(
            workflow_id=workflow_id,
            request_id="req-1",
            status=WorkflowStatus.SUCCEEDED,
            current_stage=WorkflowStage.COMPLETE,
        )

    def cancel(self, request: CancellationRequest) -> CancellationResult:
        return CancellationResult(
            status=CancellationStatus.CANCELLED,
            workflow_id=request.workflow_id,
            reason=request.reason,
        )

    async def close(self) -> None:
        return None


async def main() -> None:
    facade = create_facade(composition=CompositionProfile(runtime=EchoRuntime()))
    await facade.initialize()

    outcome = await facade.aquery(QueryRequest(request_id="req-2", prompt="count rows"))
    assert outcome.status == OutcomeStatus.SUCCEEDED
    assert outcome.result is not None
    assert outcome.result.rows == ((1,),)

    if outcome.workflow_id is not None:
        handle = facade.get_workflow(outcome.workflow_id)
        assert handle is not None and handle.status == WorkflowStatus.SUCCEEDED

        result = facade.cancel(CancellationRequest(workflow_id=outcome.workflow_id, reason="stop"))
        assert result.status == CancellationStatus.CANCELLED

    await facade.close()


asyncio.run(main())
```

Real query adapters (SQL, MongoDB) and model providers (OpenAI) plug into
the same composition boundary; see
[Adding an adapter or provider](../development/adding-adapter-or-provider.md)
and the [architecture overview](../architecture/overview.md).

## 4. Sync convenience and lifecycle notes

- `facade.query(request)` runs the same path synchronously, but only
  outside an active event loop. Inside a loop it raises the stable
  `SyncUsageError` (`ASYNC_REQUIRED`) instead of blocking the loop.
- Lifecycle is explicit: `created -> initializing -> ready -> draining ->
  closed`. Queries before initialization or after drain/close are rejected
  with structured `LifecycleError`s.
- `drain()` and `close()` are idempotent; `close()` releases provider,
  adapter, Memory, and state-store resources exactly once.
- `facade.capabilities()` returns an immutable `FacadeCapabilities`
  snapshot; `facade.health()` observes the lifecycle.

## Next steps

- [Composition and query lifecycle](../guides/composition-and-query-lifecycle.md)
  — clarification, cancellation, workflow handles, capability/health in depth.
- [Configuration reference](../reference/configuration.md) — the bounded
  configuration model.
- [Quickstart (简体中文)](quickstart.zh-CN.md) — 中文快速上手。
