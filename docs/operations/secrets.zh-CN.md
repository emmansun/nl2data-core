# 密钥、实时测试与回滚

> **本页为简体中文翻译**。规范语言为英文；如与[英文原文](secrets.md)冲突，
> 以英文为准。命令与代码不因翻译而改变。
>
> **读者**：运行实时服务或实时提供方配置的运维人员与开发者。
> **前置条件**：[服务配置](services.md)。

## 密钥规则

- **绝不提交**：本仓库中的示例、测试或文档文件都不包含真实令牌、DSN、
  凭据、原始提示词或原始结果。CI 会扫描这些模式。
- **绝不持久化**：凭据绝不进入核心模型、配置指纹、请求元数据、工作流
  状态、遥测、错误或证据。配置序列化只保留引用或打码标记（`<redacted>`）。
- **绝不记录日志**：错误记录在构造时就打码含秘密的细节
  （`redact_key_value`），未知异常类型变成带打码消息的 `INTERNAL_ERROR`
  记录。

## 环境注入（临时、宿主拥有）

Assembly deployment binding 只允许 `env:`、`vault:` 或 `file:` 引用。含凭据的
DSN，以及内联 password、token、secret 或 API key 会在 publish 前被拒绝。Host 可以在
publish verification callback 中临时解析引用，但解析值绝不写入 draft semantic payload、
Bundle fingerprint domain、accepted-assertion manifest、publish audit、catalog envelope、
admin DTO、日志或证据。Audit record 只保留 binding 数量与引用 scheme。

| 服务 | 变量 | 真实服务配置读取时机 |
| --- | --- | --- |
| PostgreSQL | `NL2DATA_POSTGRES_DSN` | 首次构建连接池 |
| Redis | `NL2DATA_REDIS_URL` | 首次构建客户端 |
| MongoDB | `NL2DATA_MONGO_URI`、`NL2DATA_MONGO_DATABASE` | 首次构建客户端 |
| OpenAI | `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL`、`OPENAI_TIMEOUT_SECONDS`、`OPENAI_LIVE_CASES` | 首次构建客户端（key） |

OpenAI 提供方在构造时还接受 `api_key_resolver` 可调用对象或
`client_factory`，用于宿主秘密注入（secret manager、vault、Kubernetes
secrets）。API 密钥只在首次构建客户端时读取。

## 实时 AI 测试

可选的实时评估配置（`nl2data_openai.live_evaluation` 中的
`run_live_openai_evaluation`）针对真实提供方运行确定性 AI 数据集，
并将每个用例分类为 `verified`、`unavailable` 或 `skipped`：

```powershell
$env:OPENAI_API_KEY = "..."          # 临时设置；运行后删除
$env:OPENAI_BASE_URL = "https://api.openai.com/v1"
$env:OPENAI_MODEL = "gpt-4o-mini"
$env:OPENAI_TIMEOUT_SECONDS = "60"
$env:OPENAI_LIVE_CASES = "normal-intent"
python scripts/run_openai_live.py
```

- 只有当每个选中的用例都是 `verified` 时退出码才是 0。
- 没有注入凭据/factory 或 `OPENAI_API_KEY` 时，每个用例都是 `skipped`
  ——默认 CI 不需要凭据，也不做任何网络访问。
- 证据只携带受保护指纹与归一化错误码；`unavailable` 与 `skipped`
  被显式报告，绝不当作通过。

## 服务集成配置

| 配置 | 所需服务 | 验证语义 |
| --- | --- | --- |
| 确定性单元/契约/安全 | 无 | 总是通过（fake 客户端、无网络） |
| 真实服务集成 | PostgreSQL / Redis / MongoDB 可达 | 驱动或服务不可用时显式跳过——绝不视为通过 |
| 实时提供方评估 | OpenAI 兼容端点 + 凭据 | 每个用例 `verified` / `unavailable` / `skipped` |

以可见的跳过原因运行真实服务测试：

```powershell
$env:NL2DATA_POSTGRES_DSN = "postgresql://localhost:5432/nl2data_test"
$env:NL2DATA_REDIS_URL = "redis://localhost:6379"
$env:NL2DATA_MONGO_URI = "mongodb://localhost:27017"
$env:NL2DATA_MONGO_DATABASE = "nl2data_mongo_test"
python -m pytest -rs packages/nl2data-memory-redis/tests/test_integration.py
python -m pytest -rs packages/nl2data-workflow-postgres/tests/test_workflow_postgres_integration.py
```

## 清理

- **环境变量**：运行后从 shell 会话中移除凭据（PowerShell 中
  `Remove-Item Env:OPENAI_API_KEY`，POSIX shell 中 `unset OPENAI_API_KEY`）。
- **状态**：状态存储上的 `cleanup()` 只移除有界的终态快照批次、过期的
  幂等记录与过期的租约——运行中的工作流与有效租约始终保留。保留策略
  归宿主所有：请显式传入截止时间。
- **元数据**：`cleanup_expired` 删除超过保留窗口（默认 30 天）的快照/
  账本记录，包括过期的活跃快照。失败或未授权的运行绝不会替换活跃快照。
- **本地工件**：`.venv`、`dist/` 与服务容器都是可丢弃的；本项目不会
  在其中任何一个存储凭据。

## 回滚

| 对象 | 方式 |
| --- | --- |
| OpenAI 提供方 | 在组合时换回核心的确定性 `FakeModelProvider`——移除 SDK 依赖与网络访问，同时保留相同的解析器、治理与评估门 |
| Bundle 激活 | 对先前已发布且有效的 fingerprint 调用 `rollback_to_fingerprint`；只改变 active pointer，Bundle、manifest、audit 与 supersession history 保持不可变 |
| 快照激活 | 在同一策略下激活先前注册的快照，或重新注册并重新激活先前的发现快照 |
| Schema/数据库 | 降级是部署决策，绝非自动回滚；旧运行时对新部署 fail-closed（`UNSUPPORTED_SCHEMA_VERSION`） |
| 仅文档变更 | 恢复先前的 README 并移除仅文档的 CI 检查；不涉及运行时迁移 |

回滚绝不重写证据：激活较旧的工件会使较新工件产生的证据失效，
过期检查点在任何适配器执行前被拒绝。

## 事件卫生

如果凭据意外泄露：

1. 立即在提供方处轮换它（视为已泄露）。
2. 从 shell 历史与任何本地文件中移除；绝不通过原地 amend 来“修复”
   提交——轮换并只在获得明确授权后重写历史。
3. 下次提交前运行 `python scripts/check_docs.py` 重新扫描文档中的
   秘密模式。

## 下一步

- [故障排查](troubleshooting.md)
- [English source](secrets.md) — 英文原文（规范）。
