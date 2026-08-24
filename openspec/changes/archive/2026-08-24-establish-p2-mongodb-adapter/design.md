## Context

The generic async-first adapter contract and P2.5 workflow runtime currently have a governed SQL specialization. MongoDB requires a document-aware specialization for nested dotted paths, BSON literals, read-only find operations, and aggregation pipelines, while the runtime and public result boundary must remain backend-neutral.

MongoDB support must be optional: importing `nl2data` or the core workflow must not import PyMongo, and an unavailable driver/service must produce a safe configuration or unavailable result rather than a false conformance pass.

## Goals / Non-Goals

**Goals:**

- Implement a structured, read-only MongoDB adapter for `find`, `aggregate`, and `count_documents`.
- Validate collections, nested fields, operators, stages, projections, sort, skip, and limit before execution.
- Normalize supported BSON/scalar values into the existing `ExecutionResult` contract.
- Bind adapter facts to existing Governance, Tenant scope, ExecutionAuthorization, Result Protection, and Workflow Runtime gates.
- Provide optional lazy PyMongo sync/async profiles and deterministic fake-driver tests.

**Non-Goals:**

- Writes, JavaScript, change streams, map-reduce, cross-database joins, Atlas Search/vector stages, automatic schema mutation, or arbitrary Mongo shell text.
- Making PyMongo types part of public or generic adapter contracts.
- Claiming MongoDB-compatible service support without a separate tested profile.

## Decisions

1. **Use typed MongoQuerySpec as the artifact input.** `find`, `aggregate`, and `count_documents` are represented by strict JSON-compatible Pydantic models. Raw shell/pipeline text is rejected; the adapter translates the validated spec into driver calls.

2. **Keep MongoDB as a generic QueryAdapter specialization.** The adapter implements the existing parse/validate/generate/estimate/execute/close lifecycle and exposes `adapter_type="mongodb"`, `query_language="mql"`, and explicit async capability. No MongoDB methods enter the core protocol.

3. **Make validation AST/spec based and allowlist driven.** Collections, paths, operators, pipeline stages, expressions, projections, and result limits are checked from typed structures. Unknown operators/stages and wildcard projections fail closed. Regex-only validation was rejected for nested document and aggregation semantics.

4. **Use lazy optional driver loading.** The base package contains models, validator, fake executor, and protocols; PyMongo is loaded only by the optional MongoDB profile. This preserves library import boundaries and enables CI without a MongoDB service.

5. **Normalize BSON conservatively.** Strings, numbers, booleans, nulls, and explicitly supported date/identifier representations are normalized to safe scalar forms. Unsupported or oversized values fail before public result construction; native cursors/documents never cross the adapter boundary.

6. **Separate metadata from execution.** Metadata discovery returns bounded authorized collection/field references with a snapshot fingerprint. It never samples or exposes raw values by default, and query validation requires current collection/field scope.

7. **Use controlled fake-driver conformance first.** Unit and integration tests run against deterministic fake collection behavior; optional real MongoDB tests are skipped/unavailable when driver or service is absent, never treated as passing.

## Risks / Trade-offs

- [Risk] BSON has richer types than the public scalar contract. → Normalize an explicit allowlist and reject unsupported values safely.
- [Risk] Aggregation pipelines can be computationally expensive. → Bound stages, expressions, documents, result bytes, and timeout; defer explain-based cost to a later change.
- [Risk] MongoDB driver behavior differs between sync and async clients. → Keep one typed spec and run the same conformance suite against each supported profile.
- [Risk] Tenant isolation in pooled collections requires mandatory tenant predicates. → Make tenant obligations explicit in query facts and fail closed when the adapter profile cannot enforce them.

## Migration Plan

1. Add typed MongoDB models, validator, fake driver, and optional dependency profile without changing SQL behavior.
2. Implement the generic adapter lifecycle and result normalization.
3. Integrate MongoDB query facts and tenant/governance authorization into the workflow runtime.
4. Add controlled fake-driver and optional real-service conformance tests.
5. Roll back by omitting the MongoDB extra/profile; core SQL and not-configured paths remain unchanged.

## Open Questions

- Which BSON identifier/date representations should be enabled in the first public adapter profile?
- Should aggregation `$lookup` remain rejected until cross-collection policy facts exist?
- Should the first MongoDB profile be async-native or thread-offloaded sync PyMongo?