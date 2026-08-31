## MODIFIED Requirements

### Requirement: Publish makes fingerprint authoritative atomically
The system SHALL make a semantic bundle fingerprint externally authoritative only during publish. A candidate Bundle MAY precompute the same deterministic fingerprint for validation, but it SHALL NOT be treated as published authority before the atomic catalog operation succeeds. Publish SHALL freeze the approved draft and its approved verification plan, run the verification policy required for the target profile, require every mandatory layer and case to pass, compute canonical semantic bytes, derive an immutable accepted-assertion manifest, verify that the manifest and emitted Bundle represent the same accepted semantic content, perform duplicate-content detection, persist the immutable published bundle and linked manifest, persist the publish audit and verification evidence summary, and update supersession metadata atomically. Plan content and verification evidence SHALL remain outside the Bundle semantic fingerprint domain but SHALL be bound to the approved revision and publication audit.

#### Scenario: Publish creates immutable semantic artifact
- **WHEN** an approved draft and plan pass every layer required by the selected verification policy and are published
- **THEN** the resulting published bundle has a `sha256:` semantic fingerprint and the draft remains distinct from the immutable published artifact

#### Scenario: Publish failure leaves no partial artifact
- **WHEN** required verification fails, is skipped/unavailable/timed out/not run, or fingerprint computation, catalog persistence, audit persistence, or supersession update fails
- **THEN** no externally visible partial publication exists and the draft remains approved for retry

#### Scenario: Same content and verification publish is idempotent
- **WHEN** the same approved semantic payload and bound verification plan are published more than once after equivalent passing verification
- **THEN** the system returns the existing published bundle fingerprint, verification evidence reference, and audit reference without creating duplicate immutable artifacts

#### Scenario: Published baseline retains assertion alignment
- **WHEN** an approved draft is published successfully
- **THEN** its accepted assertion IDs, types, canonical payloads, and payload hashes are persisted in an immutable manifest linked to the published fingerprint without entering the Bundle fingerprint domain

#### Scenario: Manifest mismatch blocks publication
- **WHEN** the accepted-assertion manifest and emitted Bundle do not represent the same frozen approved semantic content
- **THEN** Layer 1 and publish fail closed and persist no Bundle, manifest, verification evidence, audit record, or supersession edge

## ADDED Requirements

### Requirement: Verification plan changes invalidate assembly approval
A verification plan attached to an assembly draft SHALL be immutable, bounded, and included in the lifecycle approval binding while remaining excluded from semantic Bundle fingerprints. Adding, removing, enabling, disabling, or editing a case, assertion, deadline, fixture/deployment profile, or policy profile SHALL advance the draft revision and invalidate prior assembly approval. Publish SHALL verify the plan frozen with the exact approved revision.

#### Scenario: Approved plan edit requires reapproval
- **WHEN** any verification-plan content changes after draft approval
- **THEN** the draft returns to review or otherwise loses publish eligibility until the new plan and semantic content are approved and verified

#### Scenario: Verification does not mutate approved content
- **WHEN** a suite runs against an approved draft
- **THEN** only external verification evidence is produced; the draft, assertions, plan, and candidate Bundle remain immutable
