## ADDED Requirements

### Requirement: PostgreSQL backend implements durable workflow contracts
The package SHALL implement the core `StateStore`, `IdempotencyStore`, `WorkflowLeaseStore`, and `FencedStateStore` contracts using PostgreSQL, without changing workflow models, transition rules, or public outcome semantics.

#### Scenario: Workflow state survives restart
- **WHEN** a worker persists valid workflow state and a new worker reconnects to the same configured PostgreSQL schema
- **THEN** the new worker can load the same compatible safe checkpoint, revision, and scope

#### Scenario: Core transition rules remain authoritative
- **WHEN** the package receives a state mutation that bypasses a required workflow gate
- **THEN** it rejects the mutation with the core workflow error semantics

### Requirement: PostgreSQL persistence is safe and versioned
The package SHALL persist only bounded versioned safe workflow snapshots, idempotency records, lease identity, and opaque evidence references. It SHALL reject raw prompts, queries, results, credentials, provider objects, native driver values, and unsupported schema versions on write or read.

#### Scenario: Unsafe state cannot reach PostgreSQL
- **WHEN** a caller attempts to persist a prohibited raw payload or unbounded value
- **THEN** validation rejects it before database persistence

#### Scenario: Newer schema fails closed
- **WHEN** the database or serialized state reports a schema version newer than the package supports
- **THEN** the package returns a normalized incompatible-schema error and does not modify the state

### Requirement: Shared workers use transactional leases and fencing
The package SHALL coordinate shared workflow execution using atomic lease acquisition, renewal, release, takeover after expiry, and monotonic fencing tokens. Protected state and idempotency mutations SHALL verify the current owner and fencing token.

#### Scenario: Lease takeover fences stale worker
- **WHEN** worker B takes over an expired workflow lease after worker A pauses
- **THEN** worker B receives a higher fencing token and worker A's later state or completion mutation is rejected

#### Scenario: Valid lease remains exclusive
- **WHEN** a worker attempts to acquire a workflow lease while another valid owner holds it
- **THEN** acquisition is rejected as busy and the current owner remains unchanged

### Requirement: Durable compare-and-set and idempotency are preserved
The package SHALL atomically enforce workflow identity, expected revision/status, tenant scope, lease ownership/fencing where supplied, and idempotency key ownership. Completed idempotency records SHALL reference one safe terminal outcome and conflicting reuse SHALL be rejected.

#### Scenario: Stale update is rejected
- **WHEN** two workers update one workflow from the same prior revision
- **THEN** exactly one update succeeds and the other receives a structured conflict without overwriting stored state

#### Scenario: Duplicate request does not re-execute
- **WHEN** a completed idempotency key is reused within its scope
- **THEN** the package returns the stored safe terminal reference and does not authorize a second external execution

### Requirement: Tenant and compatibility isolation are enforced
The package SHALL require matching tenant scope fingerprints for scoped workflow, lease, and idempotency operations and SHALL preserve IR/View compatibility fingerprints used by resume validation. Raw tenant or principal identifiers SHALL never be persisted as namespace keys.

#### Scenario: Cross-tenant resume is denied
- **WHEN** a worker loads a checkpoint using a different tenant scope fingerprint
- **THEN** the package does not reveal or resume the checkpoint

#### Scenario: Stale semantic checkpoint is rejected
- **WHEN** a persisted checkpoint's IR or View fingerprint is incompatible with current runtime context
- **THEN** resume is rejected before adapter execution

### Requirement: PostgreSQL operations are optional and normalized
The package SHALL load psycopg/psycopg_pool lazily, support host-injected pools or DSNs, provide bounded command timeouts and cleanup, normalize database failures without leaking DSNs or driver text, and keep its schema separate from semantic catalog and business-data tables.

#### Scenario: Core import remains PostgreSQL-free
- **WHEN** an application imports or constructs the base core without installing the PostgreSQL workflow package
- **THEN** psycopg and PostgreSQL workflow modules are not required or loaded

#### Scenario: Database outage is retryable and safe
- **WHEN** PostgreSQL is unavailable or a command times out
- **THEN** the package returns a normalized retryable store error and does not claim an uncommitted mutation succeeded
