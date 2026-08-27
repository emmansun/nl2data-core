# CompositionProfile 参考

> **读者**：应用集成方与组合层开发者。
> **前置阅读**：[组合与查询生命周期](../guides/composition-and-query-lifecycle.md)
> 与[语义层](../architecture/semantic-layer.zh-CN.md)。
>
> [English source / 英文规范原文](composition-profile.md)
>
> **本页为简体中文翻译**。规范语言为英文；如与[英文原文](composition-profile.md)冲突，
> 以英文为准。

`CompositionProfile` 是公共 facade（`NL2Data` 或 `create_facade`）的类型化、不可变
组合输入。它绑定一个预构建的运行时端口，或绑定确定性的组合部件。未知字段在构造时
即被拒绝（`extra="forbid"`），拼写错误会提前失败。

本页说明每个字段：含义、取值来源、是否影响可执行性，以及实际用法。

## 两种组合模式

1. **预构建运行时** —— 绑定 `runtime` 为 `WorkflowRuntimePort`（例如你自己的组合层
   产出的运行时）。facade 完全委托给它。
2. **确定性部件** —— 绑定 `adapter`、`policy_scope`、`view`、`plan_resolver`，
   外加可选的 `provider`、`state_store`、`tenant_context`、`memory`、`binding`
   等。facade 自行构建受治理的运行时。

空 profile 是合法的：每条查询都返回带安全 `ErrorRecord` 的 `NOT_CONFIGURED`。
不加载任何东西，也不调用任何东西。

## 可执行性门槛

没有任何字段在结构上是必填的，但确定性 profile **只有同时绑定四个运行时部件**才
可执行：

- `adapter`（查询 I/O）
- `policy_scope`（治理允许范围）
- `view`（授权语义视图）
- `plan_resolver`（请求 → `SemanticQueryIR`）

绑定 `runtime` 时，门槛改为 `runtime.is_configured()`。缺少任一部件不会抛错——
只是每条查询静默返回 `NOT_CONFIGURED`（`facade.capabilities().configured` 为
`false`），因此四部件门槛对可用的确定性 profile 实际上是必需的。

`binding` **不属于**该门槛：它是可选的编译器上下文。没有它时，默认 SQL 编译器
使用 `sqlite` 方言，生成的产物无法匹配真实 PostgreSQL 列布局——只要为非默认方言
编译，就应当提供它。

## 字段参考

