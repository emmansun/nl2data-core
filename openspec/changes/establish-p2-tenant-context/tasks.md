## 1. Trusted Context Contracts

- [x] 1.1 Define immutable `SubjectContext`, `TenantContext`, delegation, entitlement revision, isolation profile, and tenant lifecycle models.
- [x] 1.2 Implement trusted-context validation that rejects missing, inactive, conflicting, unknown, or unsupported tenant scope.
- [x] 1.3 Implement canonical scope payloads, deterministic fingerprints, safe serialization, and tenant-scoped namespace/key helpers.
- [x] 1.4 Add contract tests for immutability, equivalent/different scope fingerprints, delegation, profile validation, and safe dumps.

## 2. Governance and Authorization Propagation

- [x] 2.1 Extend typed governance facts and policy scope with optional tenant profile and required tenant scope behavior.
- [x] 2.2 Extend `ExecutionAuthorization` issuance and verification with tenant scope fingerprint and isolation profile binding.
- [x] 2.3 Reject tenant scope mismatches before adapter execution and preserve non-tenant single-local-fixture compatibility.
- [x] 2.4 Add governance tests for missing tenant context, cross-tenant artifact reuse, delegated scope, and unsupported isolation profiles.

## 3. Workflow and Public Context Propagation

- [x] 3.1 Add trusted tenant context injection to the workflow execution composition without treating `QueryRequest` body or prompt claims as authority.
- [x] 3.2 Propagate safe tenant scope references through workflow state, evidence, and future cache namespace primitives without persisting raw identity claims.
- [x] 3.3 Update public context models/imports only with bounded opaque references, preserving the existing public import boundary and safe outcome contract.
- [x] 3.4 Add integration tests proving same-tenant success, missing-context denial, and client-claim mismatch denial.

## 4. Tenant Isolation Conformance

- [x] 4.1 Define deterministic tenant conformance cases for positive propagation, cross-tenant reuse, inactive tenant, delegation, and namespace separation.
- [x] 4.2 Implement protected conformance evidence and reports containing fingerprints/profile metadata but no raw tenant IDs, credentials, or tokens.
- [x] 4.3 Add adversarial tests for conflicting client tenant hints and authorization/context fingerprint mismatches.

## 5. Quality Gates and Compatibility

- [x] 5.1 Verify existing P0/P1/P2.1 tests remain green for non-tenant local composition.
- [x] 5.2 Run tenancy contract, security, integration, Ruff, Mypy, and package-install checks.
- [x] 5.3 Document that authentication, durable state, Memory, HTTP authentication, and cryptographic trust establishment remain host-integration responsibilities.