## Context

`publish_assembly` currently performs meaningful Layer 1 work: authorization, revision and review checks, separation of duties, Bundle emission, core Bundle validation, accepted-manifest derivation, and core manifest-to-Bundle equivalence. It then invokes one required `ManifestBundleVerifier` callback returning only `valid` plus issues. `PublishVerificationSummary` records two booleans and a callback count. This is insufficient evidence for DDS-020's claim that smoke behavior and business semantic contracts passed, and a host callback can neither express per-case deadlines nor distinguish failure from unavailable/skipped execution.

The repository already has reusable governed execution pieces: `SemanticQueryIR`, fixture profiles, `EvaluationRunner`, compiler/evidence contracts, adapter capability fingerprints, structured errors, protected results, tenant/source scope, deployment binding secret resolution, and atomic in-memory/PostgreSQL publication. The Verification Suite should compose these boundaries rather than build a second query runtime.

## Goals / Non-Goals

**Goals:**

- Implement explicit Layer 1 structural, Layer 2 smoke, and Layer 3 semantic contract verification.
- Make production publication require non-empty Layer 2/3 plans and passing results; unavailable/skipped/not-run is never success.
- Bind plans and evidence to the exact approved revision, manifest, candidate Bundle, tenant/source scope, policy, runner, and executor identities.
- Keep plans and evidence outside semantic Bundle identity while invalidating lifecycle approval when plans change.
- Reuse governed execution and fixture infrastructure with bounded deadlines and cleanup.
- Persist only safe verification summaries/references atomically with publication.
- Provide transport-neutral Admin verification and evidence inspection.

**Non-Goals:**

- No CLI, TUI, UI, scheduler, distributed test farm, or hosted verification service.
- No arbitrary test code, Python callbacks as plan content, raw SQL/MQL assertions, regex, snapshots of unrestricted rows, or physical-name assertions.
- No performance/load testing or statistical tolerance framework.
- No secret-manager implementation; hosts provide bounded resolvers/executors.
- No automatic generation of smoke or semantic cases from data profiling in this slice.
- No change to Bundle semantic fingerprint rules.

## Decisions

### D1 — VerificationPlan is lifecycle content, not semantic Bundle content

Add `VerificationPlan` as an optional bounded member of `AssemblyDraft` under N6. Its canonical payload has `verification_version`, policy profile/version, Layer 2/3 cases, deadlines, and capability requirements; its fingerprint is deterministic with cases sorted by ID. It is omitted when unset for draft compatibility and never enters `SemanticModelBundle.canonical_payload()`.

Draft approval captures `approved_verification_plan_fingerprint` alongside the approved revision. Any plan edit uses normal draft mutation, advances `draft_revision`, clears approval, and requires review/approval again. Publish compares the frozen plan fingerprint to this binding before executing anything.

Alternative: store tests as semantic assertions. Rejected because tests and expected observations are release evidence, not query semantics, and would incorrectly change the Bundle fingerprint. Alternative: pass an unbound plan only at publish. Rejected because unreviewed release criteria could be swapped after approval.

### D2 — Policy profiles are explicit and versioned

Define `VerificationPolicy` with built-in identities:

- `compatibility-v1`: Layer 1 required; Layer 2/3 optional. This preserves trusted embedded/manual paths and existing callers but audit must label the result structural-only.
- `production-v1`: Layers 1, 2, and 3 required; Layer 2 and 3 each require at least one enabled case; every enabled required case must pass.

A host may construct a stricter bounded policy but cannot weaken the semantics of a built-in identity. There is no fallback from production to compatibility on missing capabilities or infrastructure.

Alternative: infer policy from deployment environment name. Rejected because environment strings are untrusted metadata and silent downgrade is unsafe.

### D3 — Layer 1 remains core-owned and runs before external work

Refactor existing publish checks into a `CoreStructuralVerificationRunner`: authorization remains outside suite execution, while revision/review/plan binding, Bundle validation, source/business identity, accepted-manifest derivation/equivalence, tenant/source scope, and calculated-field constraints produce Layer 1 evidence. Failure stops before secret resolution, fixture setup, adapter invocation, or catalog writes.

The existing `ManifestBundleVerifier` becomes a compatibility adapter or an additional external structural check, never the authority for manifest equivalence. Core evidence names every check by bounded code.

### D4 — Plans contain governed queries, not backend syntax

