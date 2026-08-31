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

`spec` 还可包含字段、嵌套关系和计算字段、度量、粒度、源引用、兼容性和
部署绑定。完整示例见
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