## 1. Shared Compiler Contracts

- [x] 1.1 Define immutable `CompilationContext` with validated IR, resolved view/model bundle evidence, tenant/purpose references, policy fingerprint, adapter capabilities, limits, and compiler-specific physical context.
- [x] 1.2 Define safe compilation facts/evidence linking IR, view/model, policy, capability, compiler, artifact, and guard identities.
- [x] 1.3 Define compiler and artifact-guard result contracts with structured fail-closed issues and no raw physical payload in evidence.

## 2. Compiler Alignment

- [x] 2.1 Adapt SQL compiler entry points to consume shared compilation context and emit common evidence while preserving deterministic SQL generation.
- [x] 2.2 Adapt MongoDB compiler entry points to consume shared compilation context and emit common evidence while preserving structured MQL generation.
- [x] 2.3 Ensure compilers cannot authorize, broaden policy scope, bypass view membership, or omit required capability/limit facts.
- [x] 2.4 Align compiler artifact fingerprints and compiler identity/version across SQL and MongoDB paths.

## 3. Guard and Governance Integration

- [x] 3.1 Make SQL and MongoDB artifact guards consume common policy obligations, effective limits, tenant scope, and IR filter evidence.
- [x] 3.2 Add a shared pre-execution guard boundary that rejects unvalidated, stale, unsupported, or obligation-incomplete artifacts.
- [x] 3.3 Extend governance facts/decisions to include IR, view/model, capability, operation, artifact, and mandatory-filter references.
- [x] 3.4 Extend execution authorization issuance and verification to bind the complete logical/physical governance context.

## 4. Workflow and Result Evidence

- [x] 4.1 Enforce the ordered workflow gates: IR/view validation, compilation, artifact guard, governance, authorization, execution, protection, persistence.
- [x] 4.2 Reverify artifact, policy, view/model, capability, tenant, and effective-limit evidence immediately before adapter execution.
- [x] 4.3 Link protected result and audit evidence to logical IR, view/model, policy, artifact, adapter, authorization, and result fingerprints.
- [x] 4.4 Preserve cancellation, deadlines, tenant isolation, idempotency, stateless fallback, and public facade behavior.

## 5. Verification and Documentation

- [x] 5.1 Add shared contract tests for compilation context, evidence, guard ordering, stale identities, and sensitive-payload exclusion.
- [x] 5.2 Add SQL/MongoDB parity tests for capability mismatch, mandatory filters, bounds, authorization mismatch, and protected result lineage.
- [x] 5.3 Add security tests proving compiler output cannot grant authority or bypass policy/view/tenant restrictions.
- [x] 5.4 Update README and active specifications with the aligned boundary and evidence chain; run pytest, Ruff, and Mypy.