| 字段 | 类别 | 含义 | 来源 / 默认值 |
| --- | --- | --- | --- |
| `runtime` | 端口 | 预构建的受治理运行时；`is_configured()` 决定可执行性 | 你的组合层 / `None` |
| `provider` | 端口 | AI 提供方；上报 `capabilities().provider_name` | `nl2data-openai` 或你的提供方 / `None` |
| `memory` | 端口 | Memory 后端；`is_available()` 决定召回 | `nl2data-memory-redis` 或你的后端 / `None` |
| `adapter` | 端口 | 查询适配器；上报 `capabilities().adapter_type` | `nl2data-postgres`、`nl2data-mongodb` 或你自己的 / `None` |
| `telemetry` | 端口 | 结构化 log/span/metric/audit 汇 | 你的汇 / `None` |
| `tenant_context` | 不透明 | 可信租户范围；其 `scope_fingerprint` 决定视图解析与租户证据 | 你持有的可信范围 / `None` |
| `policy_scope` | 不透明 | 治理允许范围；`resource_ids` 是**物理对象名**且必须与 binding 一致；其 `policy_fingerprint` 绑定视图定义 | Host 持有，或由 discovery 边界推导 / `None` |
| `view` | 不透明 | `AuthorizedView`：`source_id`、`root_entity_ids`、`field_ids`、可选的视图绑定（`view_id`/`view_version`/`view_fingerprint` 全有或全无） | `AuthorizedView.from_projection(projection)` 或手工构建 / `None` |
| `projection` | 不透明 | 解析得到的 `ResolvedViewProjection`；绑定时运行时自行推导授权视图与语义引用，并记录结构化 Bundle 证据 | `ViewRegistry.resolve(...).projection` / `None` |
| `binding` | 不透明 | `PhysicalBinding`：一个 `object_id`、`dialect`、列绑定——仅编译器上下文 | Host，来自 discovery 边界或数据源配置 / `None` |
| `config` | 不透明 | 模型调用配置（超时、重试、temperature） | Host / `None` |
| `plan_resolver` | 不透明 | 把 `QueryRequest` 映射为校验过的 `SemanticQueryIR` 或 `None` | `StaticPlanResolver(ir)` 或你的 resolver / `None` |
| `state_store` | 不透明 | 持久化工作流状态；启用工作流句柄、取消、幂等 | `nl2data-workflow-postgres` 或你的存储 / `None` |
| `semantic_references` | 不透明 | field → `SemanticReference` 映射，用于 AI 上下文组装 | 由 `projection` 自动推导；没有投影时才手工构建 / `None` |
| `memory_budget` | 不透明 | Memory 召回预算（候选数、单请求上限） | Host / `None` |
| `budget` | 不透明 | 工作流尝试/事件/时长预算 | Host / `None` |
| `approval_required` | 不透明 | 决定已编译 IR 是否需要审批的可调用对象 | Host / `None` |
| `plan_compiler` | 不透明 | IR → 产物编译器；默认是内置 SQL 编译器 | Host / 内置 SQL 编译器 |
| `now` | 不透明 | 确定性测试用的时钟注入 | Host / 系统时钟 |
| `min_confidence` | 标量 | 解析器接受的最低 intent 置信度 | `0.6` |
| `memory_ttl_seconds` | 标量 | Memory 写回 TTL | `86_400` |
| `idempotency_ttl_seconds` | 标量 | 幂等保留 TTL | `86_400.0` |

**端口字段**是 `nl2data` 中定义的公共协议形状（`WorkflowRuntimePort`、
`ModelProviderPort`、`MemoryProviderPort`、`QueryAdapterPort`、
`TelemetryPort`）。**不透明字段**是内部类型（`PolicyScope`、`AuthorizedView`、
`ResolvedViewProjection`、`PhysicalBinding`、`ModelConfig` 等）。应用只导入
`nl2data`；不透明值来自你的组合层，组合层可以导入 `nl2data_core`。应用直接导入
`nl2data_core` 不受支持（见[故障排查](../operations/troubleshooting.md)）。

### `view` 与 `projection` 的区别

- `view` 是运行时对每条 IR 都要校验的治理契约。
- `projection` 是携带证据的解析结果。绑定时，运行时通过
  `AuthorizedView.from_projection` 自动构建 `view` 与语义引用，checkpoint、
  授权与结果血缘证据都会携带解析视图与 Bundle 的 fingerprint。不绑定时，
  你必须自己构建 `view` 与 `semantic_references`，Bundle 身份只能通过视图
  fingerprint 间接提交。

只要使用元数据生命周期，就应优先绑定 `projection`。

## 示例

### 1. 空 profile —— 安全回退

```python
from nl2data import CompositionProfile, create_facade

facade = create_facade(composition=CompositionProfile())
await facade.initialize()

outcome = await facade.aquery(request)
assert outcome.status == "not_configured"
```

### 2. 预构建运行时端口

```python
from nl2data import CompositionProfile, create_facade

facade = create_facade(composition=CompositionProfile(runtime=my_runtime))
```

### 3. 确定性部件

下面的内部类型由你的组合层提供；这里展示出来是为了让形状更具体。

