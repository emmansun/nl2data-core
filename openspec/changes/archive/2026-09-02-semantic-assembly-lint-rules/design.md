## Context

The semantic control plane already separates authoring validation, assembly lifecycle review, publish-time verification, audit records, and immutable Bundle publication. Verification Suite protects frozen publish candidates, while authoring validation rejects structurally unsafe or invalid input before lowering.

There is still a quality gap between those boundaries: valid assembly content can be ambiguous, weakly described, missing production governance hints, or under-prepared for verification. Those issues should be visible to authors and reviewers before approval, but they should not be represented as publish evidence or semantic Bundle identity.

## Goals / Non-Goals

**Goals:**

- Add a deterministic lint engine for validated authoring models and lifecycle drafts.
- Emit stable, bounded diagnostics with rule IDs, severity, profile, target path, source location when available, safe message text, and optional safe references.
- Support compatibility, recommended, and production profiles so hosts can choose advisory or blocking behavior.
- Keep lint separate from model validation, review decisions, verification evidence, publish audit authority, and Bundle fingerprints.
- Expose lint through core APIs and Admin service DTO/schema surfaces without requiring transport dependencies.

**Non-Goals:**

- No LLM-based critique, automatic rewrite, or semantic suggestion generation in this change.
- No raw database access, data profiling, query execution, or verification-case execution.
- No change to publish-time Verification Suite pass/fail semantics.
- No automatic approval, rejection, lifecycle mutation, or audit event creation from lint results.

## Decisions

1. **Lint operates only on already parsed/validated semantic objects.**
   - The engine accepts an authoring model after safe YAML parsing/model validation, or an `AssemblyDraft` after lifecycle loading.
   - Rationale: parser/schema failures already have diagnostics; lint should focus on quality and governance readiness.
   - Alternative considered: lint raw YAML directly. Rejected because it would duplicate safe parser behavior and increase secret-leak risk.

2. **Rules are deterministic and profile gated.**
   - Built-in rules are pure functions over model content and configuration. Each rule declares the profiles where it runs and the severity emitted for each profile.
   - Rationale: CI and Admin UI need repeatable output, while production hosts need stricter gates than compatibility workflows.
   - Alternative considered: let callers mutate severities arbitrarily per invocation. Rejected for v1 because it weakens cross-environment repeatability.

3. **Diagnostics are stable contracts.**
   - Codes use a stable `SAL###` namespace. Result ordering is deterministic by severity, code, target path, source location, and safe reference.
   - Rationale: tests, CI baselines, and authoring UI should not churn across equivalent input ordering.
   - Alternative considered: return free-form findings only. Rejected because it cannot support reliable blocking policy.

4. **Lint errors may block host workflow, but they do not become lifecycle authority.**
   - The result includes `has_errors` and `blocking` indicators for the selected profile. Hosts may gate CI, submit-for-review, approval, or publish UX on those values.
   - Core publish remains governed by existing lifecycle and Verification Suite checks; lint does not produce verification evidence or audit records.
   - Alternative considered: require production lint pass inside publish. Deferred to avoid coupling authoring quality policy to immutable publication semantics before rule maturity.

5. **Admin lint is side-effect-free and permission scoped.**
   - `lint_authoring` validates authoring content and then lints without persistence. `lint_draft` loads an existing draft by tenant/source scope and expected revision, then lints it without mutation.
   - Rationale: host tools need both pre-import and draft-review feedback, but neither operation should change lifecycle state.

## Risks / Trade-offs

- [Risk] Lint rules drift into subjective writing style checks. -> Keep v1 rules deterministic, bounded, and tied to ambiguity, governance, reference consistency, or verification readiness.
- [Risk] Lint duplicates validation or Verification Suite. -> Define rule ownership clearly: validation rejects malformed/unsafe content; lint reports quality/readiness issues; Verification Suite checks frozen publish candidates and executable semantic contracts.
- [Risk] Production profile becomes too strict for early adopters. -> Provide compatibility and recommended profiles, and document production errors as host-enforceable policy rather than hard publish semantics in v1.
- [Risk] Diagnostics leak sensitive content from names, descriptions, or value semantics. -> Messages use bounded safe summaries and paths; secret-like scalar values are redacted or omitted using existing safe-content helpers.
- [Risk] Source locations are unavailable for lifecycle drafts. -> Preserve authoring source marks when present, but require diagnostics to function with path-only targets.

## Migration Plan

- Add models, engine, rules, public exports, and tests without changing existing validation or publish behavior.
- Add Admin DTO/schema/service operations behind existing admin package boundaries.
- Document lint profiles and rule codes. Hosts can adopt lint incrementally, starting with advisory recommended-profile checks.
- Rollback is additive: removing host calls to the lint operation restores previous behavior because lifecycle and verification semantics remain unchanged.

## Open Questions

- Should a later change require production-profile lint to pass before `approve_draft`, before `publish_draft`, or only in host CI?
- Should hosts be allowed to register custom deterministic rules, or should v1 remain built-in only until the diagnostic contract stabilizes?