A Layer 2 smoke case carries:

- stable `case_id` and description,
- canonical `SemanticQueryIR` (or a separately versioned governed request that deterministically resolves before freeze),
- deployment/fixture profile reference,
- required adapter/executor capabilities,
- deadline,
- closed smoke assertions.

V1 uses canonical IR to keep verification deterministic and avoid model-provider variability. IR references are revalidated against a projection built from the candidate Bundle and execute through compiler, guard, governance, authorization, adapter, and result protection. Raw SQL/MQL and physical names are structurally absent.

Smoke assertions are `outcome`, `result_shape`, `row_count`, `scalar_equals`, `is_null`, and `error_code`. Expected scalars use an explicit tagged wire type (`null`, `bool`, `int`, `decimal`, `str`); decimal values are canonical strings, and bool/int subclass rules are enforced. No float enters the plan fingerprint domain.

Alternative: use SQL substring assertions from DDS-020 examples. Rejected because they violate backend neutrality, physical-name isolation, and artifact abstraction.

### D5 — Layer 3 is a closed evaluator over transient protected observations

Layer 3 cases carry canonical IR plus semantic contracts. The closed V1 operators are:

- exact protected result,
- scalar equality,
- row-count equality/range,
- aggregate total equality,
- mapping outcome equality,
- null behavior,
- structured error-code equality.

Contracts identify selections/semantic fields, never physical columns. Arbitrary expressions, functions, Python, SQL/MQL, regex, and callbacks are inexpressible. Unsupported types/operators fail plan validation.

Executors return a bounded transient `VerificationObservation` that may contain protected scalar rows needed for assertion evaluation. It is never persisted or included in API/audit DTOs. The runner reduces it immediately to statuses, counts, issue codes, and result/evidence fingerprints, then releases references before fixture cleanup.

Alternative: persist protected rows like evaluation reports. Rejected because publication audit does not need values and should minimize evidence sensitivity.

### D6 — Identical executions are shared across layers

The suite builds an execution key from candidate Bundle fingerprint, canonical IR fingerprint, deployment/fixture profile ID, tenant/source scope, and executor capability fingerprint. Layer 2 and Layer 3 cases with the same key share one execution observation, while each evaluates its own assertions independently. Scheduling order cannot change evidence identity: case/layer records are sorted and durations are excluded from evidence fingerprints.

This avoids double-running side-effect-free read queries and prevents Layer 2/3 from observing different fixture states. Queries remain read-only and bounded through existing IR/governance contracts.

### D7 — Execution is replaceable, bounded, and fail-closed

Introduce `VerificationExecutor` and `VerificationFixtureSession` protocols. Core provides a deterministic SQLite reference integration; SQL/PostgreSQL/Mongo hosts can adapt existing fixtures and adapters. The execution context carries frozen candidate, manifest, projection/view, tenant/source scope, policy, effective limits, deadline, and cancellation.

Every case, layer, and suite has bounded timeouts. Statuses are `passed`, `failed`, `skipped`, `unavailable`, `timed_out`, and `not_run`; only `passed` satisfies a required item. Exceptions are converted to controlled issue codes. Fixture reset/disposal always runs, and cleanup failure is recorded separately without masking a prior failure.

A missing secret resolver, adapter, capability, fixture, or service yields `unavailable`, never skipped-success. User-declared disabled optional cases are `skipped`; production policy rejects a disabled case if it is needed to meet layer minimums.

### D8 — Evidence is fingerprints and bounded facts only

Models:

- `VerificationCaseEvidence`: case/query IDs and fingerprints, status, assertion counts, result/evidence fingerprints, issue codes.
- `VerificationLayerEvidence`: layer ID/version/status, sorted cases, counts, fingerprint.
- `VerificationSuiteEvidence`: suite/policy/plan/runner identities, frozen draft ID/revision, candidate Bundle and manifest fingerprints, tenant/source scope fingerprints, executor identities, three layer records, fingerprint.

Durations may be included for operational display but are excluded from evidence fingerprints. Evidence carries no plan payload, IR payload, rows, expected/actual values, prompts, artifacts, SQL/MQL, physical names, deployment references, secrets, or exception messages.

Runner identity is a code constant. External executor identity and capability fingerprint are required whenever Layer 2/3 execute. One-sided or drifted identities invalidate evidence. Evidence from compatibility mode remains explicitly distinguishable and cannot satisfy production policy.

