## MODIFIED Requirements

### Requirement: Bundle lifecycle mutations are guarded
The service SHALL expose validation, three-layer verification, publish, activate, active lookup, version listing, verification/audit lookup, and rollback commands that delegate structural, dependency, fingerprint, scope, freshness, completeness, drift, review-binding, verification-policy, runner/evidence identity, publish atomicity, and idempotency checks to the core/catalog. Verification and mutations SHALL require the expected draft revision or published fingerprint context, idempotency where applicable, and authorized lifecycle permission. Admin SHALL not convert skipped, unavailable, timed-out, missing, or not-run verification into success and SHALL not implement a competing verifier.

#### Scenario: Invalid activation preserves current state
- **WHEN** activation fails validation, compatibility, authorization, freshness, drift, or required verification-evidence checks
- **THEN** the service reports a safe rejection and the current active Bundle remains unchanged

#### Scenario: Rollback keeps immutable history
- **WHEN** an authorized operator rolls back to a previously valid version
- **THEN** the active pointer changes atomically and both old and new Bundle artifacts and their verification/audit references remain immutable and retrievable by fingerprint

#### Scenario: Publish delegates lifecycle enforcement
- **WHEN** a host requests publish for an approved assembly draft
- **THEN** the service delegates pending-assertion checks, frozen-plan verification, fingerprint computation, verification/audit creation, idempotency, and supersession updates to core/catalog and returns only the bounded outcome

## ADDED Requirements

### Requirement: Admin service exposes safe verification operations
The Admin service SHALL expose an authorized side-effect-free `verify_draft` operation and verification-evidence inspection. Verification SHALL require Assembly verification permission, trusted tenant/source scope, expected draft revision, selected policy profile, and configured executor capabilities. Results SHALL contain bounded plan/layer/case statuses, counts, issue codes, and evidence fingerprints only. Verification SHALL not approve, publish, activate, modify a draft, or expose raw queries, results, physical names, credentials, deployment references, or backend exceptions.

#### Scenario: Verification has no lifecycle side effect
- **WHEN** an authorized operator verifies an approved or review-state draft revision
- **THEN** the service returns bounded suite evidence and the stored draft state/revision/content remain unchanged

#### Scenario: Missing capability is safe failure
- **WHEN** the required fixture, adapter, executor, secret resolver, or verification permission is unavailable
- **THEN** Admin returns a safe unavailable/denied result and no publication occurs

#### Scenario: Audit inspection exposes layer outcome
- **WHEN** an authorized operator inspects a published verification record
- **THEN** the response identifies policy/plan/runner versions, each layer status and counts, and the suite evidence fingerprint without exposing case values or connection material
