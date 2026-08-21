## 1. Runtime Contracts and Stage Model

- [x] 1.1 Define workflow stage, gate, terminal/branch outcome, deadline, cancellation, retry budget, and typed execution-context models.
- [x] 1.2 Define a framework-neutral `WorkflowRuntime` protocol and node/stage ports with safe evidence and normalized errors.
- [x] 1.3 Extend checkpoint state with current stage, gate evidence fingerprints, compatibility fingerprints, cancellation/deadline state, and bounded retry/repair counters.
- [x] 1.4 Add contract tests for immutable stage context, invalid gate order, bounded budgets, safe serialization, and runtime import without LangGraph.

## 2. Deterministic Runtime Graph

- [x] 2.1 Implement explicit ordered stages for request initialization, Memory recall, intent resolution, plan building, plan validation, governance, authorization, execution, result protection, Memory write-back, and completion.
- [x] 2.2 Implement mandatory gate checks that prevent adapter execution without current tenant, plan validation, governance, artifact validation, and authorization evidence.
- [x] 2.3 Implement clarification, rejection, timeout, cancellation, retry exhaustion, and approval-required branch outcomes without raw provider/task leakage.
- [x] 2.4 Implement cooperative deadline/cancellation propagation and bounded node retries.
- [x] 2.5 Add deterministic runtime tests for normal read flow, malformed intent, policy denial, stale Memory, clarification, timeout, and cancellation before adapter execution.

## 3. Checkpoint, Resume, and Idempotency Integration

- [x] 3.1 Persist safe stage checkpoints through the P2.3 StateStore and retain tenant scope and compatibility fingerprints.
- [x] 3.2 Resume only compatible non-terminal checkpoints and reject stale configuration/policy/catalog/semantic/artifact snapshots.
- [x] 3.3 Integrate durable idempotency with runtime terminal outcomes without re-executing completed external work.
- [x] 3.4 Add recovery tests for restart, ambiguous post-execution state, stale checkpoint, cross-tenant checkpoint, and retry budget exhaustion.

## 4. Existing Runner Migration and Optional Backend Boundary

- [x] 4.1 Route the opt-in AI+Memory composition through the deterministic core runtime while preserving P1 structured-plan and not-configured fallbacks.
- [x] 4.2 Reduce `AIWorkflowRunner` to a compatibility facade/delegating adapter rather than a second orchestration implementation.
- [x] 4.3 Define the optional LangGraph backend contract/profile without adding LangGraph to core dependencies or imports.
- [x] 4.4 Add backend conformance tests proving optional implementations cannot bypass mandatory gates.

## 5. Quality Gates and Documentation

- [x] 5.1 Add complete workflow-runtime conformance cases with protected evidence and mandatory gate assertions.
- [x] 5.2 Run all P0/P1/P2 tests plus workflow, cancellation, recovery, security, Ruff, Mypy, and package-install checks.
- [x] 5.3 Document the core runtime contract, optional LangGraph integration, at-least-once recovery semantics, and unsupported streaming/distributed-worker features.