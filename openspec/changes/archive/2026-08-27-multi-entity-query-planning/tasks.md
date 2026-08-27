## 1. Phase P1: Multi-Entity Intent Foundation

- [x] 1.1 Add new intent models in `src/nl2data_core/ai/models.py` (`MultiEntityIntent`, `EntityRef`, `MetricRef`, `DimensionRef`, `JoinHintRef` if needed) with immutable/bounded pydantic fields.
- [x] 1.2 Add canonicalization/fingerprint helpers for multi-entity intent in `src/nl2data_core/ai/fingerprint.py` and/or `src/nl2data_core/canonical.py` usage points.
- [x] 1.3 Extend resolver output parsing in `src/nl2data_core/ai/resolver.py` so provider structured output can produce multi-entity intent while preserving existing single-entity schema.
- [x] 1.4 Add fail-closed validation in `src/nl2data_core/ai/resolver.py` and `src/nl2data_core/planning/validation.py` for unresolved semantic refs and physical-query text in multi-entity payloads.
- [x] 1.5 Add compatibility adapter in `src/nl2data_core/ai/plan_builder.py` to map single-entity intent to current IR flow unchanged when multi-entity fields are absent.
- [x] 1.6 Add unit tests in `tests/unit/test_ai_models.py` for field bounds, immutability, and serialization stability of `MultiEntityIntent`.
- [x] 1.7 Add contract tests in `tests/contract/test_intent_resolution.py` for allowed multi-entity outputs and fail-closed rejection of SQL/MQL/injection-shaped payloads.
- [x] 1.8 Add IR compatibility tests in `tests/contract/test_ir_compatibility.py` proving single-entity path remains behaviorally identical.
- [x] 1.9 Add integration coverage in `tests/integration/test_ir_workflow.py` and/or `tests/integration/test_workflow_execution.py` for multi-entity intent reaching planning gate without adapter execution on invalid refs.
- [x] 1.10 Record rollout flag and defaults (off-by-default for multi-entity path if required) in config docs and tests.

## 2. Phase P2: RelationshipGraph and JoinPlanner

- [x] 2.1 Add relationship graph models in `src/nl2data_core/planning/models.py` (nodes, edges, join keys, cardinality hints, edge metadata, graph identity).
- [x] 2.2 Add canonical graph fingerprinting helpers in `src/nl2data_core/canonical.py` and/or new helpers under `src/nl2data_core/planning/ir/`.
- [x] 2.3 Add validation rules in `src/nl2data_core/planning/validation.py` for edge integrity (known refs, bounded identifiers, no unsafe/physical payload fields).
- [x] 2.4 Define deterministic join-plan models in `src/nl2data_core/planning/ir/models.py` (`LogicalJoinPlan`, join steps, selected path evidence).
- [x] 2.5 Extend planning contracts in `src/nl2data_core/workflow/runner.py` with an internal `JoinPlanner` protocol/port and deterministic output contract.
- [x] 2.6 Implement default deterministic planner in a new module (for example `src/nl2data_core/planning/join_planner.py`) with canonical path ordering and stable tie-break policy.
- [x] 2.7 Return structured path-resolution outcomes for `path_not_found` and `path_ambiguous` in planner/runtime boundary mapping (`src/nl2data_core/workflow/runtime.py`, `src/nl2data_core/errors.py` mapping as needed).
- [x] 2.8 Add contract tests in `tests/contract/test_canonical_ir.py` and/or a new `tests/contract/test_join_planner.py` for graph and plan fingerprint determinism.
- [x] 2.9 Add contract tests in `tests/contract/test_workflow_runtime_contract.py` proving unresolved/ambiguous paths do not invoke adapters.
- [x] 2.10 Add integration tests in `tests/integration/test_ir_workflow.py` and `tests/integration/test_workflow_runtime.py` for deterministic multi-entity path selection under equivalent input order permutations.

## 3. Phase P3: Compiler and Runtime Integration

