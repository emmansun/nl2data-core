## 1. Characterization and Architecture Baseline

- [x] 1.1 Add characterization tests that pin current public imports, Admin method signatures and generated DTO schema, AssemblyDraft/Bundle/manifest/verification/audit wire payloads, error codes, and all semantic/evidence fingerprints touched by this refactor.
- [x] 1.2 Add a control-plane architecture manifest describing canonical contract owners, approved module layers/import edges, compatibility re-exports, optional package direction, hotspot budgets, and the explicit generated-file/duplicate allowlists.
- [x] 1.3 Implement lightweight AST/source architecture checks for prohibited imports/cycles, exact duplicate Python modules, canonical public exports, complete port declarations, physical-line budgets, and cross-domain import ratchets.
- [x] 1.4 Capture and document before metrics for assembly, verification, publication, Admin, PostgreSQL store/fake, focused tests, declaration count, and cross-domain imports; pin the post-refactor target budgets from design D9.
- [x] 1.5 Run the full baseline suite and preserve its results as migration evidence before moving implementation code.

## 2. Fix Historical Verification Evidence

- [x] 2.1 Add a failing regression: publish a verified draft, reopen/edit or replace that draft and verification plan, restart the PostgreSQL catalog, and load the original publication evidence successfully.
- [x] 2.2 Add frozen versioned `FrozenReleaseBinding` contract and fingerprint covering approved draft ID/revision, approved plan, Bundle/manifest, scope, policy, runner, and executor identities without mutable or sensitive payloads.
- [x] 2.3 Persist the frozen binding in the verification-evidence publication envelope and link its fingerprint from the publish audit; validate all aggregate cross-links before transaction writes.
- [x] 2.4 Change in-memory and PostgreSQL historical evidence lookup to validate only immutable Bundle/manifest/evidence/audit/frozen-binding records and never read the current draft store/table.
- [x] 2.5 Add explicit legacy evidence decoding/classification for records without a frozen binding; prohibit deriving a production-valid binding from the current draft.
- [x] 2.6 Add contract, restart, tenant-isolation, tamper, failure-injection, and real-PostgreSQL skip-gated tests for frozen bindings and historical evidence after draft evolution.

## 3. Canonical Contracts and Dependency Direction

- [x] 3.1 Remove the byte-identical `verification/runner.py` implementation; use `verification.models` as canonical owner and retain only a temporary logic-free re-export if an import compatibility test requires it.
- [x] 3.2 Introduce the smallest acyclic publication/control-plane package chosen in design implementation, with direct-module imports and no package-initializer side effects closing cycles.
- [x] 3.3 Move publication aggregate/request/context/result value objects and publication ports to their canonical owners; update imports without changing serialized models or public exports.
- [x] 3.4 Remove catalog protocol dependencies on `AssemblyDraft`; catalog boundaries accept immutable publication aggregate/release contracts only.
- [x] 3.5 Consolidate identifier/fingerprint/reference/issue-code constrained helpers only where characterization proves identical behavior; preserve messages and canonical payloads.
- [x] 3.6 Enable architecture DAG, duplicate-owner, and public-export tests; delete temporary compatibility shims once no supported import depends on them.

## 4. Complete Typed Ports

- [x] 4.1 Define complete capability-oriented ports for metadata catalog/discovery, authoring, draft lifecycle storage, lifecycle authorization, Bundle emission, verification context/execution, publication aggregate storage, published lookup/evidence/audit, and activation/rollback/state changes.
- [x] 4.2 Replace Admin dependency helper `Any` returns and `getattr` discovery with typed optional port resolution and explicit missing-capability failures.
- [x] 4.3 Update in-memory and PostgreSQL catalogs, test fakes, demo composition, and host dependency fixtures to satisfy the complete ports under Mypy.
- [x] 4.4 Add static/runtime protocol conformance tests proving every Admin-invoked method is declared and partial hosts fail closed at the requested capability boundary.

