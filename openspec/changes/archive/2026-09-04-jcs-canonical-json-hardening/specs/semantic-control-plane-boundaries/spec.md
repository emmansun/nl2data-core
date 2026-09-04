## ADDED Requirements

### Requirement: Canonicalization has one control-plane owner
The semantic control plane SHALL use one canonical owner for fingerprint-critical canonical JSON serialization and SHA-256 fingerprint construction. Domain, verification, publication, catalog, Admin, workflow, memory, and optional package modules SHALL NOT implement duplicate canonical serializers or local fingerprint-critical `json.dumps` calls unless an architecture manifest explicitly marks them as presentation-only or legacy compatibility code.

#### Scenario: Duplicate fingerprint serializer is rejected
- **WHEN** a control-plane module adds a local canonical JSON or fingerprint implementation for identity-critical payloads instead of importing the canonical owner
- **THEN** architecture conformance or focused tests fail with the offending module and boundary

#### Scenario: Presentation JSON remains separate
- **WHEN** a module serializes JSON for display, transport, debugging, or authoring export outside a fingerprint-critical domain
- **THEN** it is either excluded from identity checks by documented boundary rules or uses a clearly named presentation serializer that cannot be mistaken for canonical identity

### Requirement: Fingerprint domain ownership is explicit
Every fingerprint-critical payload SHALL document its owner, canonicalization profile, included fields, excluded fields, and golden-vector coverage. Compatibility re-exports MAY expose helpers, but they SHALL NOT own independent canonicalization behavior.

#### Scenario: New fingerprint domain requires ownership metadata
- **WHEN** a change adds a new persisted fingerprint, evidence fingerprint, semantic identity, workflow checkpoint identity, or catalog envelope identity
- **THEN** the change includes ownership metadata and golden-vector coverage for the canonicalization profile