- [x] 3.1 Extend compile contract/context in `src/nl2data_core/compilation/contract.py` to carry logical join-plan evidence and planner identity.
- [x] 3.2 Integrate join-plan aware compile path into `src/nl2data_core/workflow/runtime.py` between intent/IR validation and artifact guard stages.
- [x] 3.3 Update execution planner path in `src/nl2data_core/workflow/runner.py` and `src/nl2data_core/ai/workflow.py` so multi-entity plans flow through existing deterministic runtime gates.
- [x] 3.4 Update SQL compile path in `src/nl2data_core/adapters/sql/compile.py` to consume logical join-plan context deterministically without accepting raw join text from provider output.
- [x] 3.5 Ensure artifact guard and governance evidence include join-plan/context fingerprints (`src/nl2data_core/adapters/models.py`, `src/nl2data_core/adapters/sql/guard.py`, governance evidence mapping points).
- [x] 3.6 Ensure stale view/policy/capability/tenant evidence invalidates planned execution before adapter call (`src/nl2data_core/workflow/runtime.py`).
- [x] 3.7 Add integration tests in `tests/integration/test_runtime_recovery.py`, `tests/integration/test_semantic_view_workflow.py`, and `tests/integration/test_tenant_workflow.py` for stale-evidence rejection and replanning requirements.
- [x] 3.8 Add adapter-facing tests in `tests/contract/test_sql_adapter.py` and/or `tests/integration/test_workflow_execution.py` proving unresolved/ambiguous path outcomes never trigger adapter execution.
- [x] 3.9 Add conformance updates in `tests/conformance/test_workflow_runtime_conformance.py` for preserved gate order with multi-entity planning enabled.
- [x] 3.10 Add profile-level coverage in `tests/integration/test_production_profile_e2e.py` to validate multi-entity planning behavior under full governed runtime flow.

## 4. Governance, Docs, and Operational Readiness

- [x] 4.1 Define structured public/internal error codes for path failures and unsupported rollout states.
- [x] 4.2 Document relationship graph authoring, ambiguity handling, and troubleshooting for operators.
- [x] 4.3 Document phase rollout controls and feature-gate behavior.
- [x] 4.4 Update architecture docs to show where join planning sits between intent resolution and compilation.

## 5. Quality Gates

- [x] 5.1 Run OpenSpec validation for `multi-entity-query-planning` and fix any schema or scenario formatting issues.
- [x] 5.2 Verify deterministic test profile and integration profile both provide clear pass/fail evidence for multi-entity planning behavior.
- [x] 5.3 Publish a readiness checklist showing P1/P2/P3 acceptance criteria and rollback conditions.

## 6. P1 Exit Criteria (Go/No-Go)

- [x] 6.1 All P1 unit/contract/integration tests pass in local deterministic profile.
- [x] 6.2 Existing single-entity golden-path tests show no regression.
- [x] 6.3 Multi-entity invalid-reference and unsafe-payload cases reject before any adapter call.
- [x] 6.4 Team review signs off that P1 contracts are stable enough to begin P2 `RelationshipGraph` work.

## 7. P2 Exit Criteria (Go/No-Go)

- [x] 7.1 Relationship graph and logical join plan fingerprints are deterministic across equivalent input orderings.
- [x] 7.2 Not-found and ambiguous path outcomes are structured and never silently auto-selected.
- [x] 7.3 Contract/integration tests confirm unresolved or ambiguous paths stop before adapter invocation.
- [x] 7.4 Team review signs off planner determinism and governance-boundary compatibility before P3 integration.

## 8. P3 Exit Criteria (Go/No-Go)

- [x] 8.1 Multi-entity join-plan evidence flows through compile -> guard -> govern -> authorize -> execute gates without bypass.
- [x] 8.2 Stale evidence cases (view/policy/capability/tenant) reject before adapter access and require replanning.
- [x] 8.3 Deterministic and integration profiles both pass with multi-entity planning enabled.
- [x] 8.4 Release-readiness review confirms rollback/feature-gate path works without breaking single-entity flows.