## 5. Consolidate Verification Evaluation

- [x] 5.1 Pin current Layer 2/3 evidence fingerprints, status precedence, tagged-scalar behavior, preflight issue codes, deadline handling, cleanup issue ordering, and shared-cache execution counts.
- [x] 5.2 Extract canonical evaluator utilities for tagged scalar comparison, selected observation lookup, status reduction, preflight, deadline context, cache invocation, cleanup merging, and deterministic layer aggregation.
- [x] 5.3 Reduce smoke and semantic modules to layer-specific assertion/contract dispatch over shared mechanics; preserve all fail-closed behavior and redaction.
- [x] 5.4 Move reusable Verification test builders out of private unit-test modules into a dedicated test-support module and update conformance tests to avoid importing test implementation details.
- [x] 5.5 Run focused Verification model/execution/layer/suite/publishing/Admin/persistence/conformance tests and confirm no evidence fingerprint changes.

## 6. Publication Aggregate and Gate Extraction

- [x] 6.1 Introduce typed `PublicationRequest`, immutable `PublicationContext`, gate result, frozen binding, and `PublicationAggregate` models with complete cross-link validators.
- [x] 6.2 Extract fixed freeze, materialize/structural, verify, aggregate/audit, and persist stages from `publish_assembly`; each stage receives narrow immutable inputs and returns bounded outputs/issues.
- [x] 6.3 Implement a short publication coordinator over the fixed stage order; preserve short-circuit semantics, exception normalization, authorization, separation-of-duties, and no-external-work-before-structural-pass behavior.
- [x] 6.4 Convert `assembly.publishing.publish_assembly` into a compatibility facade that constructs the typed request/context and delegates without retaining domain logic.
- [x] 6.5 Update in-memory and PostgreSQL publication ports to accept `PublicationAggregate`; remove parallel nullable manifest/evidence/audit/draft parameters after call-site migration.
- [x] 6.6 Run publication atomicity, idempotency, concurrency, verification, audit, tenant, fingerprint, and full bundle-view workflow characterization tests after each stage extraction.

## 7. Admin Capability Service Decomposition

- [x] 7.1 Extract shared typed `AdminRequestContext`, authorization/source-scope helpers, dependency access, error normalization, and common pagination/job utilities without changing safe errors.
- [x] 7.2 Extract metadata/discovery and authoring capability services with their existing DTO projections and tests.
- [x] 7.3 Extract assembly lifecycle and verification/publication capability services with revision, authorization, separation, evidence, and publish orchestration tests.
- [x] 7.4 Extract published Bundle lifecycle service for lookup, audit/evidence, versions, activation, rollback, and state transitions.
- [x] 7.5 Convert `AdminService` to a <=400-line compatibility facade delegating every existing method; keep constructor, method signatures, capability output, generated service schema, DTO JSON, and normalized errors unchanged.
- [x] 7.6 Add focused capability-service tests plus full Admin contract/security/schema/host-integration tests; ensure unrelated services need no edits for a local capability change.

## 8. PostgreSQL Repository Decomposition

- [x] 8.1 Extract shared SQL template registry categories, transaction/UnitOfWork, cursor execution, timeout, envelope encoding/decoding, schema-version handling, and error normalization.
- [x] 8.2 Extract draft and proposal/snapshot repositories with focused tests and no publication dependencies.
- [x] 8.3 Extract publication aggregate and verification/audit repositories that accept an existing UnitOfWork and never commit independently.
- [x] 8.4 Extract activation/history/version repositories while preserving advisory locks, pointer/history semantics, and dependency validation.
- [x] 8.5 Convert PostgreSQL `store.py` to a <=600-line protocol facade/cross-repository coordinator; keep each repository <=700 lines and retain exact external methods.
- [x] 8.6 Split fake PostgreSQL handlers by repository domain over shared lock/key/transaction infrastructure and preserve exact production SQL template coverage.
- [x] 8.7 Replace brittle full-operation SQL-order assertions where possible with repository state/atomicity contracts while retaining focused SQL parameter/order tests for locking and transactional writes.
- [x] 8.8 Run failure injection at every publication write, restart/reload, concurrent first publish/reuse, activation/rollback, tenant isolation, cleanup, security, and real-PostgreSQL integration tests.

