## Context

The repository now has a canonical Semantic Query IR, bundle-backed Semantic Views, deterministic SQL/MongoDB compilers, artifact guards, governance evaluation, artifact-bound authorization, and protected results. These pieces work, but the boundary between them is represented by loosely related callables and evidence dictionaries. DDS-019 requires a single explicit chain that makes logical provenance, policy facts, physical artifact identity, authorization, resource bounds, and result protection auditable and impossible to bypass.

## Goals / Non-Goals

**Goals:**

- Define a shared immutable compiler context containing current IR, resolved view/bundle references, adapter capabilities, policy/tenant context, and effective limits.
- Define safe compilation and artifact-guard evidence that links logical IR to physical artifact without storing raw artifacts in governance evidence.
- Ensure compiler, artifact guard, governance, and authorization stages have one ordering and fail-closed contracts across SQL and MongoDB.
- Bind authorization to logical and physical identities plus policy, view/model, tenant, capability, and limits evidence.
- Preserve result protection and audit linkage through the final result envelope.

**Non-Goals:**

- New query languages, adapters, policy engines, or distributed execution.
- Allowing compilers to issue authorization or bypass adapter-specific guards.
- Replacing the existing QueryAdapter protocol or public facade.
- Implementing cost-based optimization, federation, streaming, or agent repair loops.

## Decisions

### Shared context, specialized compiler

Introduce a backend-neutral `CompilationContext` and compiler protocol accepting validated IR and context. SQL and MongoDB compilers retain specialized physical binding and artifact generation, but return common evidence and cannot make policy decisions. A shared context is preferred over duplicated compiler arguments because it gives governance one stable input contract.

### Governance evaluates facts, not physical syntax

Compilers and guards emit safe typed facts: source, operation, semantic fields, required capabilities, policy/view/model fingerprints, mandatory-filter fingerprints, bounds, and artifact identity. Governance consumes these facts and never parses or trusts compiler-generated authorization claims. Backend guards remain authoritative for SQL/MQL syntax and dangerous operations.

### Explicit stage ordering

The runtime order is:

```text
validate IR/view/bundle
  → compile
  → artifact parse/guard
  → governance facts/decision
  → execution authorization
  → bounded execute
  → result protection
  → evidence persistence
```

No adapter call is permitted before every preceding gate succeeds. Authorization is issued only after the artifact guard and governance decision, and is verified again immediately before execution.

### Distinct identities and complete evidence

Logical IR, resolved view/bundle, physical artifact, policy decision, authorization, and protected result each retain separate fingerprints. Evidence links them; it does not collapse them into one hash. Raw SQL/MQL, credentials, tenant IDs, result values, and native objects remain outside evidence.

### Compatibility through adapters, not bypasses

Existing compiler functions and adapter-specific guard models can be wrapped behind the new context/evidence contract during migration. The wrapper must still execute the same guard and authorization gates. A compiler that cannot produce required evidence is rejected as unsupported rather than silently receiving broader authority.

## Risks / Trade-offs

- [Common contract duplicates backend facts] → Keep facts minimal and typed; leave syntax validation to each specialized guard.
- [Evidence drift across SQL and MongoDB] → Add shared conformance fixtures and require the same identity fields for both compilers.
- [Authorization issued for stale artifact/view] → Verify all logical, policy, capability, tenant, and artifact fingerprints immediately before execution.
- [Additional stage overhead] → Use immutable references/fingerprints and avoid copying raw payloads; correctness takes priority over micro-optimizing the boundary.
- [Legacy callables bypass context] → Remove direct runtime paths after migration and add tests that fail when a compiler is called without validated context.

## Migration Plan

1. Add common context, facts, evidence, and compiler/guard contracts without changing public APIs.
2. Adapt SQL and MongoDB compilers and guards to emit/consume the shared contracts.
3. Update workflow runtime ordering and authorization verification to require the complete chain.
4. Update result/evidence persistence and cross-backend conformance tests.
5. Remove obsolete direct compiler/governance wiring after all active callers migrate.
6. Roll back by restoring existing compiler wrappers; no persisted raw artifact migration is required.

## Open Questions

- Whether common compilation evidence should be persisted as workflow stage metadata or through a dedicated audit port.
- Whether cost estimates belong before or after governance in a future optimizer-aware runtime.
- Which additional adapter facts are required when search, graph, or API compilers are introduced.
