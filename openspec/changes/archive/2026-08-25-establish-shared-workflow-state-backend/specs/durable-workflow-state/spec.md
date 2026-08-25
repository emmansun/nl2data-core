## MODIFIED Requirements

### Requirement: SQLite workflow state is durable and safe
The workflow state foundation SHALL persist versioned workflow snapshots through replaceable StateStore implementations. SQLite SHALL persist safe serialized state with indexed workflow/request/status/scope fields, while the shared PostgreSQL implementation SHALL provide the same safe snapshot semantics for multiple workers and SHALL exclude raw prompts, queries, results, credentials, provider objects, and native clients.

#### Scenario: State survives store recreation
- **WHEN** a workflow state is created, a durable store is closed, and a compatible store opens the same persistence backend
- **THEN** the state and safe events can be retrieved with the same version and fingerprints

#### Scenario: Unsafe state cannot be persisted
- **WHEN** a caller attempts to persist raw provider or result payloads through any state API
- **THEN** validation rejects the payload before it reaches SQLite or PostgreSQL

### Requirement: Durable updates are transactional compare-and-set
The durable store SHALL update state only when workflow ID, expected revision/version, expected status, tenant scope, and, for shared execution, current lease ownership and fencing token match the stored record.

#### Scenario: Concurrent stale update is rejected
- **WHEN** two writers update the same workflow from one prior version
- **THEN** exactly one update succeeds and the stale writer receives a structured conflict

#### Scenario: Superseded owner cannot update
- **WHEN** a worker updates a shared workflow with a fencing token superseded by another owner
- **THEN** the update is rejected and the stored state remains unchanged

### Requirement: Cleanup preserves active workflows
The durable store SHALL delete only terminal or expired records in bounded cleanup operations and SHALL never remove active or running workflows or valid execution leases.

#### Scenario: Active state survives cleanup
- **WHEN** cleanup runs while a workflow is running under a valid lease
- **THEN** the running state and lease remain retrievable
