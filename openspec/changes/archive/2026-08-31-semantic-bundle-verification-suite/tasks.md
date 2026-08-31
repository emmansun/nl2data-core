## 1. Verification Contracts and Policy

- [x] 1.1 Add `src/nl2data_core/verification/` with frozen versioned enums/models for suite/layer/case status, tagged expected scalars, smoke assertions, semantic contract assertions, query cases, and bounded deadlines/capability requirements.
- [x] 1.2 Implement canonical `VerificationPlan` payload/fingerprint with case-order independence, unique stable IDs, JSON-wire safety, no floats in expected scalar identity, and strict rejection of SQL/MQL/code/physical names/native values/credentials.
- [x] 1.3 Add immutable evidence models for case, layer, and suite identities/status/counts/fingerprints with durations excluded from evidence identity and N6 omission for absent optional references.
- [x] 1.4 Define immutable built-in `compatibility-v1` and `production-v1` policies plus bounded stricter host policy validation; prohibit silent profile downgrade and built-in identity weakening.
- [x] 1.5 Add model/policy tests for all bounds, tagged scalar discipline, duplicate IDs, unsupported operators, unsafe material, canonical ordering, fingerprint stability, production layer minima, compatibility labeling, and evidence redaction.

## 2. Assembly Plan Binding

- [x] 2.1 Add optional `verification_plan` and approved-plan fingerprint binding to `AssemblyDraft` with N6 omit-when-unset compatibility and no contribution to Bundle semantic fingerprints.
- [x] 2.2 Extend draft mutation/transition rules so plan changes advance revision and invalidate approval, and approval captures the exact plan fingerprint without exposing a Bundle fingerprint.
- [x] 2.3 Validate publish-time equality among stored authoritative draft, expected revision, approved plan binding, and frozen plan; reject one-sided/mismatched plans before external work.
- [x] 2.4 Add lifecycle tests for plan add/edit/remove/enable/disable/case/deadline/profile changes, unchanged-plan approval preservation where valid, approved-content freeze, stale CAS, and semantic fingerprint independence.

## 3. Layer 1 Structural Verification

- [x] 3.1 Refactor current publish structural checks into a core Layer 1 runner covering draft/review/plan binding, Bundle validation, source/business identity, manifest derivation and equivalence, scope/compatibility, and calculated-field invariants.
- [x] 3.2 Produce deterministic Layer 1 case/layer evidence with bounded check codes and runner identity, without fixture, secret, adapter, or catalog access.
- [x] 3.3 Keep `ManifestBundleVerifier` as an explicit compatibility/additional-check adapter only; ensure a host success result cannot override any core mismatch.
- [x] 3.4 Add contract/security tests proving every Layer 1 mismatch stops before Layer 2/3 and catalog writes, host callbacks cannot grant equivalence, and structural issue serialization is safe.

## 4. Governed Observation Execution

- [x] 4.1 Define replaceable `VerificationExecutor`, fixture/session, cancellation, and execution-context protocols carrying frozen candidate/manifest/view/scope/policy/limits/deadline and required executor identity/capability fingerprint.
- [x] 4.2 Implement a deterministic SQLite reference executor by composing existing view resolution, IR validation, compilation, guard, governance, authorization, adapter execution, result protection, and fixture lifecycle boundaries.
- [x] 4.3 Implement bounded transient `VerificationObservation` handling for protected scalars needed by assertions; ensure observations and values are released after reduction and cannot enter persisted/API evidence.
- [x] 4.4 Add deterministic execution-key caching so identical Layer 2/3 query/profile/scope/executor inputs execute once, with sorted result evaluation independent of scheduling.
- [x] 4.5 Add executor tests for setup/reset/disposal, read-only bounded IR enforcement, scope/candidate binding, capability mismatch, timeout/cancellation, unavailable services/resolvers, cleanup failure precedence, shared execution, and value non-persistence.

## 5. Layer 2 Smoke Verification

- [x] 5.1 Implement Layer 2 evaluation for `outcome`, `result_shape`, `row_count`, tagged `scalar_equals`, `is_null`, and `error_code` assertions over transient observations.
- [x] 5.2 Revalidate every smoke IR against the candidate Bundle-derived projection and reject raw backend syntax, physical identifiers, unbounded limits, stale fingerprints, unsupported capabilities, and result assertions beyond bounds.
- [x] 5.3 Map assertion mismatches, execution failures, missing capabilities/fixtures/secrets, timeout, cancellation, skip, and unavailable states to bounded fail-closed case/layer outcomes.
- [x] 5.4 Add Layer 2 tests for passing results, each assertion kind, mismatches, structured failures, all non-pass statuses, candidate drift, true division/null behavior, and evidence containing fingerprints but no values.

## 6. Layer 3 Semantic Contract Verification

- [x] 6.1 Implement the closed Layer 3 evaluator for exact protected result, scalar equality, row-count equality/range, aggregate total, mapping outcome, null behavior, and structured error-code contracts.
- [x] 6.2 Enforce semantic selection/field references, tagged expected values, deterministic type checking, and structural impossibility of arbitrary expressions, callbacks, regex, Python, SQL/MQL, and physical names.
- [x] 6.3 Reuse compatible Layer 2 observations through the deterministic execution cache while evaluating Layer 3 contracts independently.
- [x] 6.4 Add semantic drift tests for mapping edits, aggregation changes, calculated-field expressions, zero-division/null policy, result type mismatch, unsupported operator, and evidence-safe failure records.

