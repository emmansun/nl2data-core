## Why

The repository now has a canonical `SemanticQueryIR`, but the runtime still maintains `SemanticQueryPlan` and a bidirectional compatibility bridge. There are no external users to protect, so retaining the legacy model creates duplicate validation, fingerprint, grouping, and compiler contracts without providing migration value.

## What Changes

- **BREAKING** Remove the legacy `SemanticQueryPlan` model and its duplicate plan component models from active source APIs.
- Make `SemanticQueryIR` the only internal logical query representation.
- Remove `plan_to_ir` and `ir_to_plan` compatibility translation.
- Update AI planning, workflow runner/runtime, evaluation, SQL compiler, and MongoDB compiler to consume IR directly.
- Move physical bindings fully into compiler/model context and keep them out of IR serialization.
- Update workflow evidence, artifact evidence, tests, documentation, and active specifications to use IR terminology.
- Preserve governed validation, tenant/policy authorization, artifact guards, result protection, fallback behavior, and adapter lifecycle semantics.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `canonical-semantic-query-ir`: Make SemanticQueryIR the sole logical query contract and remove the legacy-plan compatibility requirement.
- `query-adapter-contract`: Require compiler-facing paths to consume validated IR without legacy plan adapters.
- `workflow-state-foundation`: Require workflow planning/checkpoint evidence to reference IR only and remove legacy plan compatibility assumptions.
- `semantic-query-planning`: Replace the legacy Semantic Query Plan contract with the canonical Semantic Query IR contract.

## Impact

This is an intentional internal breaking change across `src/nl2data_core/planning`, AI, workflow, SQL/MongoDB compilation, evaluation, and tests. The public `nl2data` API remains protected because it does not expose `SemanticQueryPlan`. Historical archived specifications are not rewritten. The subsequent Semantic View change will target IR directly and no longer need a static-plan compatibility mode.
