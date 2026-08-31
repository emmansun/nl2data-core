# 兼容性

> **本页为简体中文翻译**。规范语言为英文；如与[英文原文](compatibility.md)冲突，
> 以英文为准。版本号与依赖范围不因翻译而改变。
>
> **读者**：规划升级的集成者。**前置条件**：[能力与支持](capabilities.md)。

## 版本管理

| 工件 | 版本策略 | 说明 |
| --- | --- | --- |
| `nl2data-core` | 0.1.0（Alpha） | 公共 API 在文档化表面内保持稳定；`Development Status :: 3 - Alpha` 分类器 |
| `nl2data-openai` | 0.1.0（Alpha） | 依赖 `nl2data-core>=0.1.0`；`openai>=1.40,<3` |
| `nl2data-mongodb` | 0.1.0（Alpha） | 依赖 `nl2data-core>=0.1.0`；`pymongo>=4.6,<5` |
| `nl2data-workflow-postgres` | 0.1.0（Alpha） | 依赖 `nl2data-core>=0.1.0`；`psycopg[binary,pool]>=3.1,<4` |
| `nl2data-memory-redis` | 0.1.0（Alpha） | 依赖 `nl2data-core>=0.1.0`；`redis>=5.0,<7` |
| 配置 schema | `schema_version: 1`（字面量） | 不支持的版本 fail-closed——绝不静默降级 |
| 工作流状态快照 | 显式 `schema_version` | 仅加法迁移；比运行时更新的 schema 被拒绝 |
| 指令契约 | 带版本的 `ModelInstructionBundle` | 不支持的 bundle 版本 fail-closed（`INSTRUCTION_VERSION_INCOMPATIBLE`） |

## 兼容的内容

验证计划和证据使用显式版本与策略身份。`compatibility-v1` 仅表示明确的结构验证；
`production-v1` 要求三层全部通过。没有证据的旧发布标记为 `legacy_unverified`，
不会自动升级或静默降级。仅改变验证计划或证据不会改变 Bundle 语义指纹，但可能
要求重新批准和验证。

- **公共 API 稳定性**：只导入 `nl2data` 文档化符号的应用程序遵循门面契约。
  `NL2DataEngine` 仍可用于源码兼容；新代码应使用 `NL2Data`/`create_facade`。
- **确定性身份**：键顺序不同但等价的输入产生相同指纹，因此规范工件在
  跨版本、跨进程时仍可比较。
- **向后兼容的 IR 执行**：未配置视图注册表时，现有的未绑定 IR 完全按
  之前的方式继续执行——不会伪造任何视图身份。
- **手工回退路径**：手工编写的描述符/bundle 与没有快照绑定的适配器
  继续原样工作（`expected_snapshot_fingerprint=None`）。

## 不兼容的内容（设计使然）

- **直接导入 `nl2data_core` 已弃用**，可能恕不另行通知即变更。请通过
  门面迁移：
  - 尽可能将 `NL2DataEngine` 用法替换为 `NL2Data`/`create_facade`。
  - 将组合输入移入 `CompositionProfile`。
  - 配置仍通过公共的 `load_config` 加载；类型化配置模型在公共配置 API
    发布前保持内部。
- **MongoDB 适配器位于 `nl2data-mongodb`**：核心中的
  `nl2data_core.adapters.mongodb` 模块已移除。请从 `nl2data_mongodb`
  包导入 MongoDB 符号；核心发行版不再包含 MongoDB 适配器或
  `mongodb` extra。
- **Redis 内存后端位于 `nl2data-memory-redis`**：核心中的
  `nl2data_core.memory.redis_*` 模块与 `RedisMemoryProvider` 已移除。请从
  `nl2data_memory_redis` 导入 `RedisMemoryProvider` / `RedisMemoryConfig`；
  核心发行版不再包含 Redis 内存实现。
- **PostgreSQL 工作流状态位于 `nl2data-workflow-postgres`**：核心中的
  `nl2data_core.workflow.postgres_*` 模块已移除。请从
  `nl2data_workflow_postgres` 包导入 `PostgreSQLStateStore` /
  `WorkflowPostgresConfig`；核心发行版不再包含 PostgreSQL 工作流后端。
- **原始载荷绝不成为任何契约的一部分**：SQL/MQL、提示词、结果与凭据
  在任何地方都没有序列化形式——没有任何东西依赖它们，因此也没有任何
  东西可以与它们“兼容”。

## 迁移政策

| 变更 | 政策 |
| --- | --- |
| View/Bundle 激活或回滚 | 显式：配置注册表，从受信任上下文解析视图；一旦配置注册表，产生新 IR 的路径必须携带视图引用。回滚是对称的 |
| Schema/数据库版本 | 降级是部署决策，绝非自动回滚；旧运行时对新部署 fail-closed |
| 指纹版本/轮换 | 新版本是带新指纹的新载荷；旧证据失效，过期检查点在任何适配器执行前被拒绝 |
| 可选后端激活 | 只有通过强制一致性套件后才可（例如 `tests/conformance/test_workflow_runtime_conformance.py`、`tests/contract/test_backend_conformance.py`） |

## 环境兼容性

| 组件 | 兼容版本 | 说明 |
| --- | --- | --- |
| Python | 3.11、3.12、3.13 | CI 矩阵；`requires-python >=3.11` |
| pydantic | `>=2.0,<3` | 核心依赖 |
| PyYAML | `>=6.0` | 核心依赖 |
| sqlglot | `>=25.0,<30` | `sql` extra |
| psycopg | `>=3.1,<4`（binary + pool） | `postgres` extra、`nl2data-workflow-postgres` |
| pymongo | `>=4.6,<5` | `nl2data-mongodb` |
| redis | `>=5.0,<7` | `nl2data-memory-redis` |
| openai | `>=1.40,<3` | `nl2data-openai` |

## 下一步

- [生产就绪](production-readiness.md)
- [English source](compatibility.md) — 英文原文（规范）。