## 9. Readiness Checklist

- [x] P1 multi-entity intent models, validation, and tests implemented
- [x] P2 deterministic `RelationshipGraph` + `JoinPlanner` implemented and tested
- [x] P3 compilation context/runtime integration and SQL JOIN generation implemented
- [x] All local quality gates pass: `ruff`, `mypy`, `pytest`, `check_docs`
- [x] Single-entity golden-path tests show no regression
- [x] Multi-entity unsupported/ambiguous/not-found/unauthorized paths reject before adapter
- [x] Operator docs, troubleshooting, and error-code reference updated
- [x] Rollback path documented: remove `JoinPlanner` binding to disable multi-entity
- [x] OpenSpec CLI validation executed (change and all specs validated)
- [x] Team review sign-off for P1/P2/P3 exit criteria

## 10. Review Log (2026-08-27)

Findings from the implementation review and their resolutions:

- [x] **Join step ordering bug**: `JoinPlanner` sorted steps by `step_id`, which
  broke the topological order the SQL compiler relies on (a multi-hop plan
  could emit `JOIN` clauses referencing an alias not yet in `FROM`, producing
  invalid SQL). Fixed by keeping the deterministic path order; compiler now
  also rejects out-of-order steps and unqualified fields in joined queries.
- [x] **Dead `join_hints` vocabulary**: `JoinHintRef`/`MultiEntityIntent.join_hints`
  were never consumed by the planner (v1 is strict deterministic
  shortest-path with ambiguity rejection). Removed the unused field and model.
- [x] **Dead validation helper**: `validate_multi_entity_intent_references` in
  `planning/validation.py` had no call sites; the resolver performs the same
  fail-closed checks inline with richer error details. Removed the duplicate.
- [x] **Planner source check**: `JoinPlanner.plan()` now fails closed
  (`unauthorized`) when the intent `source_id` does not match the authorized
  view, so the standalone port is safe even outside the runtime.
- [x] Regression tests added: topological step order (planner + compiler),
  out-of-order join-step rejection, unbound-field rejection in joined
  queries, and intent source mismatch.
- [x] Gates re-run after fixes: `pytest` 1913 passed / 59 skipped, `ruff`
  clean, `mypy` clean, `check_docs` passed, `openspec validate` passed.

### Follow-up hardening (2026-08-27, after team sign-off)

- [x] **PostgreSQL capabilities**: `PostgresQueryAdapter.capabilities()` now
  declares `join`/`multi_entity` features, matching the core SQL adapter
  (PostgreSQL natively executes JOIN queries through the shared compiler
  and guard; only the capability declaration was missing).
- [x] **MongoDB fail-closed**: `compile_mongo`/`compile_mongo_ir` reject any
  IR carrying a `join_plan_fingerprint` (`MONGO_REJECTED`); a joined IR can
  no longer be silently degraded into a single-collection MQL query.
  Full `$lookup`-based multi-entity support remains a separate change.
- [x] Regression tests: PostgreSQL capability declaration assertion; Mongo
  compiler join-plan rejection (checked before fingerprint verification).
- [x] Targeted gates: 66 + 33 tests passed, `ruff` clean on touched files.
- [x] **Documentation sync sweep**: `execution-flow.zh-CN.md` and
  `configuration.zh-CN.md` were missing the multi-entity sections (both
  now synced, including the mermaid join-plan branch);
  `semantic-layer.md`/`semantic-layer.zh-CN.md` still described
  relationships as "vocabulary before execution" — updated to the
  implemented "compiled at query time" state (heading, body, and anchor
  link); fixed `execution-flow.md` outcome count (three → four).
  `error-codes`/`troubleshooting` have no zh-CN variant;
  `capabilities.md` already declares `$lookup` unsupported. `check_docs`
  passes.
