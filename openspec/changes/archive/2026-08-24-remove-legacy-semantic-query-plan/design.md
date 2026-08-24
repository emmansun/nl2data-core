## Context

`SemanticQueryIR` is implemented and is already normalized at the governed boundary, but active code still carries `SemanticQueryPlan` plus a bidirectional compatibility module. Because the project has no external consumers, this compatibility window provides no release value and creates duplicate logical models. The cleanup must preserve behavior while making IR the only planning contract.

## Goals / Non-Goals

**Goals:**

- Remove `SemanticQueryPlan` and its legacy component model surface from active source.
- Make AI planning, workflow, evaluation, SQL compilation, and MongoDB compilation consume `SemanticQueryIR` directly.
- Keep physical bindings in explicit compiler context and retain all existing governed gates.
- Update active specs and tests so the canonical IR contract is authoritative.

**Non-Goals:**

- Changes to the public `nl2data` models or facade behavior.
- Semantic View implementation, Semantic Model DSL, context retrieval, or new adapters.
- Changes to artifact safety rules, tenant authorization, result protection, or workflow state semantics beyond plan identity fields.
- Rewriting historical archived change artifacts.

## Decisions

### Delete the compatibility bridge

Remove `planning.ir.compat` and all calls to `plan_to_ir`/`ir_to_plan`. The bridge is no longer needed because no external users require source compatibility, and its lossy reverse mapping can silently discard IR time context, extensions, and explicit groupings.

### Make IR the direct compiler input

Change compiler and runtime callables to accept `SemanticQueryIR`. Physical bindings become a required compiler context/input where needed; they never enter IR serialization. SQL and MongoDB behavior remains unchanged apart from the logical input type.

### Migrate the builder and resolver together

`build_plan_from_intent` becomes an IR builder, and `PlanResolver`/`StaticPlanResolver` return IR. Validation happens once through IR validation at the planning boundary. This removes duplicate plan validation and avoids constructing a legacy model only to immediately translate it.

### Update specifications as an intentional breaking change

The active semantic-query-planning contract is rewritten to describe IR. The canonical IR spec removes the legacy bridge requirement. Historical archived specs remain unchanged for auditability.

## Risks / Trade-offs

- [Internal tests or integrations import legacy classes] → Update all repository callers in one change and treat remaining imports as deliberate failures.
- [Compiler behavior changes during signature migration] → Preserve existing fixtures and run SQL/MongoDB contract and integration suites before and after migration.
- [Physical binding is lost] → Require it through compiler context and add tests proving compilation fails safely when a required binding is absent.
- [Checkpoint/evidence compatibility changes] → Keep IR version/fingerprint as the sole logical identity and reject old legacy-plan evidence rather than reinterpret it.

## Migration Plan

1. Add IR-native builder/compiler/context signatures and tests while the legacy model is still present.
2. Migrate workflow, AI, evaluation, SQL, MongoDB, and fixtures to IR-only calls.
3. Delete the legacy plan models, compatibility bridge, and obsolete tests/imports.
4. Update active specifications and README terminology.
5. Run full tests, lint, type checking, and import-boundary checks.
6. Roll back by reverting this repository change; no external data migration is required.

## Open Questions

- Whether `PhysicalBinding` should move into a dedicated `planning.compiler` module now or remain temporarily in `planning.models` as a compiler-only type.
- Whether the next Semantic View change should make `view_ref` mandatory immediately or only when a registry is configured.
