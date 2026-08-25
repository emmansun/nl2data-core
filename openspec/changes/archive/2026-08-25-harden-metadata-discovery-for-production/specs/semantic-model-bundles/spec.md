## MODIFIED Requirements

### Requirement: Bundle loading and compatibility are explicit
Bundle loaders SHALL validate schema version, model version, source identity, dependency fingerprints, source snapshot fingerprint, freshness, completeness, and compatibility constraints before returning or activating a bundle. Incompatible, stale, expired, unauthorized, or blocking-drift bundles SHALL fail closed and SHALL not be silently downgraded. A partial discovery snapshot SHALL not satisfy production activation by default.

#### Scenario: Stale dependency blocks activation
- **WHEN** a bundle depends on an unavailable, stale, incomplete, blocking-drift, or incompatible catalog/model snapshot
- **THEN** loading or activation is rejected before a View can resolve against it

#### Scenario: Compatible snapshot permits activation
- **WHEN** a bundle source snapshot is fresh, authorized, complete, and has no blocking drift against its declared compatibility baseline
- **THEN** the bundle may be activated after normal Bundle validation

### Requirement: Catalog publication is atomic and replaceable
The system SHALL define a replaceable catalog protocol and a bounded reference implementation supporting immutable publish, lookup by bundle/version, active snapshot lookup, atomic activation, and rollback only to a previously published valid bundle. Publication SHALL validate source snapshot compatibility and production drift policy before making a bundle available, and activation SHALL never expose a partial bundle.

#### Scenario: Blocking drift remains inactive
- **WHEN** a candidate bundle is based on a snapshot with a blocking drift decision
- **THEN** the catalog rejects publication or activation and preserves the current active bundle

#### Scenario: Rollback remains safe
- **WHEN** an operator rolls back to a previously published valid bundle whose snapshot is still compatible with the policy
- **THEN** the catalog changes the active pointer atomically without mutating published artifacts
