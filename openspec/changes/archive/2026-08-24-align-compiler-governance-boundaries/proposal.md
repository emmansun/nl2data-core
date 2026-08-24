## Why

The project now has a canonical Semantic Query IR, resolved Semantic Views, and deterministic SQL/MongoDB compilers, but compiler facts and governance decisions are still connected through adapter-specific conventions. DDS-019 requires one explicit boundary where semantic intent, policy constraints, physical artifact evidence, authorization, resource limits, and protected results remain linked without allowing compilers to grant authority.

## What Changes

- Add a common compiler contract for validated Semantic Query IR plus immutable compilation context.
- Define backend-neutral compilation evidence and artifact facts that governance can evaluate without parsing SQL/MQL.
- Align SQL and MongoDB compilers around the same provenance, capability, policy-obligation, and artifact-fingerprint lifecycle.
- Make artifact guards enforce policy obligations and resource bounds before authorization and execution.
- Bind execution authorization to IR fingerprint, view/model/policy evidence, artifact fingerprint, adapter capabilities, tenant scope, and effective limits.
- Ensure result protection and audit evidence retain the full logical-to-physical decision chain without exposing raw artifacts or sensitive values.
- Add cross-backend conformance tests for denial, stale evidence, mandatory filters, capability mismatch, bounds, and protected results.

## Capabilities

### New Capabilities

- `compiler-governance-boundaries`: Shared compiler context, evidence, artifact guard, authorization binding, and result/audit linkage across backend compilers.

### Modified Capabilities

- `canonical-semantic-query-ir`: IR compilation SHALL require current view/model/policy context and emit governance-consumable evidence.
- `query-adapter-contract`: Compiler and artifact lifecycle SHALL expose aligned safe facts and enforce a common pre-execution guard boundary.
- `query-governance-foundation`: Governance SHALL consume semantic and artifact facts across planning, compilation, execution, and result protection.
- `workflow-runtime-contract`: Workflow stages SHALL use the aligned compiler, guard, authorization, and evidence chain.

## Impact

Affected areas include planning IR validation, compiler modules for SQL/MongoDB, adapter models and guards, governance authorization/evaluation, workflow runtime evidence, result protection, and cross-backend tests. No new database/LLM dependency, HTTP transport, deployment change, distributed state implementation, or public transport API is included.
