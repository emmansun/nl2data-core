# 能力与支持

> **本页为简体中文翻译**。规范语言为英文；如与[英文原文](capabilities.md)冲突，
> 以英文为准。状态词汇与能力标识符不因翻译而改变。
>
> **读者**：评估本库的决策者。**前置条件**：无。状态词汇：**Implemented
> （已实现）**（存在于源码）、**Conformant（符合规范）**（通过确定性一致性
> 测试套件）、**Verified（已验证）**（通过真实服务/实时提供方运行）、
> **Production Supported（生产支持）**（有部署契约 + 运维指引）。

## 能力矩阵

| 能力 | 状态 | 验证方式 | 需要 |
| --- | --- | --- | --- |
| 公共门面（`NL2Data`/`create_facade`）、生命周期、受保护结果 | Implemented + conformant | 确定性套件 | — |
| 公共模型/错误（不可变、打码） | Implemented + conformant | 单元 + 安全套件 | — |
| 严格版本化配置 + 指纹 | Implemented + conformant | 单元套件 | — |
| Semantic IR + 规范指纹 | Implemented + conformant | 契约套件 | — |
| Semantic View 解析（fail-closed） | Implemented + conformant | 契约 + 安全套件 | — |
| Semantic Model Bundles + 目录 | Implemented + conformant | 契约 + 安全套件 | — |
| 持久化语义目录（PostgreSQL） | Implemented + verified | 真实服务 CI 配置 | `nl2data-semantic-catalog-postgres` + 服务 |
| 受治理工作流运行时（确定性） | Implemented + conformant | 一致性套件 | — |
| 查询生命周期：澄清、取消、句柄、能力/健康 | Implemented + conformant | 集成套件 | — |
| 持久工作流状态（SQLite） | Implemented + conformant | 契约套件 | — |
| 持久工作流状态（PostgreSQL，租约 + 围栏） | Implemented + verified | 真实服务 CI 配置 | `nl2data-workflow-postgres` 包 + 服务 |
| 内存（in-memory） | Implemented + conformant | 一致性套件 | — |
| 内存（Redis） | Implemented + verified | 真实服务 CI 配置 | `redis` extra + 服务 |
| SQL 适配器（SQLite fixtures） | Implemented + conformant | 一致性套件 | `sql` extra |
| SQL 适配器（PostgreSQL） | Implemented + verified | 真实服务 CI 配置 | `nl2data-postgres` 包 + 服务 |
| MongoDB 适配器 | Implemented + verified | 真实服务 CI 配置 | `nl2data-mongodb` + 服务 |
| 元数据发现 + 生产配置 | Implemented + verified | 真实服务 CI 配置 | `nl2data-postgres`/`nl2data-mongodb` 包 + 服务 |
| AI 意图解析 + 指令契约 | Implemented + conformant | 评估套件（fake provider） | — |
| OpenAI 结构化输出提供方 | Implemented；按需实时验证 | `run_openai_live.py` | `nl2data-openai` + 凭据 |
| 租户隔离 + 范围指纹 | Implemented + conformant | 一致性套件 | — |
| 管理控制平面服务（传输中立） | Implemented + conformant | 契约 + 安全套件 | `nl2data-admin-service` |
| 遥测/审计端口（in-memory 汇点） | Implemented + conformant | 契约套件 | — |

## 当前不支持的内容

- **HTTP 托管**：没有 `nl2data_http` 包；未来的宿主程序面向传输中立的
  `FacadePort` 编程。
- **流式线协议**、代理循环，以及超出有界扩展点的自主修复。
- **公共的审批必需结果状态**（仅为内部运行时事件）。
- **工作流状态除 PostgreSQL 之外的服务后端**（MongoDB/HTTP 状态后端
  未实现）。
- **恰好一次的外部执行**——恢复按设计是 at-least-once。
- MongoDB 适配器中的 **$lookup/跨集合连接、Atlas Search/向量阶段、
  map-reduce、变更流与写入**。

## 支持政策

- 公共 `nl2data` API 是唯一受支持的应用程序表面；`nl2data_core` 仅限
  贡献者使用，恕不另行通知即可变更。
- 可选兄弟发行包（`nl2data-openai`、`nl2data-semantic-catalog-postgres`、
  `nl2data-admin-service`、`nl2data-postgres`、`nl2data-mongodb`）通过其文档化的包表面受支持；其内部实现恕不另行通知
  即可变更。
- 真实服务与实时提供方结果**依赖环境**：`skipped`/`unavailable` 结果
  绝不是验证。只有 `verified` 才算服务兼容性的证据。
- 受支持的 Python 版本：3.11、3.12、3.13（CI 矩阵）。
- CI 中受支持的服务：PostgreSQL 16、Redis 7、MongoDB 7、
  OpenAI 兼容网关（实时、按需）。

## 门面暴露的特性开关

`facade.capabilities().features` 报告有界的特性标识符：
`async_query`、`sync_query`、`workflow_handles`、`cancellation`、
`clarification`。`configured`、`runtime`（`custom`/`deterministic`）、
`provider`、`adapter`、`memory`、`tenant_scoped`、`durable_state` 与
`config_fingerprint` 描述组合出的实例。

## 下一步

- [兼容性](compatibility.md) — 版本与迁移政策。
- [生产就绪](production-readiness.md) — 对本库而言“生产”意味着什么。
- [English source](capabilities.md) — 英文原文（规范）。
