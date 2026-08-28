# NL2Data Documentation

> **Normative language: English.** This index and every English page under
> `docs/` are the canonical source for technical contracts, API names,
> configuration keys, error codes, support claims, and diagrams. Chinese
> pages are reader-facing translations and link back to their English
> source. Pages without a Chinese translation are **English-first** — see
> the language table below.

NL2Data is a governed and extensible Python framework for natural-language
access to heterogeneous enterprise data. It composes a **Semantic IR**,
**Semantic View / Bundle**, a deterministic **governed workflow runtime**,
and optional database, memory, and model-provider backends behind one
public facade: `import nl2data`.

- **Distribution**: `nl2data-core` (Python 3.11+)
- **Optional sibling**: `nl2data-openai` (OpenAI structured-output provider)
- **Internal package**: `nl2data_core` — contributor-only, never imported by applications

## Documentation sections

| Section | Intended reader | Prerequisites | Start here |
| --- | --- | --- | --- |
| [Getting started](getting-started/installation.md) | Application developers who want to install and run a first query | Python 3.11+ and `pip` | [Installation](getting-started/installation.md) → [Quickstart](getting-started/quickstart.md) |
| [Guides](guides/composition-and-query-lifecycle.md) | Application integrators composing the library | Getting started | [Composition and query lifecycle](guides/composition-and-query-lifecycle.md) · [Metadata to Bundle](guides/metadata-to-bundle.md) · [Mainflow demo](guides/mainflow-demo.md) |
| [Architecture](architecture/overview.md) | Architects, security reviewers, and maintainers | Basic knowledge of the public API | [Architecture overview](architecture/overview.md) |
| [Development](development/local-development.md) | Contributors to this repository | Python tooling (pytest, mypy, ruff) | [Local development](development/local-development.md) |
| [Operations](operations/services.md) | Operators and platform engineers | PostgreSQL, Redis, MongoDB, or OpenAI access as needed | [Service configuration](operations/services.md) |
| [Reference](reference/configuration.md) | Everyone looking up a specific fact | None | [Configuration](reference/configuration.md), [Error codes](reference/error-codes.md), [CompositionProfile](reference/composition-profile.md) |

## Language navigation

English is the normative source. Chinese pages are staged translations:
they preserve code, identifiers, Mermaid meaning, normative requirements,
and security warnings, and they always link to the canonical English page.

| Page | Chinese translation |
| --- | --- |
| [Docs index (this page)](README.md) | [索引 (简体中文)](README.zh-CN.md) — 完整翻译 |
| [Installation](getting-started/installation.md) | [安装 (简体中文)](getting-started/installation.zh-CN.md) — 完整翻译 |
| [Quickstart](getting-started/quickstart.md) | [快速上手 (简体中文)](getting-started/quickstart.zh-CN.md) — 完整翻译 |
| [Composition and query lifecycle](guides/composition-and-query-lifecycle.md) | English-first（暂无中文翻译，请阅读英文原文） |
| [Mainflow demo](guides/mainflow-demo.md) | [主流程演示 (简体中文)](guides/mainflow-demo.zh-CN.md) — 完整翻译 |
| [Metadata to active Bundle](guides/metadata-to-bundle.md) | [从元数据到激活 Bundle](guides/metadata-to-bundle.zh-CN.md) — 完整翻译 |
| [Architecture overview](architecture/overview.md) | [架构总览 (简体中文)](architecture/overview.zh-CN.md) — 完整翻译 |
| [Execution flow](architecture/execution-flow.md) | [执行流程 (简体中文)](architecture/execution-flow.zh-CN.md) — 完整翻译 |
| [Package boundaries](architecture/package-boundaries.md) | English-first（暂无中文翻译，请阅读英文原文） |
| [Governance and tenancy](architecture/governance-and-tenancy.md) | English-first（暂无中文翻译，请阅读英文原文） |
| [Workflow state](architecture/workflow-state.md) | English-first（暂无中文翻译，请阅读英文原文） |
| [Metadata lifecycle](architecture/metadata-lifecycle.md) | English-first（暂无中文翻译，请阅读英文原文） |
| [Semantic layer](architecture/semantic-layer.md) | [语义层 (简体中文)](architecture/semantic-layer.zh-CN.md) — 完整翻译 |
| [ADR: pii masking enforcement point](architecture/adr-pii-masking-enforcement-point.md) | [ADR：pii 掩码执行点 (简体中文)](architecture/adr-pii-masking-enforcement-point.zh-CN.md) — 完整翻译 |
| [Evidence and fingerprints](architecture/evidence-and-fingerprints.md) | [证据与指纹 (简体中文)](architecture/evidence-and-fingerprints.zh-CN.md) — 完整翻译 |
| [Local development](development/local-development.md) | English-first（暂无中文翻译，请阅读英文原文） |
| [Adding an adapter or provider](development/adding-adapter-or-provider.md) | English-first（暂无中文翻译，请阅读英文原文） |
| [Service configuration](operations/services.md) | English-first（暂无中文翻译，请阅读英文原文） |
| [Secrets and live testing](operations/secrets.md) | [密钥与实时测试 (简体中文)](operations/secrets.zh-CN.md) — 完整翻译 |
| [Troubleshooting](operations/troubleshooting.md) | English-first（暂无中文翻译，请阅读英文原文） |
| [Configuration reference](reference/configuration.md) | [配置参考 (简体中文)](reference/configuration.zh-CN.md) — 完整翻译 |
| [CompositionProfile reference](reference/composition-profile.md) | [CompositionProfile 参考 (简体中文)](reference/composition-profile.zh-CN.md) — 完整翻译 |
| [Error codes](reference/error-codes.md) | English-first（暂无中文翻译，请阅读英文原文） |
| [Capabilities and support](reference/capabilities.md) | [能力与支持 (简体中文)](reference/capabilities.zh-CN.md) — 完整翻译 |
| [Compatibility](reference/compatibility.md) | [兼容性 (简体中文)](reference/compatibility.zh-CN.md) — 完整翻译 |
| [Production readiness](reference/production-readiness.md) | English-first（暂无中文翻译，请阅读英文原文） |

## Reader paths

- **New user**: [Installation](getting-started/installation.md) → [Quickstart](getting-started/quickstart.md) → [Metadata to active Bundle](guides/metadata-to-bundle.md) → [Composition and query lifecycle](guides/composition-and-query-lifecycle.md) → [Mainflow demo](guides/mainflow-demo.md)
- **Integrator**: [Architecture overview](architecture/overview.md) → [Semantic layer](architecture/semantic-layer.md) → [Package boundaries](architecture/package-boundaries.md) → [Governance and tenancy](architecture/governance-and-tenancy.md) → [CompositionProfile reference](reference/composition-profile.md)
- **Operator**: [Service configuration](operations/services.md) → [Secrets and live testing](operations/secrets.md) → [Troubleshooting](operations/troubleshooting.md)
- **Contributor**: [Local development](development/local-development.md) → [Adding an adapter or provider](development/adding-adapter-or-provider.md) → [Architecture](architecture/overview.md)

## Status vocabulary

Documentation distinguishes four levels of capability status:

- **Implemented** — the feature exists in source.
- **Conformant** — the feature passes its deterministic conformance suite.
- **Verified** — the feature has passed a real-service or live-provider run.
- **Production Supported** — the feature is covered by a deployment contract
  and operational guidance in this documentation set.

Real PostgreSQL, Redis, MongoDB, and OpenAI tests are environment-dependent;
every guide states whether a command uses fake clients, service containers,
or live credentials. See [Production readiness](reference/production-readiness.md).