## 7. Suite Orchestration and Identity Guards

- [x] 7.1 Implement suite orchestration in fixed layer order with bounded suite/layer/case deadlines, cancellation propagation, required-layer policy evaluation, stable sorting, and only `passed` satisfying requirements.
- [x] 7.2 Define the core runner identity/version and symmetric guards for plan, policy, draft revision, Bundle, manifest, tenant/source, runner, executor, and capability fingerprints; reject one-sided or drifted identities.
- [x] 7.3 Implement explicit compatibility handling for legacy/no-plan callers and `legacy_unverified` publications without allowing them to satisfy `production-v1`.
- [x] 7.4 Add orchestration tests for Layer 1 short-circuit, shared execution, deterministic fingerprints across timing/scheduling, policy downgrade rejection, stale precomputed evidence, identity drift, skipped/unavailable/not-run behavior, and total deadline exhaustion.

## 8. Atomic Publish and Audit Integration

- [x] 8.1 Integrate the suite into `publish_assembly`: freeze candidate/plan, run or validate bound evidence, require policy pass, then construct audit and call catalog; preserve approved draft on every failure.
- [x] 8.2 Expand `PublishVerificationSummary` with suite version, policy/plan/runner identities, layer statuses/counts, and verification evidence fingerprint/reference while retaining bounded legacy decoding.
- [x] 8.3 Extend in-memory catalog publication validation and records to atomically bind Bundle, manifest, verification evidence, audit, and supersession metadata; idempotent reuse returns original evidence/audit and never overwrites it.
- [x] 8.4 Add publish contract tests for every layer failure/status, missing/forged/stale evidence, plan changes, callback exceptions, atomic rollback, idempotent reuse, concurrent publish, semantic fingerprint stability, and no external work before Layer 1 pass.

## 9. Durable Verification Evidence

- [x] 9.1 Add an additive PostgreSQL schema migration and artifact kind for immutable verification evidence keyed by tenant scope, bundle ID, and Bundle fingerprint with safe versioned envelopes.
- [x] 9.2 Persist Bundle, manifest, verification evidence, audit reference/summary, version record, and supersession edge in one transaction after revalidating every evidence binding under the publication-series lock.
- [x] 9.3 Implement verification evidence lookup/reload with envelope checksum/version and plan/policy/runner/executor/draft/manifest/Bundle/scope binding validation; classify old publications as `legacy_unverified`.
- [x] 9.4 Extend the fake PostgreSQL driver and contract tests for exact SQL templates, failure injection at each write, transaction rollback, tenant isolation, restart reload, tamper rejection, identical concurrent reuse, and different-content serialization.
- [x] 9.5 Add real PostgreSQL integration tests for evidence restart survival, atomic failure, concurrent first publish, tenant isolation, and activation rejection of missing/tampered production evidence using the existing skip-gated profile.

## 10. Admin Verification Surface

- [x] 10.1 Add `ASSEMBLY_VERIFY` permission and bounded command/result DTOs for draft verification, suite/layer/case summaries, policy selection, evidence references, and published verification inspection.
- [x] 10.2 Add side-effect-free `verify_draft` with trusted tenant/source scope, expected revision, configured runner/executor capabilities, safe error normalization, and no draft/catalog mutation.
- [x] 10.3 Update `publish_draft` to consume configured suite policy and fresh/bound evidence while delegating all verification and atomicity decisions to core/catalog.
- [x] 10.4 Add verification evidence lookup to audit/version detail, capability metadata, service schema, protocols, and package documentation without exposing observations, expected/actual values, queries, deployment references, or secrets.
- [x] 10.5 Add Admin contract/security tests for permission/role/scope denial, stale revision, all suite statuses, missing executor/resolver, side-effect freedom, bounded DTOs, redaction, publish blocking, evidence inspection, and idempotent response identity.

## 11. Conformance, Documentation, and Quality Gates

- [x] 11.1 Add controlled SQLite and skip-gated PostgreSQL/Mongo verification fixtures that run the same smoke/semantic cases and assert exact protected semantic equivalence and equivalent structured failures.
- [x] 11.2 Add English and Chinese architecture/operations guides for plan authoring, policy profiles, layer semantics, deterministic fixture requirements, executor trust, unavailable/skip handling, secrets, audit evidence, troubleshooting, and production gates.
- [x] 11.3 Update capability/error-code/compatibility/admin references and deterministic demo to show a three-layer passing publication plus one safely blocked semantic drift case.
- [x] 11.4 Coordinate with `semantic-assembly-yaml-authoring`: after that capability is synchronized, add or amend its authoring schema to express verification plans without lifecycle evidence or backend syntax; until then keep plans programmatic and document the sequencing dependency.
- [x] 11.5 Run focused model, lifecycle, Layer 1/2/3, orchestration, publish, security, Admin, catalog, conformance, and real-service integration tests.
- [x] 11.6 Run full pytest, Ruff, Mypy, `scripts/check_docs.py`, and `openspec validate semantic-bundle-verification-suite --type change`.
- [x] 11.7 Verify legacy no-plan/manual paths remain explicit compatibility behavior, existing published Bundle fingerprints stay byte-identical, and no verification-only content enters descriptor/snapshot/Bundle/evidence semantic identity.
