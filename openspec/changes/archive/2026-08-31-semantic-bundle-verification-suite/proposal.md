## Why

Semantic assembly publication currently proves structural validity and manifest equivalence, but delegates all behavioral verification to one host boolean callback. A governed three-layer Verification Suite is required before the project can claim that a published Bundle was checked against executable smoke behavior and business semantic contracts rather than merely being well formed.

## What Changes

- Add a versioned bounded `VerificationPlan` and deterministic runner covering Layer 1 structural validation, Layer 2 smoke cases, and Layer 3 semantic contract cases.
- Make Layer 1 core-owned and mandatory for every publish; define policy profiles so production publication requires non-empty Layer 2 and Layer 3 plans with every required case passing.
- Define smoke cases as governed canonical IR executions against controlled deployment/fixture profiles with protected expected result shape, row-count, scalar, or evidence assertions; raw SQL/MQL and backend text are not test inputs.
- Define a closed semantic-contract assertion DSL for exact protected results and governed relations such as field equality, aggregation totals, mapping outcomes, null behavior, and structured error codes; arbitrary Python, SQL, expressions, callbacks, and unrestricted values are rejected.
- Bind verification plans to the approved assembly lifecycle: plan changes invalidate approval, plans remain outside the Bundle semantic fingerprint, and publish freezes the plan with the approved semantic content.
- Replace the single host verification boolean with per-layer/per-case bounded outcomes, deadlines, deterministic evidence fingerprints, and fail-closed handling of `failed`, `skipped`, `unavailable`, timeout, exception, or missing required execution.
- Persist a safe verification evidence summary and plan/runner identities atomically with the Bundle manifest and publish audit; never persist rows, credentials, physical names, raw queries, or backend exceptions.
- Extend the Admin service with authorized side-effect-free verification and publish-result inspection operations using bounded DTOs; verification cannot approve or publish a draft.
- Reuse existing fixture, evaluation, compiler, governance, and adapter boundaries where possible; no CLI/TUI/UI is introduced in this change.

## Capabilities

### New Capabilities
- `semantic-bundle-verification-suite`: Versioned three-layer verification plans, bounded case models and assertion DSL, deterministic execution, policy profiles, protected evidence, and pass/fail semantics.

### Modified Capabilities
- `semantic-assembly-lifecycle`: Bind verification plans to draft approval and require the configured suite to pass on the frozen approved revision before atomic publish.
- `semantic-model-bundles`: Strengthen publication eligibility so required verification layers and evidence identities are validated without entering the semantic fingerprint domain.
- `semantic-admin-api`: Expose authorized verification and verification-evidence inspection operations with bounded safe results and no lifecycle bypass.
- `durable-semantic-catalog`: Persist verification summaries and identities atomically in publish audit evidence and preserve them across reload/idempotent reuse.

## Impact

- **Core assembly**: new verification package, plan/result models, layer runners, assertion evaluators, policy profiles, and publish-gate integration.
- **Execution integration**: controlled reuse of `EvaluationRunner`, fixture profiles, `SemanticQueryIR`, compilation/governance, and adapters through replaceable executor ports.
- **Bundle/audit**: expand `PublishVerificationSummary` from booleans/callback count to versioned per-layer evidence references while preserving secret-safe publication records.
- **Admin package**: add verification commands, safe result DTOs, capability metadata, and audit inspection fields.
- **Persistence**: update PostgreSQL audit envelopes/schema compatibility as needed; verification evidence remains bounded metadata, not raw case output.
- **Tests/docs**: layer-specific unit tests, fail-closed publication contracts, dual-adapter smoke/semantic fixtures, persistence/reload tests, security tests, and bilingual operating guidance.
