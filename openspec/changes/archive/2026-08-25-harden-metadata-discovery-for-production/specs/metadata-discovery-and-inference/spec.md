## MODIFIED Requirements

### Requirement: Metadata discovery is bounded, scoped, and provider-neutral
The system SHALL define a replaceable metadata discovery capability that returns immutable, versioned snapshots containing only authorized structural metadata, normalized types/constraints, bounded protected statistics, source/catalog fingerprints, freshness, completeness/partial status, and safe provenance. Production discovery SHALL require an explicit trusted source/tenant authorization context and read-only discovery identity. It SHALL never return credentials, connection strings, native driver objects, raw rows/documents, or unrestricted sample values.

#### Scenario: Discovery returns a safe snapshot
- **WHEN** an authorized discoverer inspects a configured source within object, field, size, sample, timeout, and concurrency bounds
- **THEN** it returns a canonical snapshot of safe metadata and protected references without raw data values

#### Scenario: Discovery bounds are exceeded
- **WHEN** a source contains more objects, fields, nested paths, or statistics than the configured limits permit
- **THEN** discovery records explicit bounded/partial status or fails safely, never performing unbounded work

#### Scenario: Unauthorized source is denied
- **WHEN** discovery is requested without trusted source/tenant authorization or outside the configured allowlist
- **THEN** discovery returns a safe denial and does not reveal source metadata

### Requirement: Snapshot drift is detectable and fail-closed
The system SHALL compare compatible metadata snapshots by canonical identity and report bounded safe added, removed, and changed object/field/constraint references. Canonical serialization SHALL be independent of object-internal and provenance evidence iteration order. Production drift policy SHALL classify changes by severity and block activation/resolution for referenced removals, incompatible type or constraint changes, source identity changes, expired freshness, and incompatible catalog changes by default. Bundle/View/IR consumers SHALL reject stale snapshot references when required compatibility fingerprints no longer match.

#### Scenario: Schema drift produces safe changes
- **WHEN** a new snapshot changes a field type or removes a referenced object
- **THEN** comparison reports the changed semantic reference without exposing raw values, and dependent activation/resolution is blocked by default

#### Scenario: Equivalent snapshots are stable
- **WHEN** the same metadata is discovered in a different backend mapping order
- **THEN** canonical serialization and snapshot fingerprint remain identical

#### Scenario: Non-breaking additions remain reportable
- **WHEN** a new authorized snapshot adds an unreferenced field
- **THEN** the change is classified as non-blocking information and existing Bundle/View identity remains unchanged
