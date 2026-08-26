## ADDED Requirements

### Requirement: Admin service exposes a versioned control plane
The admin package SHALL expose a versioned transport-neutral application-service contract for bounded metadata discovery, snapshot/proposal inspection, proposal review, Bundle validation/publication/activation, active lookup, drift decisions, and rollback. It SHALL not expose end-user natural-language query execution.

#### Scenario: Capabilities are discoverable
- **WHEN** an authorized host calls the service capability method
- **THEN** the result lists only implemented admin operations and their prerequisites

#### Scenario: Query execution is not an admin operation
- **WHEN** a host attempts to invoke natural-language query execution through the admin service
- **THEN** the operation is absent or rejected as unsupported

### Requirement: Admin requests require trusted host authorization
Every API request SHALL receive a host-provided trusted authentication and authorization context. Read and mutation permissions SHALL be checked against tenant/source scope; client-supplied scope claims SHALL be treated as untrusted routing input until authorized.

#### Scenario: Missing authentication fails closed
- **WHEN** a service method is called without a trusted host authorization context
- **THEN** it is rejected without reading or mutating catalog content

#### Scenario: Cross-tenant operation is denied
- **WHEN** an authenticated operator requests content or a mutation outside the authorized tenant/source scope
- **THEN** the service returns a safe authorization error without revealing whether the resource exists

### Requirement: Service results contain bounded safe administrative data
The service SHALL return typed bounded result objects containing statuses, opaque identifiers, fingerprints, versions, counts, timestamps, bounded issues, and safe provenance references. It SHALL never return credentials, DSNs, raw prompts, raw SQL/MQL, native clients, unrestricted sample values, raw provider payloads, or unrestricted backend exception text.

#### Scenario: Snapshot detail is safe
- **WHEN** an authorized host retrieves a MetadataSnapshot or discovery result
- **THEN** the result contains only allowed structural facts and bounded evidence references

#### Scenario: Backend failure is normalized
- **WHEN** a database, discoverer, or catalog operation fails
- **THEN** the service returns a normalized error code/category with no secret, DSN, or raw exception leakage

### Requirement: Long-running operations are job-oriented
Discovery and any operation that may exceed a host-defined deadline SHALL support a bounded asynchronous job contract with submit, status, and supported cancellation operations. Job status SHALL be safe, scoped, idempotent, and unable to claim success before durable completion.

#### Scenario: Discovery returns a safe job
- **WHEN** an authorized host starts bounded discovery
- **THEN** the service returns an opaque job ID and status without blocking indefinitely or exposing raw source payloads

#### Scenario: Repeated submission is idempotent
- **WHEN** the same authorized mutation is submitted with the same idempotency key
- **THEN** the service returns the original safe job/result reference without starting a conflicting duplicate operation

### Requirement: Proposal review is explicit and auditable
The service SHALL expose inspect, approve, reject, and revise operations for SemanticProposalSet records. Review mutations SHALL require the expected snapshot/proposal fingerprint or revision, preserve immutable history, and record a bounded host-provided operator audit reference.

#### Scenario: Approval changes eligibility
- **WHEN** an authorized reviewer approves selected proposal IDs against the expected proposal set fingerprint
- **THEN** the resulting immutable set marks only those proposals approved and makes them eligible for conversion

#### Scenario: Stale review is rejected
- **WHEN** a reviewer submits a decision against a proposal set whose fingerprint or revision has changed
- **THEN** the API returns a conflict and leaves the newer review state unchanged

### Requirement: Bundle lifecycle mutations are guarded
The service SHALL expose validation, publish, activate, active lookup, and rollback commands that delegate structural, dependency, fingerprint, scope, freshness, completeness, and drift checks to the core/catalog. Mutations SHALL require expected version/fingerprint context, idempotency, and authorized release permission.

#### Scenario: Invalid activation preserves current state
- **WHEN** activation fails validation, compatibility, authorization, freshness, or drift checks
- **THEN** the service reports a safe rejection and the current active Bundle remains unchanged

#### Scenario: Rollback keeps immutable history
- **WHEN** an authorized operator rolls back to a previously valid version
- **THEN** the active pointer changes atomically and both old and new Bundle artifacts remain immutable and retrievable by fingerprint

### Requirement: Admin service is optional and independently testable
The admin package SHALL be optional, keep transport frameworks and authentication dependencies outside the core import path, publish stable command/result schemas, and provide direct service contract tests plus security tests. Host HTTP/CLI/UI adapters are outside this requirement.

#### Scenario: Core remains transport-free
- **WHEN** an application imports `nl2data` or constructs the core facade without installing the admin extra
- **THEN** transport framework, admin service, and authentication modules remain unloaded

#### Scenario: Service schema matches supported operations
- **WHEN** CI generates and validates the versioned service schema
- **THEN** every documented operation has a bounded input/result model and unsupported operations are not advertised
