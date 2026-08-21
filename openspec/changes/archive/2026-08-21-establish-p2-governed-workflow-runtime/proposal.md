## Why

The library now has AI intent resolution, Memory, tenant scope, governed adapters, durable checkpoints, and idempotency, but these capabilities are still composed through specialized runners. The system needs one explicit workflow runtime that owns sequencing, gates, budgets, cancellation, clarification, recovery, and final outcome assembly.

P2.5 establishes a core-owned deterministic workflow contract and runtime. LangGraph remains an optional integration backend and is not required by the core library.

## What Changes

- Add a provider-neutral `WorkflowRuntime` contract and typed workflow execution context, node results, gates, deadlines, cancellation, and evidence references.
- Implement a deterministic core runtime that composes Memory, AI intent resolution, semantic plan validation, Governance, adapter validation/execution, Result Protection, durable checkpointing, and Memory write-back through explicit stages.
- Make clarification, rejection, approval-required, timeout, cancellation, retry exhaustion, and recovery first-class workflow outcomes.
- Enforce mandatory ordering so no execution path bypasses plan validation, governance, artifact authorization, result protection, tenant scope, or durable idempotency.
- Replace direct AI/plan orchestration growth in `AIWorkflowRunner` with a runtime composition boundary while preserving existing P1/P2 fallback behavior.
- Add optional LangGraph integration points without importing LangGraph in the core default installation.
- Add deterministic workflow conformance tests for normal read flow, clarification/resume, stale context, policy denial, timeout, cancellation, recovery, and safe evidence.
- Defer MongoDB-specific nodes, public facade redesign, HTTP hosting, streaming transport, distributed workers, and plugin hooks.

## Capabilities

### New Capabilities

- `workflow-runtime-contract`: Core workflow context, stages, gates, cancellation, deadlines, outcomes, and extension-neutral runtime protocol.
- `deterministic-workflow-runtime`: Reference runtime composing current AI, Memory, tenant, governance, adapter, protection, and durable-state ports.
- `workflow-runtime-conformance`: Mandatory end-to-end workflow and recovery assertions with protected evidence.

### Modified Capabilities

- `workflow-state-foundation`: Workflow state becomes a runtime checkpoint contract with stage identity, gate evidence, cancellation/deadline state, and durable resume semantics.

## Impact

- Adds workflow runtime modules under `src/nl2data_core/workflow/` and refactors composition around the existing runners without changing adapter contracts.
- Adds no mandatory LangGraph dependency; an optional backend package/profile may be added later.
- Extends configuration and public workflow composition while preserving `import nl2data` and the P1/P2 fallback paths.
- Adds integration, security, cancellation, recovery, and conformance tests.