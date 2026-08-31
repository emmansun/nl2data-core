## MODIFIED Requirements

### Requirement: Catalog publication is atomic and replaceable
The system SHALL define a replaceable catalog protocol and implementations supporting immutable publish by semantic fingerprint, lookup by bundle name/fingerprint and business version metadata, active snapshot lookup, atomic activation, supersession-chain traversal, and rollback only to a previously published valid bundle. Governed assembly publication SHALL validate the configured verification policy and require passing evidence bound to the exact plan, approved draft revision, manifest, candidate Bundle, tenant/source context, runner identity, and executor identities before making a bundle available. It SHALL persist the immutable artifact, manifest, verification summary/reference, publish audit, and supersession update atomically, SHALL be idempotent for identical semantic content and equivalent bound evidence, and SHALL never expose a partial bundle. The Admin API SHALL delegate these decisions to the core/catalog and SHALL not implement a competing verification or active pointer.

#### Scenario: Invalid bundle remains unpublished and inactive
- **WHEN** publication validation or any required verification layer fails, is unavailable, skipped, timed out, or not run
- **THEN** the catalog does not publish or activate the bundle, creates no partial verification/audit record, and returns structured issues

#### Scenario: Duplicate semantic content is idempotent
- **WHEN** an approved assembly draft publishes semantic content whose fingerprint already exists for the bundle name with an equivalent accepted verification plan/evidence binding
- **THEN** the catalog returns the existing immutable publication and its verification/audit references without creating a duplicate artifact

#### Scenario: Activation switches complete snapshots
- **WHEN** a valid verified published bundle is activated
- **THEN** subsequent View resolutions observe the complete new snapshot, while existing evidence continues to identify the previous bundle fingerprint

#### Scenario: Rollback selects an immutable version
- **WHEN** an operator rolls back to a previously published valid fingerprint
- **THEN** the catalog changes the active pointer to that version without mutating either bundle artifact, verification evidence, or assigning a new fingerprint

## ADDED Requirements

### Requirement: Verification lifecycle data stays outside semantic identity
Verification plans, case definitions, runner/executor identities, statuses, durations, protected result references, issue codes, and suite evidence SHALL NOT enter the Bundle canonical semantic payload or semantic fingerprint. They SHALL be lifecycle evidence bound by fingerprints in the publish audit. Changing only verification lifecycle data SHALL require renewed approval/verification according to policy but SHALL NOT change otherwise identical Bundle semantic identity.

#### Scenario: Verification plan change preserves semantic fingerprint
- **WHEN** two approved candidates have identical semantic payload but different verification plans
- **THEN** their candidate Bundle semantic fingerprints are equal while their plan and verification evidence fingerprints differ
