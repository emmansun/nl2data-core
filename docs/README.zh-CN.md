# NL2Data 文档

> **本页为简体中文翻译**。规范语言为英文；如与[英文原文](README.md)冲突，
> 以英文为准。技术契约（API 名称、配置键、错误码、图表含义）不因翻译而改变。

NL2Data 是一个受治理、可扩展的 Python 框架，用于以自然语言访问异构企业数据。
它通过一个公共门面（facade）组合 **Semantic IR**、**Semantic View / Bundle**、
确定性的**受治理工作流运行时**，以及可选的数据库、内存与模型提供方后端：
`import nl2data`。

- **发行包**：`nl2data-core`（Python 3.11+）
- **可选兄弟包**：`nl2data-openai`（OpenAI 结构化输出提供方）
- **内部包**：`nl2data_core` — 仅限贡献者使用，应用程序不得导入

## 文档分区

| 分区 | 目标读者 | 前置条件 | 从这里开始 |
| --- | --- | --- | --- |
| [快速上手](getting-started/installation.md) | 希望安装并运行首个查询的应用开发者 | Python 3.11+ 与 `pip` | [安装](getting-started/installation.md) → [快速上手](getting-started/quickstart.md) |
| [指南](guides/composition-and-query-lifecycle.md) | 组合使用本库的应用集成者 | 快速上手 | [组合与查询生命周期](guides/composition-and-query-lifecycle.md) |
| [架构](architecture/overview.md) | 架构师、安全评审人员与维护者 | 了解公共 API 基础 | [架构总览](architecture/overview.md) |
| [开发](development/local-development.md) | 本仓库贡献者 | Python 工具链（pytest、mypy、ruff） | [本地开发](development/local-development.md) |
| [运维](operations/services.md) | 运维与平台工程师 | 按需访问 PostgreSQL、Redis、MongoDB 或 OpenAI | [服务配置](operations/services.md) |
| [参考](reference/configuration.md) | 查询具体事实的任何人 | 无 | [配置](reference/configuration.md)、[错误码](reference/error-codes.md) |

## 语言导航

英文为规范来源。中文页面为面向读者的分阶段翻译：保留代码、标识符、
Mermaid 含义、规范性要求与安全警告，并始终链接到规范的英文页面。

| 页面 | 中文翻译 |
| --- | --- |
| [文档索引（本页）](README.md) | [索引 (简体中文)](README.zh-CN.md) — 完整翻译 |
| [安装](getting-started/installation.md) | [安装 (简体中文)](getting-started/installation.zh-CN.md) — 完整翻译 |
| [快速上手](getting-started/quickstart.md) | [快速上手 (简体中文)](getting-started/quickstart.zh-CN.md) — 完整翻译 |
| [组合与查询生命周期](guides/composition-and-query-lifecycle.md) | English-first（暂无中文翻译，请阅读英文原文） |
| [架构总览](architecture/overview.md) | [架构总览 (简体中文)](architecture/overview.zh-CN.md) — 完整翻译 |
| [执行流程](architecture/execution-flow.md) | [执行流程 (简体中文)](architecture/execution-flow.zh-CN.md) — 完整翻译 |
| [包边界](architecture/package-boundaries.md) | English-first（暂无中文翻译，请阅读英文原文） |
| [治理与多租户](architecture/governance-and-tenancy.md) | English-first（暂无中文翻译，请阅读英文原文） |
| [工作流状态](architecture/workflow-state.md) | English-first（暂无中文翻译，请阅读英文原文） |
| [元数据生命周期](architecture/metadata-lifecycle.md) | English-first（暂无中文翻译，请阅读英文原文） |
| [证据与指纹](architecture/evidence-and-fingerprints.md) | [证据与指纹 (简体中文)](architecture/evidence-and-fingerprints.zh-CN.md) — 完整翻译 |
| [本地开发](development/local-development.md) | English-first（暂无中文翻译，请阅读英文原文） |
| [新增适配器或提供方](development/adding-adapter-or-provider.md) | English-first（暂无中文翻译，请阅读英文原文） |
| [服务配置](operations/services.md) | English-first（暂无中文翻译，请阅读英文原文） |
| [密钥与实时测试](operations/secrets.md) | [密钥与实时测试 (简体中文)](operations/secrets.zh-CN.md) — 完整翻译 |
| [故障排查](operations/troubleshooting.md) | English-first（暂无中文翻译，请阅读英文原文） |
| [配置参考](reference/configuration.md) | [配置参考 (简体中文)](reference/configuration.zh-CN.md) — 完整翻译 |
| [错误码](reference/error-codes.md) | English-first（暂无中文翻译，请阅读英文原文） |
| [能力与支持](reference/capabilities.md) | [能力与支持 (简体中文)](reference/capabilities.zh-CN.md) — 完整翻译 |
| [兼容性](reference/compatibility.md) | [兼容性 (简体中文)](reference/compatibility.zh-CN.md) — 完整翻译 |
| [生产就绪](reference/production-readiness.md) | English-first（暂无中文翻译，请阅读英文原文） |

## 读者路径

- **新用户**：[安装](getting-started/installation.md) → [快速上手](getting-started/quickstart.md) → [组合与查询生命周期](guides/composition-and-query-lifecycle.md)
- **集成者**：[架构总览](architecture/overview.md) → [包边界](architecture/package-boundaries.md) → [治理与多租户](architecture/governance-and-tenancy.md)
- **运维**：[服务配置](operations/services.md) → [密钥与实时测试](operations/secrets.md) → [故障排查](operations/troubleshooting.md)
- **贡献者**：[本地开发](development/local-development.md) → [新增适配器或提供方](development/adding-adapter-or-provider.md) → [架构](architecture/overview.md)

## 状态词汇

文档区分四级能力状态：

- **Implemented（已实现）** — 该特性存在于源码中。
- **Conformant（符合规范）** — 该特性通过其确定性一致性测试套件。
- **Verified（已验证）** — 该特性已通过真实服务或实时提供方运行。
- **Production Supported（生产支持）** — 该特性在本文档集中有部署契约
  与运维指引覆盖。

真实的 PostgreSQL、Redis、MongoDB 与 OpenAI 测试依赖环境；每篇指南都会说明
命令使用的是 fake 客户端、服务容器还是真实凭据。参见
[生产就绪](reference/production-readiness.md)。