## 9. Complexity Ratchets, Documentation, and Completion

- [x] 9.1 Tighten the architecture manifest to final budgets: Admin facade <=400 lines, capability service <=500, publication coordinator <=250, gate module <=300, PostgreSQL store facade <=600, repository <=700, no control-plane duplicate modules, and no prohibited cycles.
- [x] 9.2 Update package-boundary and contributor docs with the final dependency DAG, publication aggregate, capability ports, UnitOfWork/repository ownership, compatibility facades, and architecture-check troubleshooting.
- [x] 9.3 Record before/after metrics and verify reduced hotspot size, duplicate count, `Any` boundary count, and cross-domain imports without increasing total behavioral complexity or weakening tests.
- [x] 9.4 Run focused architecture, historical evidence, publication, Verification, Admin, persistence, security, conformance, and demo tests.
- [x] 9.5 Run full pytest, Ruff, Mypy, `scripts/check_docs.py`, build/package smoke checks, and `openspec validate simplify-semantic-control-plane-architecture --type change`.
- [x] 9.6 Verify all existing public imports, DTO schemas, wire payloads, semantic/evidence fingerprints, error codes, tenant/source isolation, publication outcomes, and archived OpenSpec contracts remain compatible.
- [x] 9.7 Remove migration-only shims and temporary metrics exemptions; do not mark the change complete while any compatibility shim contains logic or any final architecture budget is waived.

## 10. Centralized Publication Integrity Hardening

- [x] 10.1 Add `PublicationRecordSet` with `validate_publication_integrity` as the single publication-record rule chain (stable issue codes) and `build_publication_records` as the only compatibility-kwargs-to-records converter at the catalog facades.
- [x] 10.2 Route every publish, reuse, read (`publication_records`, `verification_evidence`, `active`), activate, rollback, and reload path in both catalogs through the centralized validator; the published-version row's `audit_id` is the independent witness against deleted audit/evidence rows on reads.
- [x] 10.3 Unify pointer/history ID/version/fingerprint/state checks in `validate_lifecycle_witness` (with `require_state`) and map witness codes to persisted-record cause types via `witness_cause_type`.
- [x] 10.4 Keep compatibility publish keyword arguments only at the outermost facades; repositories accept record sets/aggregates only; pin the aggregate-only boundary with the architecture test and ratchet the cross-domain import baseline with justification.
- [x] 10.5 Add the parameterized persistence failure matrix: delete or tamper each persisted artifact (Bundle, manifest, audit, evidence, binding, version row, pointer, history) one at a time and assert every entry point fails closed with its stable outcome, covering both the active publication and the rollback target.
- [x] 10.6 Close the two remaining lifecycle gaps: re-activation with a missing pointer but an existing ACTIVE version row fails closed (`orphan_active_version`) instead of minting a second ACTIVE row, `reload_active` sweeps for pointerless ACTIVE rows, and rollback validates that the top history row sits at the pointer's `activation_sequence` (`history_discontinuity`, with rollback consuming the top row into the freed sequence slot); extend the failure matrix with `pointer-deleted` and `history-deleted` and pin chained-rollback continuity.
- [x] 10.7 Close the empty-history ambiguity: first-ever activations park the pointer at `activation_sequence = 0`, so a cleared history beside a sequence >= 1 fails closed with `history_discontinuity` instead of the pre-mature `no_rollback_history`; add the `history-cleared` matrix fault and pin that first-ever rollback remains `no_history`.
