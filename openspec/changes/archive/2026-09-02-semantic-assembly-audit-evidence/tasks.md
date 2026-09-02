## 1. Characterization and Contract Tests

- [x] 1.1 Add failing core model tests for audit-evidence entry identity, event kind validation, subject references, predecessor links, safe outcome/status fields, entry fingerprints, and deterministic ordering.
- [x] 1.2 Add lifecycle tests proving review, approval, lint-reference, verification-reference, publication, activation, and rollback evidence entries remain outside semantic Bundle fingerprints.
- [x] 1.3 Add publication aggregate tests for audit-evidence cross-link validation against approved draft revision, manifest, verification evidence, policy profile, tenant/source scope, separation-of-duties result, and Bundle fingerprint.
- [x] 1.4 Add Admin contract/schema tests for bounded audit-evidence inspection DTOs and capability advertisement.
- [x] 1.5 Add PostgreSQL/in-memory catalog tests for audit-evidence envelope persistence, reload, tamper rejection, and scoped bounded lookup.

## 2. Core Audit-Evidence Contracts

- [x] 2.1 Define core audit-evidence enums and models for event kind, subject kind/reference, outcome/status, operator audit reference, predecessor references, payload bindings, and trail summaries.
- [x] 2.2 Implement canonical payload and fingerprint helpers for audit-evidence entries, excluding duration and presentation metadata while rejecting unsafe values.
- [x] 2.3 Implement deterministic trail ordering, bounded result counts, cursor metadata primitives, and safe redaction/truncation behavior.
- [x] 2.4 Add public exports for audit-evidence contracts from the canonical core control-plane/assembly surface without importing Admin or optional persistence modules.
- [x] 2.5 Update architecture conformance allowlists/manifests so audit-evidence contracts have one canonical owner and optional packages depend inward only.

## 3. Lifecycle and Publication Integration

- [x] 3.1 Create audit-evidence entries for authoring/import and assertion review/edit/approval actions using existing lifecycle revision and payload-hash facts.
- [x] 3.2 Link semantic assembly lint summaries or references into lifecycle/release readiness evidence when a lint result is supplied, without making lint a publish authority.
- [x] 3.3 Extend publication aggregate construction to include publication audit-evidence bindings for approved draft revision, plan fingerprint, manifest fingerprint, Verification Suite evidence, policy profile, tenant/source scope, separation-of-duties result, publish audit reference, and Bundle fingerprint.
- [x] 3.4 Fail closed before catalog persistence when publication audit evidence mismatches any immutable publication aggregate identity.
- [x] 3.5 Create activation and rollback audit-evidence entries that link prior/current active fingerprints and predecessor publication evidence without republishing semantic content.
- [x] 3.6 Preserve legacy publication compatibility by explicitly classifying records missing complete audit-evidence entries rather than fabricating production-valid evidence.

## 4. Catalog Persistence

- [x] 4.1 Extend core catalog ports to persist and query bounded audit-evidence entries by draft, assertion, Bundle fingerprint, publication, activation, rollback, and predecessor reference.
- [x] 4.2 Implement in-memory catalog storage, scoped lookup, deterministic ordering, bounded limits, and tamper validation for audit-evidence entries.
- [x] 4.3 Add PostgreSQL schema migration for audit-evidence envelopes, indexes for scoped subject lookup, predecessor links, and active-publication retention dependencies.
- [x] 4.4 Implement PostgreSQL repository/unit-of-work methods that persist audit evidence atomically with publication, activation, and rollback records.
- [x] 4.5 Validate durable audit-evidence envelopes on read against schema version, fingerprint, tenant/source scope, immutable publication records, accepted manifest, Verification Suite evidence, publish audit, frozen release binding, and Bundle fingerprint.
- [x] 4.6 Extend retention/cleanup logic so active publications, active pointers, supersession chains, rollback targets, and retained publish audit records keep required audit-evidence entries.

## 5. Admin Inspection API

- [x] 5.1 Add Admin request/result DTOs for audit-evidence inspection by draft ID/revision range, assertion ID, Bundle fingerprint, publication reference, activation reference, rollback reference, and cursor/limit.
- [x] 5.2 Add capability service methods that enforce trusted auth context, tenant/source scope, read/audit permissions, subject validation, bounds, and safe normalized errors.
- [x] 5.3 Wire `AdminService` compatibility facade methods and generated schema/capability output for audit-evidence inspection.
- [x] 5.4 Add Admin tests for side-effect-free inspection, cross-scope denial, missing permission, stale/unknown subject handling, cursor bounds, and redacted response fields.
- [x] 5.5 Ensure audit inspection never creates lifecycle events, lint results, verification evidence, publish audit records, publications, activations, rollbacks, or retention changes.

## 6. Documentation and Validation

- [x] 6.1 Document the assembly audit-evidence trail, event kinds, subject references, redaction rules, retention expectations, and relationship to lint, Verification Suite, publish audit, activation, and rollback.
- [x] 6.2 Update operations/troubleshooting documentation for tampered or missing audit-evidence envelopes and legacy compatibility classification.
- [x] 6.3 Run focused core lifecycle/publication/audit-evidence unit tests.
- [x] 6.4 Run focused Admin contract/security tests and PostgreSQL catalog persistence tests for touched surfaces.
- [x] 6.5 Run `ruff check` for touched source and tests.
- [x] 6.6 Run the relevant type-check command for touched core, Admin, and catalog modules.
- [x] 6.7 Run `openspec validate semantic-assembly-audit-evidence --strict` and resolve any spec/task issues.