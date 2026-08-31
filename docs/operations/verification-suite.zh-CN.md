# 验证套件运维

> **本页为简体中文翻译**。规范语言为英文；如与
> [英文原文](verification-suite.md)冲突，以英文为准。

## 生产门禁

必须显式选择 `production-v1`，并在批准前附加准确计划。夹具应固定架构、种子、
时钟、时区、重置行为和设置指纹。必需的 Layer 2/3 用例必须启用并通过。
`failed`、`skipped`、`unavailable`、`timed_out`、`not_run`、缺少执行器能力或
缺少密钥解析都会阻止发布和生产激活。

若真实服务用例全部跳过，即使测试进程退出码为零，也不能视为验证成功。
PostgreSQL 和 MongoDB 配置受环境门控；升级发布前必须检查跳过原因和证据。

## 密钥与执行器

计划仅包含未解析的夹具/部署配置标识，不得包含 DSN、令牌、密码、物理名称、
SQL 或 MQL。宿主只能在执行器边界内临时解析连接材料。解析值不得写日志、进入
异常、缓存到证据或传递到 Admin DTO。

应记录执行器身份和能力指纹。运行器、执行器、能力、策略、计划、候选 Bundle、
清单、草稿修订或作用域任一变化，都会使旧证据失效并要求重新验证。

## 审计与检查

Admin `verify_draft` 无生命周期副作用，并要求 `ASSEMBLY_VERIFY`。
`get_verification_evidence` 和发布审计检查要求 `ASSEMBLY_AUDIT`。结果只包含有界
状态、计数、问题码、身份和指纹。持久 PostgreSQL 证据带校验和，加载时总会重新
验证其发布绑定。

## 故障排查

| 状态/代码 | 处理方式 |
| --- | --- |
| `capability_mismatch` | 配置满足计划能力指纹的执行器；不得静默删除要求 |
| `fixture_unavailable` / `unavailable` | 恢复夹具、服务或密钥解析器后重跑；不可用绝不是通过 |
| `timed_out` / `layer_deadline_exhausted` | 排查夹具或适配器，只调整经评审的有界截止时间 |
| `candidate_drift` | 针对当前候选重新构建 IR，并重新批准 |
| `verification_evidence_mismatch` | 丢弃旧证据，并验证当前修订及全部身份绑定 |
| `legacy_unverified` | 在显式策略下重新发布；不得为旧发布伪造证据 |
