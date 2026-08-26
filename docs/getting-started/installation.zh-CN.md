# 安装

> **本页为简体中文翻译**。规范语言为英文；如与[英文原文](installation.md)冲突，
> 以英文为准。命令与代码不因翻译而改变。
>
> **读者**：应用程序开发者。**前置条件**：Python 3.11+ 与 `pip`。本指南中的
> 所有命令都是确定性的，不需要凭据或网络服务。

## 安装核心库

```bash
pip install nl2data-core
```

基础包仅依赖 `pydantic>=2.0,<3` 与 `PyYAML>=6.0`。导入它绝不会加载数据库驱动、
模型提供方 SDK、HTTP 框架或遥测后端——可选后端在您显式组合之前保持不加载。

## 可选 extras

| Extra | 提供 | 依赖 |
| --- | --- | --- |
| `sql` | SQL 查询适配器（SQLite fixtures 使用标准库；`sqlglot` 提供有界 SQL 编译） | `sqlglot>=25.0,<30` |
| `postgres` | PostgreSQL 发现/一致性配置 | `psycopg[binary,pool]>=3.1,<4` |
| `nl2data-workflow-postgres` | 共享工作流状态后端（`PostgreSQLStateStore`）作为独立包 | `psycopg[binary,pool]>=3.1,<4` |

```bash
# 例如：SQL 适配器
pip install "nl2data-core[sql]"
```

Extras 是惰性的：安装它们不会在包导入时导入任何东西。驱动只在首次构建
真实服务客户端时才被加载。

## 安装可选的 Redis 内存后端

Redis 内存后端是一个独立发行包，使用 Redis 实现核心 `MemoryProvider` 契约：

```bash
pip install nl2data-memory-redis
```

它依赖 `nl2data-core>=0.1.0` 与 `redis>=5.0,<7`。驱动在包导入时不会被加载，
仅在首次使用时惰性加载。

## 安装可选的 OpenAI 提供方

OpenAI 提供方是一个独立发行包，实现提供方中立的 `ModelProvider` 契约：

```bash
pip install nl2data-openai
```

它依赖 `nl2data-core>=0.1.0` 与 `openai>=1.40,<3`。OpenAI SDK 在包导入时
绝不会被导入；客户端在首次使用时由注入的凭据惰性构建。

## 安装可选的 PostgreSQL 语义目录

持久化语义目录是一个独立发行包，实现了 `SemanticSnapshotCatalog` 边界，
使用 PostgreSQL 存储快照、提案集、Bundle 发布与活动指针：

```bash
pip install nl2data-semantic-catalog-postgres
```

它依赖 `nl2data-core>=0.1.0` 与 `psycopg[binary,pool]>=3.1,<4`。驱动在包
导入时绝不会被导入；只有从 DSN 构造目录时才会惰性加载。

从源码检出安装时，所有可选包一起安装：

```bash
pip install -e ".[dev]"
pip install -e packages/nl2data-openai
pip install -e packages/nl2data-semantic-catalog-postgres
pip install -e packages/nl2data-postgres
pip install -e packages/nl2data-mongodb
pip install -e packages/nl2data-memory-redis
```

## 为开发而安装

完整的贡献者环境（虚拟环境、测试工具、lint、类型检查与包构建）请参见
[本地开发](../development/local-development.md)。

## 验证安装

```python
import nl2data

print(nl2data.__all__)  # public API surface
```

如果打印出公共符号列表，说明核心已安装。继续阅读
[快速上手](quickstart.md)。

## 下一步

- [快速上手](quickstart.md) — 组合一个 facade 并提交首个查询。
- [组合与查询生命周期](../guides/composition-and-query-lifecycle.md)
  — 受保护结果、澄清、取消与健康操作。
- [English source](installation.md) — 英文原文（规范）。
