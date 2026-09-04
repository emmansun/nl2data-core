# 语义程序集 YAML 编写

> **本页为简体中文翻译**。规范语言为英文；如与
> [英文原文](semantic-assembly-authoring.md)冲突，以英文原文为准。

语义编写 YAML 是面向模型所有者的纯语义输入格式，不是内部
`AssemblyDraft` 信封。它不能携带评审、批准、发布、激活、审计、来源、
修订号、断言 ID 或已解析的连接信息。

## 文档结构

每个文档必须声明：

- `apiVersion: nl2data.io/semantic-assembly-authoring/v1alpha1`
- `kind: SemanticAssembly`
- `metadata.bundleId` 和 `metadata.modelVersion`
- `spec.source.sourceId` 以及至少一个实体

`spec` 还可包含字段、嵌套关系和计算字段、度量、粒度、策略模板、源引用、
兼容性和部署绑定。完整示例见
[演示文档](../../demo/authoring/sales-semantic-assembly.yaml)。标识符长度为
1-128 个字符，只允许字母、数字、`_`、`-` 和 `.`。描述最多 1,024 个
字符，并拒绝凭据、连接信息和可执行查询内容。各类语义标识符在描述符中
必须全局唯一。

## YAML 子集与边界

加载器只接受映射、序列、字符串、null、布尔值、整数和有限浮点数。标量
解析遵循 JSON 规则：只有小写 `true`、`false` 和 `null` 会转换类型；
类似时间戳的值以及 `yes`、`on` 等 YAML 1.1 单词仍为字符串。允许注释和
有界别名，但别名会在语义校验前展开。

默认限制为：UTF-8 输入 1 MiB、65,536 个解析事件、32,768 个节点、64 层
嵌套、每个标量 4,096 个字符、每个集合 16,384 项、128 个别名，以及
65,536 次展开节点访问。对象构造前会拒绝重复键、非字符串键、合并键、
自定义或对象标签、循环别名、过度别名展开、不支持的标量标签和非有限
数字。不支持 include、插值、模板、宏或外部 I/O。

## 引用与计算字段

关系目标必须存在；`sourceFields` 必须属于当前实体，`targetFields` 必须
属于目标实体，且两边数量相同。度量字段、粒度实体和粒度属性必须唯一
解析。源引用和部署绑定必须匹配 `spec.source.sourceId`。

计算字段使用受治理的 `field`、`const`、`add`、`sub`、`mul`、`div`
表达式树。`requires` 必须与字段叶节点完全一致；系统校验输出类型，禁止
计算字段组合，并禁止计算字段引用标记为 `pii` 的字段。不接受 SQL 或其他
可执行表达式文本。

部署绑定只能使用 `env:`、`vault:` 和 `file:` 引用。内联端点、凭据、令牌
和已解析值会被拒绝，也不会出现在诊断中。

## 策略模板

有界的 `spec.policies` 段以**策略模板**引用加类型化参数的形式声明治理
策略意图。注册表是封闭代码，只含四个模板；未知模板名、未知或缺失参数、
错误取值类型和越界列表都会在创建草稿前以带源位置的诊断失败关闭。每个
声明最多 8 个参数，每个文档最多 64 个声明。该段绝不接受原始策略载荷、
指纹、生命周期状态、批准绑定、凭据、物理名称或非标量值。

| 模板 | 参数（全部必填） | 标识目标 |
| --- | --- | --- |
| `tenant-isolation` | `entity`、`field`、`claim`（标识符） | 实体 + 字段 |
| `row-restriction` | `entity`、`field`、`allowed_values`（有界标量列表，1-256） | 实体 + 字段 |
| `purpose-gating` | `purposes`（标识符列表，1-16）、`effect`（`allow` \| `deny`） | purposes（排序后） |
| `field-masking` | `fields`（entity.field 引用列表，1-64）、`replacement`（有界非空字符串） | fields（排序后） |

实体、字段和 entity.field 参数必须解析到已声明的实体和字段。展开后的
策略标识在文档内必须唯一：展开到同一标识的两个声明视为冲突，而不是
静默覆盖。

策略模板在评审前的降低阶段展开为普通的 pending **策略断言**。展开标识
由模板名和标识目标派生（例如
`tenant-isolation.customers.tenant_id`）；`claim`、`allowed_values`、
`effect`、`replacement` 等取值参数只改变断言载荷，不改变标识。当渲染
目标超出标识符长度上限时，确定性的摘要回退保证标识仍然合法。展开之后
模板形式即消失：草稿只携带遵循既有评审、批准、验证和发布网关的标准
pending 策略断言，规范载荷只包含已解析的策略语义（`policy_kind` 加
类型化参数，绝无 `template` 引用），指纹计算不依赖模板声明。导出按
展开标识排序往返声明，与文档呈现方式无关。

命名说明：**策略模板**是编写语法糖；**策略断言**是展开后的草稿与包
内容；**验证策略配置**（`verificationPlan.policyProfile`）仍属验证配置，
与治理策略断言无关。

展开的策略描述的是以断言形式评审的治理意图。它们是用于评审、差异比较
和审计的内容单元，而不是新的运行时决策引擎：查询时强制仍来自宿主
`PolicyScope`，将策略断言绑定到运行时强制是刻意推迟的后续变更。

