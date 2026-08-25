# 证据与指纹

> **本页为简体中文翻译**。规范语言为英文；如与[英文原文](evidence-and-fingerprints.md)
> 冲突，以英文为准。代码、标识符、Mermaid 图表含义与安全警告不因翻译而改变。
>
> **读者**：架构师、安全评审人员、运维人员，以及任何需要推理证据身份的人。
> **前置条件**：[架构总览](overview.md)。

## 为什么（Why）

确定性的不透明指纹之所以存在，是因为系统必须能够**引用**一个载荷——
配置、Semantic View、策略、目录快照、编译产物、工作流检查点、Memory 记录——
而无需**包含**它。指纹使四件事成为可能：

- **可复现性** — 相同的安全输入总是产生相同的身份，跨进程、跨 worker、
  跨运行。
- **兼容性检查** — 变更的视图、Bundle、策略或目录会使所有先前记录的引用
  失效，因此过期证据永远无法静默地为执行授权。
- **授权绑定** — 租户范围、策略、视图与授权工件按指纹绑定，因此链条中
  任何一处的替换都会破坏绑定。
- **缓存/幂等关联与漂移检测** — 幂等键、工作流检查点与快照比较按身份
  关联记录，不匹配即表现为漂移。
- **安全遥测** — 日志、证据与报告可以携带稳定身份，而无需携带载荷本身。

指纹是**不透明的身份/引用，而非能力**。它不授予授权，不暴露源载荷，
也不可逆：SHA-256 是单向的，且规范形式刻意不是秘密或原始用户材料的
序列化。

## 是什么（What）

### 哪些内容会被指纹化

| 工件 | 指纹覆盖范围 | 出现在哪里 |
| --- | --- | --- |
| 生效配置 | 所有核心字段的规范快照（秘密被替换为引用，绝不出现明文） | capabilities 上的 `config_fingerprint`、遥测/审计上下文 |
| Semantic View / 投影 | 视图身份/版本、模型/目录、活跃 Bundle 身份/版本/指纹、租户范围、主体授权、目的、策略、适配器能力、特性开关 | IR 引用、工作流证据、Memory 记录 |
| Semantic Model Bundle | 规范载荷（描述符 + 度量、聚合、粒度、来源、信任标记、出处） | 激活、View 解析、检查点 |
| 元数据快照 | 按对象/关系 id 排序的规范序列化 | 账本、激活策略、漂移比较 |
| 策略范围 | 规范策略内容 + 租户绑定 | 治理事实、授权工件 |
| 租户范围 | 规范受信任范围（绝不使用原始租户/主体 ID） | 治理、工作流状态、命名空间、结果 |
| 编译产物 | 后端中立的编译证据（仅指纹——绝无原始 SQL/MQL） | 守卫、授权、受保护结果 |
| 受保护结果 | 归一化标量行 | 结果、幂等记录 |
| 指令包 / 输出 schema | 带版本号的指令契约 | 调用元数据、门证据、评估证据 |

### 刻意排除的内容

指纹输入在**哈希之前**就被过滤。以下内容永远不进入规范化、
指纹或任何下游证据：

- 秘密、令牌、API 密钥、DSN 与凭据；
- 原始提示词与原始查询文本；
- 原始查询、SQL/MQL 与可执行文本；
- 原始结果行、文档或提供方响应；
- 原生对象（游标、连接、驱动值、SDK 客户端）；
- 未获批准的租户/主体标识符与客户端声明。

这种过滤是**安全过滤器，而非有损优化**：包含被排除材料的输入会被拒绝
或消毒，且所得身份对被排除内容不透露任何信息。

## 如何（How）

### 规范化（Canonicalization）

哈希之前，每个载荷都会被归一化为一种规范形式，该形式在映射键插入顺序
变化时保持稳定：

1. **映射**按键（字符串形式）递归排序。
2. **集合/frozenset**按其规范 JSON 渲染排序。
3. **列表/元组**保持顺序。
4. **日期时间**变为 ISO-8601 字符串。
5. 标量直接通过；不支持的值变为字符串。

规范 JSON 渲染使用 `sort_keys=True` 与紧凑分隔符，因此仅键顺序不同的
两个载荷会哈希为同一身份。

### 安全的键顺序规范化示例

这两个等价载荷——除映射键插入顺序外完全相同——产生**相同**的指纹：

```python
from nl2data_core.canonical import canonical_json, sha256_fingerprint

first = {"view_id": "v1", "fields": ["order_id", "amount"]}
second = {"fields": ["order_id", "amount"], "view_id": "v1"}

assert canonical_json(first) == canonical_json(second)
# '{"fields":["order_id","amount"],"view_id":"v1"}'

assert sha256_fingerprint(first) == sha256_fingerprint(second)
# 'sha256:77324b62e300929d30e7bd55cbcec7aa99d3d5f89ab995db4885027c2bc3dc69'
```

