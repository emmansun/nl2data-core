## ADDED Requirements

### Requirement: SQLite workflow state is durable and safe
The system SHALL persist versioned workflow snapshots in SQLite using only safe serialized state, indexed workflow/request/status/scope fields, and SHALL exclude raw prompts, queries, results, credentials, provider objects, and native clients.

#### Scenario: State survives store recreation
- **WHEN** a workflow state is created, the store is closed, and a new store opens the same database
- **THEN** the state and safe events can be retrieved with the same version and fingerprints

#### Scenario: Unsafe state cannot be persisted
- **WHEN** a caller attempts to persist raw provider or result payloads through the state API
- **THEN** validation rejects the payload before it reaches SQLite

### Requirement: Durable updates are transactional compare-and-set
The durable store SHALL update state only when workflow ID, expected version, expected status, and tenant scope match the stored record.

#### Scenario: Concurrent stale update is rejected
- **WHEN** two writers update the same workflow from one prior version
- **THEN** exactly one update succeeds and the stale writer receives a structured conflict

### Requirement: Cleanup preserves active workflows
The durable store SHALL delete only terminal or expired records in bounded cleanup operations and SHALL never remove active or running workflows.

#### Scenario: Active state survives cleanup
- **WHEN** cleanup runs while a workflow is running
- **THEN** the running state remains retrievable