```python
from nl2data import CompositionProfile, NL2Data
from nl2data_core.governance.models import PolicyScope
from nl2data_core.planning.validation import AuthorizedView
from nl2data_core.workflow.runner import StaticPlanResolver

profile = CompositionProfile(
    provider=model_provider,
    adapter=query_adapter,              # allowed_objects={"orders"}
    policy_scope=PolicyScope(
        policy_id="demo-policy",
        source_ids=frozenset({"sales"}),
        resource_ids=frozenset({"orders"}),  # 物理对象名
        operation_ids=frozenset({"select"}),
        field_ids=frozenset({"order_id", "amount", "region", "status"}),
    ),
    view=AuthorizedView(
        source_id="sales",
        root_entity_ids=frozenset({"order"}),  # 语义实体 id
        field_ids=frozenset({"order_id", "amount", "region", "status"}),
    ),
    plan_resolver=StaticPlanResolver(ir),
    binding=physical_binding,           # object_id="orders", dialect, columns
    state_store=state_store,
    tenant_context=scope,
)

facade = NL2Data(composition=profile)
```

### 4. 元数据生命周期 —— 先解析投影

先解析（policy 与 tenant 必须先于解析存在），再把投影折叠进 profile。
完整配方见[从元数据到激活 Bundle](../guides/metadata-to-bundle.zh-CN.md)。

```python
from nl2data import CompositionProfile, NL2Data
from nl2data_core.planning.validation import AuthorizedView

projection = registry.resolve("sales_view", trusted_resolution_context).projection

profile = CompositionProfile(
    provider=model_provider,
    adapter=query_adapter,
    policy_scope=policy_scope,                      # resource_ids = 物理对象
    view=AuthorizedView.from_projection(projection),
    projection=projection,                          # 全链路 Bundle 证据
    binding=physical_binding,
    plan_resolver=plan_resolver,
    state_store=state_store,
    tenant_context=scope,
)
```

### 5. 多根数据源

有多个根表的数据源（例如 `orders` 与 `customers`）按**每个物理对象一个
profile** 组合：每条查询只有一个根实体，SQL 编译器按 profile 的 binding
只生成一条语句。

```python
# 共享语义层：一个描述符 + 一个 Bundle，包含实体 "order" 与 "customer"。
# 两个视图定义各自限定一个实体；两次解析产生两个投影。

orders_profile = CompositionProfile(
    provider=model_provider,
    adapter=orders_adapter,              # allowed_objects={"orders"}
    policy_scope=orders_policy,          # resource_ids={"orders"}
    view=AuthorizedView.from_projection(orders_projection),  # 根: order
    projection=orders_projection,
    binding=orders_binding,              # object_id="orders"
    plan_resolver=orders_plan_resolver,  # 根为 "order" 的 IR
)

customers_profile = CompositionProfile(
    provider=model_provider,
    adapter=customers_adapter,            # allowed_objects={"customers"}
    policy_scope=customers_policy,        # resource_ids={"customers"}
    view=AuthorizedView.from_projection(customers_projection),  # 根: customer
    projection=customers_projection,
    binding=customers_binding,            # object_id="customers"
    plan_resolver=customers_plan_resolver,  # 根为 "customer" 的 IR
)
```

每个 profile 都是独立的 facade。它们共享 provider、描述符与 Bundle；只有按对象
限定的部分不同。当多个根位于**同一张表**时，一个 profile 即可授权它们全部——
视图的 `root_entity_ids` 是集合，binding 覆盖它们的所有字段。

## 常见错误

- **语义名与物理名混淆**：`view.root_entity_ids` 使用语义实体 id（`"order"`）；
  `policy_scope.resource_ids` 与 `binding.object_id` 使用物理名（`"orders"`）。
  混用会导致 `GOVERNANCE_DENIED` 或编译失败。
- **已有 `projection` 却手工构建 `view`**：会丢失语义引用自动推导与结构化 Bundle
  证据。请使用 `AuthorizedView.from_projection` + `projection`。
- **把四部件门槛当作可选项**：只绑定 `adapter` + `view` 的 profile 不会大声失败——
  每条查询只是返回 `NOT_CONFIGURED`。
- **在应用中导入 `nl2data_core`**：组合层构建一次；应用只消费 `nl2data`。

## 相关页面

- [组合与查询生命周期](../guides/composition-and-query-lifecycle.md)
- [语义层](../architecture/semantic-layer.zh-CN.md)
- [从元数据到激活 Bundle](../guides/metadata-to-bundle.zh-CN.md)
- [架构总览](../architecture/overview.zh-CN.md)
