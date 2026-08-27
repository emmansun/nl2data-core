# 主流程演示运行手册

> **本页为简体中文翻译**。规范语言为英文；如与[英文原文](mainflow-demo.md)冲突，
> 以英文为准。技术契约（API 名称、配置键、错误码、图表含义）不因翻译而改变。

> **读者**：希望完整体验从配置到持久化执行的 NL2Data 主流程的操作人员、
> 平台工程师和产品评估者。
> **目标**：运行规范演示、解读结果，并区分服务不可用与真实回归失败。

## 本演示验证的内容

这是 nl2data-core 的规范端到端演示，覆盖完整的公共门面路径：

```text
配置 -> 初始化 -> 执行 -> 持久化/恢复
```

提供两种执行配置：

- **确定性配置（Deterministic profile）** — 无需任何外部服务即可运行。它使用
  SQLite 源 fixture、确定性假模型提供方以及 SQLite 持久化状态存储。
- **真实服务配置（Real-service profile）** — 使用 PostgreSQL 作为源数据库和持久化
  工作流状态后端，并可选用 Redis 作为共享 Memory。该配置用于验证类生产环境的
  持久化能力。

## 前置条件

### 通用

- Python 3.11、3.12 或 3.13。
- 在活动环境中已安装 `nl2data-core`。

### 仅确定性配置

无需额外服务或包。

### 真实服务配置

- PostgreSQL 16+，并具备可创建 schema 和数据表的数据库及用户。
- 可选：Redis 7+，用于共享 Memory。
- 可选后端包：

```bash
pip install nl2data-core[postgres] nl2data-workflow-postgres nl2data-memory-redis[redis] nl2data-postgres
```

## 最小包集合

确定性配置：

```bash
pip install nl2data-core
```

真实服务配置：

```bash
pip install nl2data-core[postgres]
pip install -e packages/nl2data-postgres
pip install -e packages/nl2data-workflow-postgres
pip install -e "packages/nl2data-memory-redis[redis]"
```

## 准备

### 1. 进入仓库

```bash
cd nl2data-core
```

### 2.（真实服务配置）设置环境变量

```bash
export NL2DATA_POSTGRES_DSN="postgresql://nl2data_user@localhost:5432/nl2data_demo"
# 如需密码，请单独设置 PGPASSWORD 或使用 .pgpass 文件。
export NL2DATA_REDIS_URL="redis://localhost:6379/0"  # 可选
```

### 3.（真实服务配置）加载参考 schema 并导入种子数据

```bash
python demo/seed/seed.py --scale small
```

此命令会在默认 schema 中创建订单履约数据表，并向其中填充两个租户分区的数据，
同时包含预定义的异常样本。

## 运行演示

### 确定性配置

```bash
python demo/run/demo_deterministic.py
```

预期输出包含：

```text
facade lifecycle: created
facade initialized; health: healthy
facade configured: True
facade durable_state: True
first outcome status: succeeded
result rows: 10
replay outcome status: rejected
workflow handle: True
cancelled outcome status: rejected
Deterministic mainflow demo passed.
```

重放请求是重复请求，因此会被拒绝且不会重新执行适配器。这验证了持久化幂等性。
`cancelled outcome status` 行验证了取消快速失败路径：被取消的非终端工作流会在任何
适配器执行前以 `WORKFLOW_CANCELLED` 被拒绝。

### 真实服务配置

```bash
python demo/run/demo_real_service.py
```

预期输出包含：

```text
facade lifecycle: created
facade initialized; health: healthy
facade configured: True
facade durable_state: True
facade memory: True|False
first outcome status: succeeded
result rows: 10
replay outcome status: rejected
workflow handle: True
cancelled outcome status: rejected
Real-service mainflow demo passed.
```

仅当设置了 `NL2DATA_REDIS_URL` 且 Redis 可到达时，`facade memory` 才为 `True`。
`cancelled outcome status` 行验证了取消快速失败路径：被取消的非终端工作流会在任何
适配器执行前以 `WORKFLOW_CANCELLED` 被拒绝。

## 各演示问题的预期输出

标准 10 题套件定义在 `demo/questions/questions.yml` 中。每道题都配有稳定的
SQL 证据查询和确定性的结果形态检查。这些 SQL 仅用于证据和排障；
治理运行时不直接向操作者暴露原始 SQL。