## Builder API

程序化宿主可以用流式的 `SemanticAssemblyBuilder` 在代码中构造与 YAML
完全相同的编写文档，而不必生成 YAML 文本。Builder 的每次调用构造的都是
YAML 加载器产生的同一批 pydantic 模型，因此所有边界、安全内容规则、
标识符模式和禁止键校验都在构造调用处生效——不存在同一文档从一个入口
通过而从另一个入口失败的分叉。Builder 文档与等价 YAML 文档产生完全相同
的校验摘要、断言标识与载荷哈希，以及逐字节相同的导出。

```python
from nl2data_core.assembly.authoring import SemanticAssemblyBuilder

builder = SemanticAssemblyBuilder("sales", "1.0.0", "Sales semantic model")
builder.source("warehouse")
(
    builder.entity("orders", "Orders")
    .field("amount", "Amount", "int", allowed_aggregations=("sum", "avg"))
    .field("customer_id", "Customer", "int")
    .relationship(
        "orders_customers",
        "customers",
        ("customer_id",),
        ("tenant_id",),
        "Order customer",
    )
    .done()
)
builder.measure("revenue", "amount", "Revenue", aggregation="sum")
builder.policy("tenant-isolation", entity="orders", field="customer_id", claim="tenant_id")
builder.deployment_binding("prod", "production", "warehouse", "env:NL2DATA_DEMO_DSN")
document = builder.build()
```

结果进入完全相同的管线：`validate_authoring(document)`、
`lower_authoring(document, draft_id=..., author_reference=...)` 与
`export_authoring(document)`。

要点：

- 计算字段只接受受治理的 `ExprNode` 表达式树，不接受表达式字符串。
- `verification_plan` 接受 `AuthoringVerificationPlan` 实例或 JSON 兼容映射，
  并经过与 YAML 相同的规范化模型构造：接受 camelCase 别名，拒绝生命周期键
  （`fingerprint`、`status`、`evidence`、运行器/执行器身份）。
- `compatibility` 接受 `BundleCompatibility` 实例或按字段展开的参数。
- Builder 接口刻意偏离 DDS-020 §9.2 草图：不提供 `table=`、`column=` 或
  `dsn=` 参数。编写层只承载引用——部署绑定使用 `env:`、`vault:` 或
  `file:` 引用形式的 `connection_reference`——并且 Builder 不暴露生命周期
  状态、评审或批准绑定、计算指纹或凭据。
- Builder 自身不做排序，也不在结构误用检查之外添加校验；基于标识的排序
  仍由降低和导出负责。

构造失败和误用都会抛出带受限消息与编写路径（例如
`$.spec.entities[0].fields[0]`）的 `AuthoringBuilderError`。消息绝不回显
被拒绝的值；程序化输入没有行列号，因此也没有源位置标记。误用——在未
打开的实体作用域外调用实体级方法、`done()` 或 `build()` 之后继续调用、
或实体作用域未关闭时调用 `build()`——同样失败关闭。

## 诊断与生命周期

诊断包含稳定代码、规范化 `$` 路径、可选的一基行列号和受控消息。最多
返回 100 个问题，`issue_count` 和 `truncated` 表示省略情况；拒绝值以及
PyYAML/Pydantic 原始异常永不返回。

`SemanticAssemblyAuthoringLoader` 只解析和校验，不持久化。
`lower_authoring` 从可信宿主接收 `draft_id` 和 `author_reference`，派生断言
ID，并创建修订号为零、来源为 manual、所有断言为 pending 的草稿。评审、
批准、发布和激活仍必须经过现有生命周期。

可选 Admin 服务的 `validate_authoring` 需要 `bundle:validate` 权限；
`import_authoring` 需要 `assembly:write` 权限和 Author 角色。两者都校验源
范围。校验不会访问草稿存储，导入只通过 `create_draft` 持久化。

## 导出保证

导出使用确定性的块式 YAML，按标识符排序集合，禁用别名，并显式引用
字符串。解析、导出、再解析会保持规范化语义和断言载荷哈希。导出始终
省略生命周期与密钥数据；已评审、已修改或无法无损表示的草稿会被拒绝，
不会降级导出。

## 拒绝的功能

`verificationPlan` 可包含策略/版本、有界截止时间、规范 Semantic IR、夹具配置标识、
能力要求、冒烟断言和语义契约。它拒绝调用方提供的指纹、批准绑定、状态、证据、
运行器/执行器身份、SQL/MQL、物理名称和凭据。降低过程只把计划附加到修订号为零的
草稿；后续计划修改仍必须遵循正常的修订和重新批准规则。

架构拒绝未知成员以及所有生命周期字段，包括断言 ID、来源、评审状态和
绑定、草稿修订号、批准者或发布者身份、审计记录、语义指纹、激活与替代
状态、可执行查询文本和已解析凭据。

## 后续步骤

- 运行 `python demo/run/demo_deterministic.py`，无需外部服务即可校验、导入、
  评审、批准、发布并激活完整示例。
- 诊断和导入失败请参阅[故障排查](../operations/troubleshooting.md)。