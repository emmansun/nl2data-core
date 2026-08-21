## Context

P0 provides the public engine boundary, immutable public models, the generic async-first `QueryAdapter` protocol, and replaceable workflow state primitives. The repository does not yet have a semantic plan, a concrete adapter, a controlled database fixture, cross-adapter governance decision, or an executable evaluation loop.

The P1 design must connect these existing boundaries without turning the core protocol into a SQL API. DDS-004 defines the backend-neutral Semantic Query Plan, DDS-005 defines SQL-specific parsing and read-only validation, DDS-011 defines artifact-bound execution authorization and result protection, and DDS-014 defines reproducible fixtures and evaluation runners.

## Goals / Non-Goals

**Goals:**

- Establish one deterministic, immutable query path from a structured Semantic Query Plan to a protected public outcome.
- Keep semantic planning, SQL syntax validation, governance, execution, and evaluation as separate ownership boundaries.
- Use SQLite for zero-service local and CI execution, with a shared PostgreSQL conformance profile that is optional at runtime.
- Make read-only and single-statement checks independent from model or caller behavior.
- Make execution approval specific to one artifact fingerprint and bounded by effective limits.
- Provide enough fixture and runner infrastructure for repeatable contract and integration tests.

**Non-Goals:**

- Full natural-language intent resolution or LLM integration.
- Complete semantic catalog compilation, metric dependency expansion, or cross-source planning.
- Production-grade policy language, identity integration, cost estimation, streaming, repair loops, or distributed execution.
- Treating SQLite as evidence of complete PostgreSQL dialect compatibility.

## Decisions

1. **Use a backend-neutral Semantic Query Plan.** The plan references semantic IDs, source and policy/catalog fingerprints, bounded selections, filters, ordering, and lineage. It does not contain SQL AST nodes or database-driver values. This preserves future MongoDB and non-SQL adapter compatibility. A SQL-shaped intermediate representation was rejected because it would make SQL syntax the accidental core contract.

2. **Implement SQL as a specialization of the existing generic adapter.** The SQL adapter exposes the canonical `capabilities`, `parse`, `validate`, `generate`, `estimate_cost`, `execute`, and `close` lifecycle. SQL-specific parsed artifacts and guard results remain in the SQL package. A second SQL-only protocol was rejected because it would violate the P0 adapter contract and duplicate lifecycle semantics.

3. **Use a mature SQL parser for authoritative validation.** The guard validates exactly one statement, read-only operation shape, object/column scope, and bounded result behavior from a parsed representation. Regex may perform cheap envelope prechecks only. A regex-only validator was rejected because comments, nested queries, CTEs, and dialect syntax make it unsafe as an authority.

4. **Make SQLite the default controlled fixture and PostgreSQL an optional profile.** Both profiles share schema, seed data, semantic plan cases, policy cases, and expected protected results. SQLite keeps local and CI setup deterministic; PostgreSQL tests real dialect and read-only behavior when an external service is available. SQLite-only conformance was rejected because it cannot establish PostgreSQL compatibility.

5. **Keep governance adapter-neutral and minimal.** P1 supports typed default-deny decisions, explicit allow/deny resource scope, and mandatory filter fingerprints. The SQL adapter emits validated facts but does not interpret identity or business policy. A policy language implementation was rejected for P1 because it would expand the change beyond the execution foundation.

6. **Bind execution authorization to canonical identity.** An authorization contains policy/configuration scope, adapter/source, artifact fingerprint, effective limits, and expiry. The executor rejects mismatches and never broadens authorization. Reusable unbound decisions were rejected because a modified query could otherwise reuse an earlier approval.

7. **Protect results at the public boundary.** Adapter-native rows are normalized into scalar-only `QueryResult` values, then governance output rules are applied before constructing `QueryOutcome`. Native cursors, connections, driver values, and raw workflow state never cross the public boundary.

8. **Use deterministic evaluation stubs.** The runner provisions or resets a fixture, executes a case, collects only protected evidence, runs mandatory assertions, and resets resources. It does not expose fixture credentials or native clients to scorers. Real LLM/provider evaluation is deferred until the deterministic contract is stable.

## Risks / Trade-offs

- [Risk] SQLite behavior may differ from PostgreSQL in quoting, functions, transaction read-only enforcement, and planner behavior. → Mitigate with shared cases plus an explicitly optional PostgreSQL conformance profile and capability declarations.
- [Risk] A minimal governance skeleton may not cover complex tenant, classification, or masking policy. → Keep the authorization contract extensible and fail closed for unsupported obligations; defer policy language to a later change.
- [Risk] Public result scalar validation can reject useful driver-native types. → Normalize only through an explicit adapter mapper and record unsupported values as safe structured failures.
- [Risk] Tightened `QueryOutcome` invariants may expose incomplete existing fake workflow implementations. → Update contract fixtures and preserve the existing public import/API shape.
- [Risk] Optional SQL parser and database dependencies can make installation profiles inconsistent. → Keep SQLite and parser dependencies scoped to the SQL/evaluation extras and test dependency availability in setup checks.

## Migration Plan

1. Add the new contracts and tests without changing existing P0 import names.
2. Add the SQLite fixture and run the P0 suite plus the new P1 contract suite.
3. Enable the PostgreSQL profile only when its service and driver are present; mark unavailable profile tests as skipped, not passed.
4. Wire the concrete workflow runner behind the existing `WorkflowExecutionPort`.
5. Roll back by disabling the P1 workflow binding and removing the optional SQL/evaluation extras; P0's not-configured runner remains the fallback.

## Open Questions

- Which SQL parser library and version should become the supported compatibility baseline?
- Should PostgreSQL integration use a local container, a developer-managed service, or both?
- Which minimal physical-binding representation is sufficient for compiling the first Semantic Query Plan cases?
- Should result transformations be limited to removal/nulling in P1, with masking and tokenization deferred?