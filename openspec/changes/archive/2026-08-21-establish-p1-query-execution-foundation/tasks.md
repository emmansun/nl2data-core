## 1. Contracts and Dependencies

- [x] 1.1 Add the scoped SQL parser and database dependencies, keeping SQLite available for the default local/test profile and PostgreSQL optional.
- [x] 1.2 Define immutable Semantic Query Plan, selection, ordering, lineage, physical-binding, and validation-error models with canonical fingerprints.
- [x] 1.3 Define typed governance decision, policy scope, mandatory-filter obligation, effective-limits, and artifact-bound ExecutionAuthorization models.
- [x] 1.4 Tighten `QueryOutcome` invariants so status and result/error payloads are consistent while preserving the public import boundary.

## 2. Governance Foundation

- [x] 2.1 Implement deterministic default-deny allow/deny evaluation for source, resource, operation, and field scope.
- [x] 2.2 Implement execution-authorization issuance and verification for adapter/source/operation, artifact fingerprint, effective limits, expiry, and mandatory-filter fingerprints.
- [x] 2.3 Add governance contract tests for missing inputs, explicit deny, artifact mismatch, expiry, and missing protected filters.

## 3. SQL Adapter Foundation

- [x] 3.1 Add SQL adapter package structure, dialect capability profiles, and generic `QueryAdapter` lifecycle implementation.
- [x] 3.2 Implement parsed SQL artifact and authoritative AST-based single-statement/read-only guard with object, column, and bounded-result checks.
- [x] 3.3 Implement Semantic Query Plan to SQL compilation for the first supported select, filter, grouping, ordering, and limit cases.
- [x] 3.4 Implement SQL execution with read-only connection policy, bounded row handling, safe error classification, scalar normalization, and protected `ExecutionResult` mapping.
- [x] 3.5 Add SQL adapter contract tests covering safe SELECT, write/DDL/multi-statement rejection, scope rejection, stable fingerprints, and unsupported native values.

## 4. Controlled SQL Fixtures

- [x] 4.1 Define versioned shared fixture schema, synthetic seed data, fixed clock/timezone, expected counts, reset strategy, and setup fingerprint.
- [x] 4.2 Implement deterministic SQLite fixture provisioning, reset, disposal, and expected-count verification.
- [x] 4.3 Add the optional PostgreSQL fixture profile using the shared logical schema, seed expectations, policy cases, and result assertions.
- [x] 4.4 Add fixture integration tests that distinguish passing, failing, skipped, and unavailable PostgreSQL profile outcomes.

## 5. Workflow and Protected Outcomes

- [x] 5.1 Implement the minimum workflow path from validated Semantic Query Plan through governance, authorization verification, SQL execution, and public outcome construction.
- [x] 5.2 Apply result protection before constructing `QueryResult`, including scalar-only rows, bounded columns/rows, field scope, and safe failure conversion.
- [x] 5.3 Preserve P0 lifecycle/readiness gating and retain the not-configured runner as the fallback when P1 workflow configuration is absent.
- [x] 5.4 Add end-to-end integration tests for successful query, validation rejection, governance denial, protected result, and lifecycle failure paths.

## 6. Evaluation Runner

- [x] 6.1 Define evaluation dataset/case/fixture/run/context/result/report models required by the P1 runner skeleton.
- [x] 6.2 Implement fixture lifecycle orchestration with case isolation, fixed clock/seed binding, reset/disposal, and safe cleanup on failures.
- [x] 6.3 Implement protected evidence collection and mandatory assertion execution without exposing fixture credentials, native clients, raw prompts, or unrestricted errors to scorers.
- [x] 6.4 Implement deterministic JSON report output with pass, fail, skipped, and unavailable states and independent mandatory-assertion failures.
- [x] 6.5 Add runner tests for cleanup, evidence redaction, mandatory security failures, and repeatable case results.

## 7. Conformance and Quality Gates

- [x] 7.1 Add SQLite end-to-end conformance cases covering plan fingerprint, SQL artifact, governance authorization, protected outcome, and evaluation evidence.
- [x] 7.2 Add optional PostgreSQL conformance execution and document the required service/driver profile without treating unavailable integration as a pass.
- [x] 7.3 Run the complete existing P0 suite plus P1 contract, unit, integration, security, and evaluation tests; fix only regressions caused by this change.
- [x] 7.4 Run formatting, lint, type, and package-install checks for the supported dependency profiles.