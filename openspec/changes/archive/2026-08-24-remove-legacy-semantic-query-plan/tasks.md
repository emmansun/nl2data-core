## 1. IR-Native Planning

- [x] 1.1 Change AI plan construction to return validated `SemanticQueryIR` directly, including provenance, result shape, grouping, and capability requirements.
- [x] 1.2 Change `PlanResolver` and `StaticPlanResolver` contracts to return `SemanticQueryIR` and remove legacy plan validation calls.
- [x] 1.3 Move or retain `PhysicalBinding` as an explicit compiler-context type without exposing it through IR serialization.

## 2. Compiler and Runtime Migration

- [x] 2.1 Update SQL compiler entry points and SQL execution fixtures to consume `SemanticQueryIR` directly.
- [x] 2.2 Update MongoDB compiler entry points and MongoDB execution fixtures to consume `SemanticQueryIR` directly.
- [x] 2.3 Remove `plan_to_ir` and `ir_to_plan` calls from workflow runner/runtime and preserve all validation, governance, authorization, protection, and fallback gates.
- [x] 2.4 Update evaluation and conformance components to use IR-only planning and compiler inputs.
- [x] 2.5 Ensure workflow and artifact evidence persist only IR version/fingerprint and never legacy plan identity or raw physical content.

## 3. Remove Legacy Surface

- [x] 3.1 Delete `SemanticQueryPlan` and obsolete plan-only component models after all active callers are migrated.
- [x] 3.2 Delete `planning.ir.compat` and remove stale exports/imports/docstrings.
- [x] 3.3 Remove or rewrite legacy plan tests and fixtures; add assertions that the legacy symbol and bridge are absent.
- [x] 3.4 Update active README and documentation terminology from Semantic Query Plan to Semantic Query IR.

## 4. Specifications and Verification

- [x] 4.1 Update active semantic planning, canonical IR, adapter, and workflow specifications without modifying historical archives.
- [x] 4.2 Add IR-native SQL/MongoDB golden tests proving logical fingerprints remain stable and physical artifact fingerprints remain distinct.
- [x] 4.3 Add security tests proving invalid IR and physical payloads cannot reach adapters after legacy removal.
- [x] 4.4 Run full pytest, Ruff, Mypy, and import-boundary checks; verify no active source/test/documentation reference to `SemanticQueryPlan` remains.
