# 配置参考

> **本页为简体中文翻译**。规范语言为英文；如与[英文原文](configuration.md)冲突，
> 以英文为准。代码、配置键与默认值不因翻译而改变。
>
> **读者**：集成者与运维人员。**前置条件**：[安装](../getting-started/installation.md)。

## 加载配置

配置通过公共的 `load_config` 函数加载：

```python
from nl2data import load_config

config = load_config(
    {
        "schema_version": 1,
        "service": {"name": "example", "environment": "production"},
    }
)
```

加载器将默认值与提供的值编译为**不可变的生效快照**，并带有确定性的
配置指纹。激活是 fail-closed（安全失败）的：

| 失败 | 错误码 | 可重试 |
| --- | --- | --- |
| 不支持的 schema 版本 | `UNSUPPORTED_SCHEMA_VERSION` | 否 |
| 严格核心分区中的未知字段 | `INVALID_CONFIGURATION` / `MALFORMED_CONFIGURATION` | 否 |
| 通过 extensions 覆盖受保护核心字段 | `PROTECTED_FIELD_OVERRIDE` | 否 |
| 格式错误或越界值 | `MALFORMED_CONFIGURATION` | 否 |

快照是冻结的：应用程序代码无法修改已激活的配置。键顺序不同但等价的
配置会产生等价快照与相同指纹。

## 核心字段（schema 版本 1）

### `service`（必填）

| 字段 | 类型 | 边界 | 默认值 |
| --- | --- | --- | --- |
| `name` | string | 1–128 字符，**必填** | — |
| `version` | string | 1–64 字符 | `None` |
| `environment` | string | 1–64 字符 | `development` |

### `runtime`（可选）

| 字段 | 类型 | 边界 | 默认值 |
| --- | --- | --- | --- |
| `max_attempts` | int | 1–10 | 3 |
| `timeout_seconds` | float | 0–3600 | 30.0 |
| `telemetry_enabled` | bool | — | `true` |
| `max_artifact_bytes` | int | 1,024 – 1 GiB | 1,048,576 |
| `shutdown_grace_seconds` | float | 0–300 | 5.0 |

### `secrets`（可选）

秘密引用——绝不使用明文值：

```python
{
    "schema_version": 1,
    "service": {"name": "example"},
    "secrets": {
        "openai_api_key": {"kind": "env", "name": "OPENAI_API_KEY"},
    },
}
```

- 只序列化引用（`kind` + `name`）；解析出的明文绝不能存储或输出。
- `safe_dump()` 与诊断输出不含任何明文秘密值——只有引用或打码标记。
- 生产安全的转储只保留引用。

### `extensions`（可选）

用于宿主特定设置的任意标量键/值分区：

```python
"extensions": {
    "my_host": {"values": {"feature_flag": True, "max_parallel": 4}}
}
```

值是有界标量（string/int/float/bool）。受保护的核心字段不能通过
extensions 覆盖。

### `model`（可选）

提供方无关的模型调用设置（`ModelConfig`），默认使用确定性 fake 提供方：

| 字段 | 类型 | 边界 | 默认值 |
| --- | --- | --- | --- |
| `provider_name` | string | 1–64 字符 | `fake` |
| `model_name` | string | 1–128 字符 | `fake-model` |
| `max_input_chars` | int | 1,000–1,000,000 | 100,000 |
| `max_output_tokens` | int | 1–131,072 | 4,096 |
| `timeout_seconds` | float | 0–3600 | 30.0 |
| `max_attempts` | int | 1–10 | 3 |
| `temperature` | float | 0.0–2.0 | `None` |
| `fingerprint` | string | `sha256:<64 hex>` | 计算得出 |

model 分区绝不携带凭据；提供方秘密仍是 `secrets` 中的 `SecretReference`
条目。

## 可选依赖配置

可选服务在核心配置文档**之外**、由宿主拥有的端点配置：

| 配置 | Extra | 配置位置 |
| --- | --- | --- |
| SQL 适配器 | `sql` | `sqlglot` 编译的有界 SQL；SQLite fixtures 无需服务 |
| PostgreSQL 共享状态 | `nl2data-workflow-postgres` | `WorkflowPostgresConfig` 设置 + `NL2DATA_POSTGRES_DSN` |
| PostgreSQL 语义目录 | `nl2data-semantic-catalog-postgres` | `SemanticCatalogConfig`（命名空间 + 边界）+ 宿主注入的 DSN |
| Redis Memory | `nl2data-memory-redis` | `RedisMemoryConfig`（命名空间 + 边界）+ 连接 URL |
| MongoDB 适配器/发现 | `nl2data-mongodb` | `ProductionDiscoveryConfig.bounds` + `NL2DATA_MONGO_URI` |
| OpenAI 提供方 | `nl2data-openai` | `OpenAIProviderConfig` + `OPENAI_API_KEY`/`base_url` |

宿主拥有的端点/秘密注入绝不会把供应商凭据呈现为核心配置字段。

## PostgreSQL 语义目录（可选包）

持久化目录以 `nl2data-semantic-catalog-postgres` 发行，并由不可变的
`SemanticCatalogConfig` 模型配置。该模型只携带行为边界与安全的秘密
*引用*——绝不携带 DSN 本身。宿主从其自身的秘密管理向目录构造函数注入
DSN（`dsn_secret_ref` 命名宿主侧的该秘密）。

| 字段 | 类型 | 边界 | 默认值 |
| --- | --- | --- | --- |
| `namespace` | string | `^[A-Za-z][A-Za-z0-9_]{0,63}$`，**必填** | — |
| `dsn_secret_ref` | string | 1–128 字符 | `None` |
| `pool_size` | int | 1–64 | 5 |
| `connect_timeout_seconds` | float | 0.1–30 | 5.0 |
| `command_timeout_seconds` | float | 0.1–120 | 10.0 |
| `pool_acquire_timeout_seconds` | float | 0.1–60 | 5.0 |
| `schema_version` | int | 1（受支持） | 1 |
| `snapshot_retention_seconds` | float | 60 – 31,536,000 | 604,800 |
| `event_retention_seconds` | float | 60 – 31,536,000 | 604,800 |
| `cleanup_batch_size` | int | 1–10,000 | 500 |
| `max_envelope_bytes` | int | 4 KiB – 16 MiB | 1,048,576 |
| `max_payload_bytes` | int | 1 KiB – 8 MiB（≤ 信封） | 524,288 |
| `max_bundle_history` | int | 1–10,000 | 100 |
| `max_active_pointers_per_scope` | int | 1–1,024 | 256 |

`namespace` 是拥有每个目录表的 PostgreSQL schema；它在每个应用程序与
环境中必须唯一，以便共享同一数据库服务的部署永远不会观察到彼此的记录。
比运行时受支持版本更新的 `schema_version` 会 fail-closed（安全失败）。
参见[服务](../operations/services.md)获取运维手册。

## 指纹稳定性

配置指纹是确定性的：键顺序不同但等价的输入产生相同的
`sha256:<小写十六进制>` 身份，且秘密值绝不进入指纹。参见
[证据与指纹](../architecture/evidence-and-fingerprints.md)。

## 下一步

- [错误码](error-codes.md)
- [English source](configuration.md) — 英文原文（规范）。
