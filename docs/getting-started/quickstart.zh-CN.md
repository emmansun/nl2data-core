# 快速上手

> **本页为简体中文翻译**。规范语言为英文；如与[英文原文](quickstart.md)冲突，
> 以英文为准。代码不因翻译而改变。
>
> **读者**：应用程序开发者。**前置条件**：已完成[安装](installation.md)。
> 本指南**经过测试**：下面的代码在 CI 中作为冒烟检查运行，只使用 `nl2data`
> 中受支持的公共导入——绝不使用内部 `nl2data_core` 模块。

## 1. 组合一个 facade

公共入口是 `NL2Data` 门面（或 `create_facade` 工厂）。您用带类型的
`CompositionProfile` 组合它：

```python
from nl2data import CompositionProfile, create_facade

facade = create_facade(composition=CompositionProfile())
```

空 profile 是安全的：门面报告没有配置可执行运行时，并且从不加载可选后端。
构造门面绝不导入数据库、LLM、HTTP 或遥测模块。

## 2. 初始化并提交首个查询

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

每个查询都返回受保护的 `QueryOutcome`；内部细节绝不跨越公共边界。
意外的运行时失败会映射为安全的失败结果。

## 3. 绑定可执行运行时

要执行真实工作，您要么绑定一个预构建的、传输中立的 `WorkflowRuntimePort`，
要么绑定确定性组合部件（查询适配器、策略范围、授权视图、计划解析器、
提供方、状态存储、租户上下文——全部可选）。`WorkflowRuntimePort` 是
公共协议，您可以自行实现，也可以从您自己的组合层接收：

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

真实的查询适配器（SQL、MongoDB）与模型提供方（OpenAI）接入同一个组合边界；
参见[新增适配器或提供方](../development/adding-adapter-or-provider.md)与
[架构总览](../architecture/overview.md)。

## 4. 同步便捷方法与生命周期说明

- `facade.query(request)` 同步执行同一路径，但只能在活跃事件循环之外。
  在循环内它会抛出稳定的 `SyncUsageError`（`ASYNC_REQUIRED`），而不是
  阻塞循环。
- 生命周期是显式的：`created -> initializing -> ready -> draining ->
  closed`。初始化之前或 drain/close 之后的查询会被结构化
  `LifecycleError` 拒绝。
- `drain()` 与 `close()` 是幂等的；`close()` 恰好一次地释放提供方、
  适配器、Memory 与状态存储资源。
- `facade.capabilities()` 返回不可变的 `FacadeCapabilities` 快照；
  `facade.health()` 观察生命周期。

## 下一步

- [组合与查询生命周期](../guides/composition-and-query-lifecycle.md)
  — 深入讲解澄清、取消、工作流句柄、能力/健康。
- [配置参考](../reference/configuration.md) — 有界配置模型。
- [English source](quickstart.md) — 英文原文（规范）。
