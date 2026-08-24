## 1. Structured MongoDB Contracts

- [x] 1.1 Define strict JSON-compatible MongoDB query specs for `find`, `aggregate`, and `count_documents`, including nested paths, filters, projection, sort, skip, and limit.
- [x] 1.2 Define MongoDB dialect/profile capabilities, adapter configuration, metadata snapshot, query facts, and safe adapter errors.
- [x] 1.3 Add optional `pymongo` dependency/profile with lazy imports and base-package import-boundary tests.
- [x] 1.4 Add contract tests for immutable specs, unknown fields, unsupported operations, stable fingerprints, and optional-driver absence.

## 2. MQL Validation and Compilation

- [x] 2.1 Implement deterministic normalization and fingerprinting for structured specs without shell text or JavaScript execution.
- [x] 2.2 Implement collection, nested-field, operator, projection, stage, expression, sort, skip, limit, and result-bound validation.
- [x] 2.3 Implement tenant obligation validation for pooled predicates and schema/database/deployment routing evidence.
- [x] 2.4 Implement structured spec to controlled PyMongo driver-call mapping behind a driver-neutral executor port.
- [x] 2.5 Add security tests for writes, admin commands, JavaScript, `$where`, unapproved stages/operators, wildcard access, and unbounded queries.

## 3. Adapter Lifecycle and Results

- [x] 3.1 Implement MongoDB adapter capabilities and generic `QueryAdapter` lifecycle with native or thread-offload async profile.
- [x] 3.2 Implement lazy client lifecycle, connection/readiness checks, close idempotency, and unavailable-driver/service errors.
- [x] 3.3 Implement bounded `find`, `aggregate`, and `count_documents` execution with timeout, document, column, and result-byte limits.
- [x] 3.4 Implement conservative BSON normalization into scalar `ExecutionResult` rows and safe unsupported-value failures.
- [x] 3.5 Add adapter contract tests for parse/validate/generate/estimate/execute/close and protected result mapping.

## 4. Metadata and Governance Integration

- [x] 4.1 Implement bounded collection/field metadata discovery with canonical dotted paths and metadata snapshot fingerprints.
- [x] 4.2 Implement MongoDB query-fact extraction for collections, fields, operators, stages, result shape, and tenant obligations.
- [x] 4.3 Integrate facts with Governance, Tenant scope, ExecutionAuthorization, Result Protection, and P2.5 runtime gates.
- [x] 4.4 Add tests proving unauthorized collection/field and missing tenant obligation stop before driver execution.

## 5. Controlled Fixtures and Conformance

- [x] 5.1 Define deterministic fake MongoDB driver/collection fixtures sharing logical cases with SQLite where possible.
- [x] 5.2 Add conformance cases for find, aggregate, count, invalid constructs, BSON normalization, limits, tenant isolation, and safe evidence.
- [x] 5.3 Add optional real MongoDB integration profile with explicit skipped/unavailable outcomes when driver/service is absent.
- [x] 5.4 Add protected SQL/Mongo logical result-equivalence tests for shared controlled fixture cases.

## 6. Quality Gates and Documentation

- [x] 6.1 Run complete P0–P2.5 tests plus MongoDB contract, security, integration, Ruff, Mypy, and package-install checks.
- [x] 6.2 Document optional MongoDB installation, supported operations, safe defaults, driver/service availability, and deferred capabilities.
- [x] 6.3 Verify no MongoDB native types or optional imports enter the public `nl2data` API or framework-neutral workflow contracts.