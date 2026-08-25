# metadata-production-readiness Specification

## Purpose
Define production operating rules, health evidence, drift policy, snapshot lifecycle, and real-source verification for metadata discovery.

## Requirements

### Requirement: Production discovery authorization is explicit
A production metadata discovery run SHALL require a trusted source/tenant authorization context, configured object and field allowlists, a read-only discovery identity, and bounded resource settings. Discovery authorization SHALL remain separate from query execution authorization, and the returned snapshot source SHALL match the authorized source identity.

#### Scenario: Missing discovery authority fails closed
- **WHEN** a discovery run has no trusted source or tenant authorization
- **THEN** it is denied before metadata is read and no snapshot becomes active

#### Scenario: Discovery cannot widen its allowlist
- **WHEN** a source returns objects or fields outside the configured discovery allowlist
- **THEN** those members are excluded and the snapshot records a bounded omission indicator without exposing unauthorized metadata

#### Scenario: Returned source mismatch fails closed
- **WHEN** a discoverer returns a snapshot whose source identity differs from the trusted authorization
- **THEN** the result is classified unauthorized and no snapshot is returned for activation

### Requirement: Snapshot lifecycle and activation are explicit
Production snapshots SHALL have immutable identity, source/catalog fingerprint, freshness, completeness/partial status, retention metadata, and an explicit active/inactive state. Unavailable, unauthorized, stale, or incomplete snapshots SHALL NOT become active by default. A source/tenant lifecycle ledger SHALL maintain at most one active snapshot for each source and tenant scope.

#### Scenario: Partial snapshot remains inactive
- **WHEN** discovery reaches an object, field, sample, or timeout bound and produces a partial snapshot
- **THEN** the snapshot may be retained as evidence but activation is rejected unless an explicit compatible policy permits it

#### Scenario: Failed discovery preserves active snapshot
- **WHEN** a new discovery run is unavailable or unauthorized
- **THEN** no partial replacement occurs and the previous active snapshot remains unchanged

#### Scenario: Activation replaces the prior snapshot
- **WHEN** a new compatible snapshot is activated for the same source and tenant scope
- **THEN** the previous snapshot becomes inactive and only the new snapshot is returned as active

### Requirement: Drift policy is severity-based and fail-closed
The production profile SHALL classify snapshot changes as informational, warning, or blocking. Removal/type changes of referenced fields, source identity changes, constraint or relationship removals, expired freshness, and incompatible catalog changes SHALL be blocking by default. Every decision SHALL expose only safe references and a decision fingerprint. Overrides are explicit, bounded, tenant/source scoped, and auditable.

#### Scenario: Referenced field removal blocks activation
- **WHEN** a new snapshot removes a field used by an active Bundle or View
- **THEN** the drift decision blocks activation and dependent IR/workflow use until reviewed and republished

#### Scenario: Non-breaking field addition is reportable
- **WHEN** a new snapshot adds an unreferenced field within the authorized allowlist
- **THEN** the change is reported as non-blocking information and does not alter existing Bundle/View identity

### Requirement: Discovery operations are observable without leakage
Production discovery SHALL report bounded duration, object/field counts, truncation/partial status, freshness, outcome category, snapshot fingerprint, and drift decision references. It SHALL NOT report DSNs, credentials, raw rows/documents, raw sampled values, or unrestricted sensitive names.

#### Scenario: Safe discovery evidence is emitted
- **WHEN** an authorized discovery completes
- **THEN** operational evidence contains bounded counts and fingerprints but no raw source payload or credentials

#### Scenario: Backend failure is normalized
- **WHEN** the source times out, rejects metadata access, or becomes unavailable
- **THEN** the profile returns a classified retryable or non-retryable result and does not activate a partial snapshot

### Requirement: Production support requires real-source verification
The production profile SHALL include integration tests against supported real SQL/PostgreSQL and MongoDB services, with isolated test namespaces/data, health checks, bounded timeouts, cleanup, and explicit unavailable-service handling. A skipped real-service profile SHALL NOT be reported as verified production support.

#### Scenario: Real discovery profile verifies common facts
- **WHEN** PostgreSQL and MongoDB service containers are healthy and discovery runs with authorized allowlists
- **THEN** both produce safe common snapshots and backend-specific differences are asserted

#### Scenario: Service absence is explicit
- **WHEN** a required real service cannot start or respond
- **THEN** the integration result is classified unavailable/failed according to workflow policy and is not silently counted as a passing verification