| # | 问题 | SQL 证据文件 | 形态检查 |
| --- | --- | --- | --- |
| 1 | 上月 GMV 最高地区 | `demo/questions/questions.yml` | `region`, `gmv`, `order_count` |
| 2 | 按品类的退款率异常 | `demo/questions/questions.yml` | `category`, `refunded_orders`, `total_orders`, `refund_rate_pct` |
| 3 | 48 小时内未发货订单 | `demo/questions/questions.yml` | `order_id`, `created_at`, `paid_at`, `shipped_at`, `hours_to_ship` |
| 4 | 已付款但未发货订单 | `demo/questions/questions.yml` | `order_id`, `amount`, `paid_at`, `shipment_status` |
| 5 | 周订单环比增长 | `demo/questions/questions.yml` | `week_start`, `order_count`, `gmv`, `prev_order_count`, `wow_growth_pct` |
| 6 | 各渠道新客户首单转化 | `demo/questions/questions.yml` | `channel`, `new_customers`, `converted_customers`, `conversion_pct` |
| 7 | AOV 分位数（P50/P90） | `demo/questions/questions.yml` | `p50_aov`, `p90_aov`, `mean_aov`, `order_count` |
| 8 | 快速增长但库存低的产品 | `demo/questions/questions.yml` | `product_id`, `category`, `stock_quantity`, `unit_price`, `units_sold_7d`, `days_of_cover` |
| 9 | 租户范围的订单与金额汇总 | `demo/questions/questions.yml` | `tenant_id`, `order_count`, `total_amount`, `avg_amount` |
| 10 | 歧义业务术语澄清分支 | `demo/questions/questions.yml` | `metric`, `value`（2 行） |

## 问题-价值矩阵

| # | 目标角色 | 决策意图 | 建议行动阈值 | 注意事项 |
| --- | --- | --- | --- | --- |
| 1 | 区域销售经理 | 跨区域促销预算再平衡 | 月度环比下降 >15% 的地区需调查 | 月末订单延迟入库可能改变月度总计 |
| 2 | 产品运营负责人 | 对异常品类启动质量调查 | 退款率超过品类基线 2 倍时告警 | 退款入账延迟可能导致当日比率被低估 |
| 3 | 履约运营经理 | 加快积压队列和人员调度 | 延迟比例 >8% 时告警 | 承运商同步延迟可能临时高估延迟数 |
| 4 | 客户支持负责人 | 在投诉激增前主动触达 |  backlog 连续 3 天增长时升级 | 部分发货可能因规则不同而被视为未发货 |
| 5 | 业务分析师 | 验证活动影响和需求预测 | 与 8 周趋势偏差 +/-20% 时调查 | 节假日效应可能主导正常周季节性 |
| 6 | 增长营销经理 | 按渠道效率调整获客投入 | 低于中位数转化 60% 的渠道暂停 | 归因窗口期可能后期重新分类转化 |
| 7 | 定价策略负责人 | 按消费层级调整捆绑/折扣策略 | P90/P50 比率变化 >25% 时复核 | 极端企业订单可能拉高上分位数 |
| 8 | 库存计划员 | 优先补货和调拨 | 预计覆盖天数 <10 天时补货 | 库存快照可能跨仓库存在时滞 |
| 9 | 租户账户负责人 | 确认租户业务健康且无跨租户泄露 | 突发零活动或异常波动时调查 | 作用域配置错误应 fail-closed，无泄漏 |
| 10 | 分析消费者 | 在行动前选择正确的指标语义 | 指标映射置信度低时要求明确澄清 | 不同会计口径可能产生有意差异 |

## 失败解读

### 服务不可用 vs 验证失败

| 现象 | 解读 | 操作 |
| --- | --- | --- |
| `NL2DATA_POSTGRES_DSN is not set` | 真实服务配置未就绪 | 设置 DSN 或运行确定性配置 |
| `nl2data-postgres package is not installed` | 可选后端缺失 | 安装可选后端包 |
| `Failed to connect to PostgreSQL` | 真实服务不可用 | 启动服务后重试；确定性配置仍可正常通过 |
| `first outcome status: rejected` 且非重复代码 | 已验证的运行时失败 | 检查错误码和持久化状态记录 |
| `replay outcome status: succeeded` | 重复请求执行了两次 — 回归 | 检查幂等存储和适配器防护 |

### 恢复与排障要点

1. **重放/恢复语义** — 重复请求必须返回 `REJECTED` 并携带 `DUPLICATE_REQUEST`，
   且适配器不会重新执行。如未满足，请检查持久化状态存储连接和幂等 TTL。
2. **取消快速失败** — 对非终端工作流发起取消后再恢复，必须在任何适配器执行前
   产生 `WORKFLOW_CANCELLED`。如果适配器仍执行，说明取消标志未写入状态存储。
3. **过期检查点** — 代码或策略变更后恢复应快速失败并返回 `STALE_CHECKPOINT`。
   变更后仍能成功恢复，说明指纹校验存在回归。
4. **Redis Memory** — Redis 不可用时，真实服务配置会显示 `facade memory: False`，
   但仍应成功完成。仅当启用 Redis 时才失败的，问题指向 Memory 提供方。

## CI 证据

演示配置已接入测试套件：

```bash
pytest tests/integration/test_mainflow_demo.py -q
pytest tests/integration/test_mainflow_demo_real.py -q
```

真实服务测试在所需服务不可用时显式跳过。这是显式跳过，而不是通过。

## 下一步

- [组合与查询生命周期](composition-and-query-lifecycle.md) — 如何构建自己的配置。
- [生产就绪](../reference/production-readiness.md) — 本项目对“生产支持”的定义。
- [故障排查](../operations/troubleshooting.md) — 更深入的失败分析。
