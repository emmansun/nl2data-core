## ADDED Requirements

### Requirement: Durable envelopes record and validate canonicalization profiles
The durable semantic catalog SHALL record the canonicalization profile used for fingerprint-critical persisted envelopes, including metadata snapshots, proposal sets, assembly drafts, published Bundles, accepted manifests, verification evidence, audit evidence, active pointers, workflow-compatible references, and publication aggregates where applicable. Reload SHALL validate the declared profile, canonical bytes, fingerprint, schema version, tenant/source scope, and artifact kind before exposing an artifact as current.

#### Scenario: Reload rejects unsupported profile
- **WHEN** a persisted envelope declares an unsupported or unknown canonicalization profile
- **THEN** the catalog returns a safe incompatible-profile error and does not expose the artifact as validated current content

#### Scenario: Legacy profile is preserved explicitly
- **WHEN** an existing persisted record was written before strict JCS-compatible canonicalization metadata existed
- **THEN** the catalog classifies it through an explicit legacy profile or migration path and never silently rewrites its fingerprint as current-profile evidence

### Requirement: Catalog envelope payloads are JSON-safe before canonicalization
Catalog persistence SHALL validate fingerprint-critical envelope payloads as JSON-safe values before canonicalization. It SHALL reject native objects, datetimes, sets, bytes, callables, exceptions, non-finite numbers, raw prompts, raw queries/results, credentials, resolved deployment values, physical names outside approved artifact boundaries, and unbounded text before write or reload validation.

#### Scenario: Unsafe envelope write is rejected
- **WHEN** a catalog write attempts to store an unsafe native value or forbidden sensitive value in a fingerprint-critical envelope
- **THEN** the write fails before persistence and returns a normalized safe error without producing a fingerprint