### D9 — Publish runs or validates evidence inside the atomic gate

`publish_assembly` accepts a `VerificationSuiteRunner`, selected policy, and execution context. After Layer 1 candidate/manifest construction, it runs Layer 2/3 and requires a passing suite before constructing audit and calling catalog publish. The catalog validates evidence binding again (defense in depth) and atomically stores the safe suite envelope/reference with Bundle, manifest, audit, and supersession metadata.

Precomputed evidence may be supplied by Admin verification to improve UX, but publish must validate all bindings and freshness. Since the approved draft is immutable, valid evidence can be reused only when every identity/fingerprint matches. A changed runner/executor policy can intentionally force re-verification without changing semantic Bundle identity.

Idempotent content reuse returns the evidence/audit attached to the existing publication; a new plan does not mutate an old publication. If policy requires the new plan to be recorded, the operation must create a new business publication decision or explicitly return an already-published/content-reused outcome with the existing evidence, never overwrite it.

### D10 — Admin verification is side-effect-free with respect to lifecycle

Add `ASSEMBLY_VERIFY` permission. `verify_draft` requires trusted auth, source/tenant scope, expected revision, policy profile, and configured verifier/executor. It may allocate/reset temporary fixtures and resolve deployment secrets ephemerally but does not mutate the draft or catalog. The DTO returns only suite/layer/case statuses, counts, issue codes, identities, and evidence fingerprints.

Publish consumes fresh or supplied bound evidence and remains the only operation that creates publication/audit records. Audit lookup includes the safe verification summary. Admin never exposes observations or expected values.

### D11 — Persist evidence as a separate versioned envelope

Add an immutable verification evidence record keyed by tenant scope, bundle ID, and Bundle fingerprint in a separate table/envelope. The publish audit stores the evidence fingerprint/reference and aggregate layer statuses; per-case records remain in the verification envelope. This avoids inflating audit rows and lets case schemas evolve independently. Bundle, manifest, verification evidence, audit, and supersession writes occur in the same publication transaction.

Schema migration is additive. Existing publications without suite evidence remain loadable but are classified `legacy_unverified` and cannot satisfy `production-v1`. No migration fabricates passing evidence.

## Risks / Trade-offs

- **Behavioral tests can read sensitive values transiently** → Bound scalar observations, reuse result protection, never persist values, and aggressively redact errors.
- **External service availability blocks production publish** → This is intentional fail-closed behavior; compatibility profile remains explicit for non-production trusted paths.
- **Test flakiness becomes release flakiness** → Require deterministic fixtures, fixed clock/timezone, exact bounded cases, runner/executor identities, and no probabilistic providers in V1.
- **Verification plans grow large** → Bound layers, cases, assertions, rows inspected, scalar sizes, deadlines, and total suite execution.
- **Layer 2 and Layer 3 duplicate work** → Share observations by a deterministic execution key.
- **Plan edits outside semantic fingerprint surprise users** → Bind plan to lifecycle approval and audit; document that release criteria affect publish eligibility, not semantic identity.
- **Legacy publications appear verified** → Explicit `legacy_unverified` status and policy checks; never synthesize results.
- **Host executor could lie** → Anchor executor identity/capabilities, use controlled fixtures where possible, and treat it as a replaceable trust boundary visible in audit.

## Migration Plan

1. Add verification plan/policy/assertion/evidence models and compatibility defaults without changing existing publication behavior.
2. Add Layer 1 runner and migrate current structural/manifest checks into evidence-producing checks.
3. Add transient observation executor protocol, SQLite reference executor, shared execution cache, and Layer 2/3 evaluators.
4. Bind optional plans to assembly draft revision/approval; enable `compatibility-v1` for legacy callers.
5. Integrate suite evidence into publish and Admin; add production policy enforcement.
6. Add PostgreSQL evidence persistence/reload migration and idempotency/concurrency coverage.
7. Add dual-adapter conformance fixtures, bilingual docs, and production runbook guidance.
8. After adoption evidence, consider making a non-empty production plan the default for all non-manual publication paths.

Rollback disables production policy selection and returns callers to explicit compatibility verification. Additive plan/evidence fields remain readable and omitted when unset; published Bundle semantic fingerprints and immutable artifacts are unchanged.

## Open Questions

- Whether a later evaluation change should consume Verification Suite evidence directly for release dashboards.
