## Context

The current system has the right components but no single owner for their order. `AIWorkflowRunner` recalls Memory, resolves intent, builds a plan, executes it, and writes a reference; `QueryExecutionRunner` performs validation, governance, authorization, execution, and protection. This is useful as a foundation but makes future clarification, repair, cancellation, approval, and streaming behavior accumulate as nested conditionals.

DDS-012 defines a staged Workflow Runtime. P2.5 will implement the core contract and deterministic reference runtime. LangGraph may implement the same contract later, but core behavior must remain usable without it.

## Goals / Non-Goals

**Goals:**

- Define one explicit ordered workflow stage graph and mandatory gates.
- Compose existing AI, Memory, Tenant, Governance, Adapter, Result Protection, StateStore, and idempotency boundaries.
- Make cancellation, deadlines, retries, clarification, approval-required, failure, and recovery explicit typed outcomes.
- Persist safe checkpoints at stage boundaries and resume only when snapshot fingerprints remain compatible.
- Provide deterministic tests and a runtime implementation that does not require LangGraph.

**Non-Goals:**

- Replacing the existing adapter, governance, Memory, or durable-state contracts.
- MongoDB implementation, HTTP transport, public SDK redesign, streaming wire protocols, or plugin execution.
- Full query repair or autonomous agent loops; only bounded extension points are defined.

## Decisions

1. **Own orchestration in a core `WorkflowRuntime` contract.** Nodes receive typed immutable context and return typed stage results. `AIWorkflowRunner` becomes a compatibility adapter or delegates to the runtime. A LangGraph-first public API was rejected because it would make an optional framework part of the library contract.

2. **Use an explicit linear gate graph with bounded branches.** The reference flow is `initialize -> memory -> intent -> plan -> validate -> govern -> authorize -> execute -> protect -> persist -> complete`; clarification, denial, cancellation, timeout, and bounded recovery are terminal or controlled branch states. Arbitrary model-driven loops are rejected.

3. **Keep stage outputs safe and fingerprinted.** Checkpoints persist stage name, workflow state, current tenant scope, configuration/policy/catalog/semantic/artifact fingerprints, and safe evidence references. Raw prompt, query, result, provider, and native objects never enter runtime state.

4. **Make every execution gate mandatory.** A runtime cannot invoke an adapter unless current plan validation, governance, authorization, tenant scope, and deadline checks pass. Rewritten or repaired artifacts require revalidation and a new authorization.

5. **Use cooperative cancellation and request deadlines.** Nodes receive a cancellation/deadline context and must stop before starting the next external operation. The runtime cannot claim cancellation stopped an already-running external database call; it records an ambiguous/reconciliation state when needed.

6. **Use optional backend adapters.** A future `nl2data-langgraph` implementation can translate the core stage contract to LangGraph nodes/checkpoints. It must pass the same conformance suite and cannot bypass core gates.

## Risks / Trade-offs

- [Risk] A custom runtime may recreate a workflow framework. → Keep the P2.5 graph small, typed, deterministic, and focused on NL2Data gates.
- [Risk] Compatibility with existing runners can create duplicate orchestration paths. → Define one runtime-owned path and retain runners only as adapters/fallbacks during migration.
- [Risk] Cancellation after external execution is ambiguous. → Persist evidence and reconciliation status; do not claim exactly-once or guaranteed cancellation.
- [Risk] Optional LangGraph backend may diverge from core semantics. → Require shared contract and conformance tests before activation.

## Migration Plan

1. Add runtime contracts and deterministic stage implementations without changing the default fallback.
2. Route opt-in AI+Memory execution through the core runtime.
3. Add durable checkpoint stage metadata and resume compatibility checks.
4. Migrate existing integration tests from direct runner composition to runtime conformance cases.
5. Add LangGraph only as a later optional backend implementing the same contract.

## Open Questions

- Should approval-required be introduced as a public `OutcomeStatus` now or remain an internal runtime event until the public facade change?
- Which stage checkpoint data is sufficient to resume without storing model prompts or plans containing sensitive semantic details?
- Should bounded repair be included in this change or follow as a W2-specific change?