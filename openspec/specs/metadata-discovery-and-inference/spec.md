# metadata-discovery-and-inference Specification

## Purpose
Define safe, bounded metadata discovery, deterministic semantic inference, review, and schema drift handling without granting authorization.

## Requirements

### Requirement: Metadata discovery is bounded, scoped, and provider-neutral
The system SHALL define a replaceable metadata discovery capability that returns immutable, versioned snapshots containing only authorized structural metadata, normalized types/constraints, bounded protected statistics, source/catalog fingerprints, freshness, completeness/partial status, and safe provenance. Production discovery SHALL require an explicit trusted source/tenant authorization context and read-only discovery identity. It SHALL never return credentials, connection strings, native driver objects, raw rows/documents, or unrestricted sample values.

#### Scenario: Discovery returns a safe snapshot
- **WHEN** an authorized discoverer inspects a configured source within object, field, size, sample, and timeout bounds
- **THEN** it returns a canonical snapshot of safe metadata and protected references without raw data values

#### Scenario: Discovery bounds are exceeded
- **WHEN** a source contains more objects, fields, nested paths, or statistics than the configured limits permit
	- **THEN** discovery records explicit bounded/partial status or fails safely, never performing unbounded work

#### Scenario: Unauthorized source is denied
- **WHEN** discovery is requested without trusted source/tenant authorization or outside the configured allowlist
- **THEN** discovery returns a safe denial and does not reveal source metadata

### Requirement: Metadata facts carry trust and provenance
Every discovered or generated fact SHALL be classified as `declared`, `observed`, or `inferred` and SHALL carry bounded evidence references, method/source metadata, confidence where applicable, and snapshot identity. Inferred facts SHALL remain non-authoritative until explicitly approved.

#### Scenario: Inference is distinguishable
- **WHEN** a relationship or measure is generated from naming/type/statistical heuristics
- **THEN** the proposal identifies it as inferred with bounded confidence and evidence, separate from declared facts

#### Scenario: Inference cannot grant access
- **WHEN** an inferred field classification or relationship is present in a snapshot or proposal
- **THEN** it cannot independently grant Semantic View visibility, tenant access, mandatory filters, or execution authorization

### Requirement: SQL and MongoDB discovery normalize to common facts
SQL and MongoDB discovery implementations SHALL map backend metadata into the common snapshot contract while preserving explicit backend capabilities and semantic differences. MongoDB dynamic paths SHALL be marked as observed/inferred according to their evidence and SHALL not be treated as complete schema declarations.

#### Scenario: SQL metadata is normalized
- **WHEN** a SQL discoverer reads tables, columns, types, keys, and bounded safe statistics
- **THEN** the result uses common object/field/constraint facts with a stable catalog fingerprint

#### Scenario: MongoDB observed paths are bounded
- **WHEN** a MongoDB discoverer inspects an allowed collection and bounded document structure
- **THEN** it returns canonical dotted paths without raw values and records that the observation may be incomplete

### Requirement: Semantic proposals are reviewed before bundle publication
The system SHALL generate bounded semantic proposals for entities, fields, types, relationships, grains, measures, synonyms, and classifications with trust/provenance metadata. Only explicitly approved proposals SHALL be eligible for conversion into an active Semantic Model Bundle, and each proposal set SHALL bind every proposal to its declared source snapshot fingerprint.

#### Scenario: Approved proposal becomes bundle input
- **WHEN** a reviewer approves a valid proposal set against a compatible snapshot
- **THEN** the resulting Bundle input preserves proposal provenance and approved trust markers

#### Scenario: Unreviewed proposal remains inactive
- **WHEN** a proposal is inferred or observed but has not been approved
- **THEN** it cannot be published as active semantic model authority or used to grant View access

### Requirement: Snapshot drift is detectable and fail-closed
The system SHALL compare compatible metadata snapshots by canonical identity and report bounded safe added, removed, and changed object/field/constraint references. Canonical serialization SHALL be independent of object-internal and provenance evidence iteration order. Production drift policy SHALL classify changes by severity and block activation/resolution for referenced removals, incompatible type or constraint changes, source identity changes, expired freshness, and incompatible catalog changes by default. Bundle/View/IR consumers SHALL reject stale snapshot references when required compatibility fingerprints no longer match.

#### Scenario: Schema drift produces safe changes
- **WHEN** a new snapshot changes a field type or removes a referenced object
- **THEN** comparison reports the changed semantic reference without exposing raw values, and dependent activation/resolution can fail closed
- **THEN** comparison reports the changed semantic reference without exposing raw values, and dependent activation/resolution is blocked by default

#### Scenario: Non-breaking additions remain reportable
- **WHEN** a new authorized snapshot adds an unreferenced field
- **THEN** the change is classified as non-blocking information and existing Bundle/View identity remains unchanged

#### Scenario: Equivalent snapshots are stable
- **WHEN** the same metadata is discovered in a different backend mapping order
- **THEN** canonical serialization and snapshot fingerprint remain identical

### Requirement: Discovery failures are normalized
Discovery SHALL use bounded connection/command timeouts and normalize unavailable, unauthorized, malformed, and partial-discovery failures without leaking credentials, DSNs, raw backend exceptions, or raw metadata payloads.

#### Scenario: Source is unavailable
- **WHEN** a discovery provider cannot reach its source
- **THEN** it returns a safe retryable unavailable result and no partial snapshot is activated