示例不含任何凭据、原始提示词、查询或结果——只有安全标识符。
`sha256:<小写十六进制>` 是系统中固定的指纹格式。

> 注意：上面的代码片段导入了 `nl2data_core`，因此**仅限贡献者**使用。
> 应用程序代码从不计算指纹；它以不透明有界引用的形式接收指纹。

### 指纹依赖与生命周期

```mermaid
flowchart TD
    SRC["Safe canonical inputs<br/>(secrets / raw payloads excluded)"] --> CAN["canonical_json<br/>order-independent"]
    CAN --> H["sha256 digest<br/>lowercase hex"]
    H --> FP["sha256:&lt;64 hex&gt;<br/>opaque identity"]

    FP --> BIND["binding: tenant scope, policy,<br/>view, bundle, authorization"]
    FP --> STORE["evidence: checkpoints, Memory,<br/>idempotency keys, telemetry"]
    FP --> CMP["comparison: drift, revalidation,<br/>compatibility checks"]

    BIND --> CHG{"input changes?"}
    STORE --> CHG
    CMP --> CHG
    CHG -- "yes" --> NEW["new fingerprint<br/>old references invalidated"]
    CHG -- "no" --> SAME["same fingerprint<br/>equivalent identity"]

    classDef deny fill:#fdecea,stroke:#c62828
```

**读者问题**：指纹从哪里来、如何传播、输入变化时会发生什么？

**文本等价描述**：安全规范输入被与顺序无关地渲染，并经 SHA-256 哈希为
固定格式的不透明身份。该身份用于绑定（授权工件、范围）、存储（证据、
幂等键、遥测）与比较（漂移、重新验证）。如果任何输入发生变化——视图版本、
策略、目录快照、租户范围——新指纹会使每个先前记录的引用失效；如果输入
等价，则身份不变。指纹绝不包含源载荷，也永远无法被逆向还原为源载荷。

### 传播

指纹作为唯一的载荷身份在系统中传播：

- 治理事实与策略范围携带租户范围指纹；
- 授权签发者将范围指纹与策略指纹绑定到授权工件中；验证者在执行前
  重新检查它们；
- 工作流检查点存储阶段名、状态、租户范围，以及配置/策略/目录/语义/
  工件指纹——绝不存原始材料；
- Memory 记录存储意图、IR、工件、策略与目录的指纹；
- 公共结果与句柄只暴露 `tenant_scope_fingerprint` 与有界的
  `evidence_fingerprints`。

### 不匹配处理

指纹不匹配意味着被引用的身份不再是当前上下文所期望的。系统
**fail-closed（安全失败）**：

| 不匹配 | 行为 |
| --- | --- |
| 召回的 Memory 引用过期/超出范围 | 在调用提供方之前 fail-closed 进入澄清 |
| 检查点处于不同的已解析视图之下 | 在任何适配器执行之前 `STALE_CHECKPOINT` |
| 存储的 IR 推导已变化 | 在 execute 门拒绝 |
| Bundle/快照指纹漂移 | `snapshot_stale` / `bundle_stale` / `catalog_stale` 拒绝 |
| 租户范围不匹配 | `TENANT_CONTEXT_REJECTED` |
| 配置快照与期望不同 | 受保护覆盖拒绝或不可重试的配置错误 |

**不存在明文恢复路径**：不匹配绝不提示或暴露原始载荷——它拒绝、
重新验证或回滚。

### 有意的版本与轮换变更

身份按构造即带版本——新版本就是带新指纹的新载荷：

- 新的 **Semantic Model Bundle 版本**是带新指纹的新 Bundle；激活会
  重新验证，并要求声明的依赖项具有匹配的指纹；
- 配置或快照中的 **schema 版本**是显式的；旧运行时对新部署
  fail-closed（`UNSUPPORTED_SCHEMA_VERSION`）——降级是部署决策，
  绝非自动回滚；
- **回滚**（Bundle、快照或视图）在同一策略下激活先前的工件——新的
  活跃身份会使回滚前产生的证据失效，过期检查点在任何适配器执行前
  被拒绝。

轮换之所以安全，正是因为指纹是确定性身份而非可变状态：切换版本是
比较问题，绝不是证据的重写。

## 下一步

- [治理与多租户](governance-and-tenancy.md) — 指纹如何绑定授权。
- [工作流状态](workflow-state.md) — 证据指纹如何在重启后持久化。
- [元数据生命周期](metadata-lifecycle.md) — 快照与 Bundle 指纹及漂移。
- [English source](evidence-and-fingerprints.md) — 英文原文（规范）。
