# semantic-admin-api Specification

## Purpose
Define a versioned transport-neutral admin application service for bounded metadata discovery, proposal review, semantic Bundle lifecycle, drift decisions, and operational status with host-provided authorization.

## Requirements

### Requirement: Admin service exposes a versioned control plane
The admin package SHALL expose a versioned transport-neutral application-service contract for bounded metadata discovery, snapshot/proposal inspection, assembly draft creation and inspection, assertion-level review, assembly approval, Bundle verification/publication/activation, active lookup, drift decisions, audit lookup, version listing, and rollback. It SHALL not expose end-user natural-language query execution.

#### Scenario: Capabilities are discoverable
- **WHEN** an authorized host calls the service capability method
- **THEN** the result lists only implemented admin operations and their prerequisites

#### Scenario: Query execution is not an admin operation
- **WHEN** a host attempts to invoke natural-language query execution through the admin service
- **THEN** the operation is absent or rejected as unsupported

#### Scenario: Assembly lifecycle operations are discoverable
- **WHEN** an authorized host inspects admin capabilities after this change
- **THEN** the response describes supported draft, review, approval, publish, activation, rollback, audit, and version-listing operations with safe prerequisites

### Requirement: Admin requests require trusted host authorization
Every API request SHALL receive a host-provided trusted authentication and authorization context. Read and mutation permissions SHALL be checked against tenant/source scope; client-supplied scope claims SHALL be treated as untrusted routing input until authorized. Lifecycle mutations SHALL additionally require the host-authorized role for the requested Author, Reviewer, Approver, or Publisher action.

#### Scenario: Missing authentication fails closed
- **WHEN** a service method is called without a trusted host authorization context
- **THEN** it is rejected without reading or mutating catalog content

#### Scenario: Cross-tenant operation is denied
- **WHEN** an authenticated operator requests content or a mutation outside the authorized tenant/source scope
- **THEN** the service returns a safe authorization error without revealing whether the resource exists

#### Scenario: Missing lifecycle role is denied
- **WHEN** an authenticated operator lacks the required lifecycle role for an assertion decision, approval, publish, activation, or rollback
- **THEN** the service rejects the request and leaves lifecycle state unchanged

### Requirement: Service results contain bounded safe administrative data
The service SHALL return typed bounded result objects containing lifecycle statuses, opaque identifiers, fingerprints, versions, counts, timestamps, bounded issues, review summaries, audit references, and safe provenance references. It SHALL never return credentials, resolved DSNs, raw prompts, raw SQL/MQL, native clients, unrestricted sample values, raw provider payloads, unrestricted backend exception text, or raw operator identity beyond host-approved bounded audit references.

#### Scenario: Snapshot detail is safe
- **WHEN** an authorized host retrieves a MetadataSnapshot or discovery result
- **THEN** the result contains only allowed structural facts and bounded evidence references

#### Scenario: Backend failure is normalized
- **WHEN** a database, discoverer, catalog, verifier, or lifecycle operation fails
- **THEN** the service returns a normalized error code/category with no secret, DSN, raw exception, raw prompt, or raw native payload leakage

#### Scenario: Draft detail omits secrets
- **WHEN** an authorized host retrieves an assembly draft with deployment bindings
- **THEN** the response contains only safe reference forms and redacted summaries, never resolved credentials

### Requirement: Long-running operations are job-oriented
Discovery and any operation that may exceed a host-defined deadline SHALL support a bounded asynchronous job contract with submit, status, and supported cancellation operations. Job status SHALL be safe, scoped, idempotent, and unable to claim success before durable completion.

#### Scenario: Discovery returns a safe job
- **WHEN** an authorized host starts bounded discovery
- **THEN** the service returns an opaque job ID and status without blocking indefinitely or exposing raw source payloads

#### Scenario: Repeated submission is idempotent
- **WHEN** the same authorized mutation is submitted with the same idempotency key
- **THEN** the service returns the original safe job/result reference without starting a conflicting duplicate operation

### Requirement: Proposal review is explicit and auditable
The service SHALL expose inspect, approve, reject, and revise operations for SemanticProposalSet records and SHALL expose conversion/adaptation from reviewed proposals into assembly assertions. Review mutations SHALL require the expected snapshot/proposal fingerprint or revision, preserve immutable history, and record a bounded host-provided operator audit reference. Proposal review SHALL NOT bypass assembly assertion review where assertion review is required for publication.

#### Scenario: Approval changes eligibility
- **WHEN** an authorized reviewer approves selected proposal IDs against the expected proposal set fingerprint
- **THEN** the resulting immutable set marks only those proposals approved and makes them eligible for assertion adaptation

#### Scenario: Stale review is rejected
- **WHEN** a reviewer submits a decision against a proposal set whose fingerprint or revision has changed
- **THEN** the API returns a conflict and leaves the newer review state unchanged

#### Scenario: Proposal adaptation preserves provenance
- **WHEN** approved discovery proposals are adapted into an assembly draft
- **THEN** the resulting assertions carry bounded proposal provenance as audit-side metadata and remain subject to assertion review bindings before publication

### Requirement: Bundle lifecycle mutations are guarded
The service SHALL expose validation, verification, publish, activate, active lookup, version listing, audit lookup, and rollback commands that delegate structural, dependency, fingerprint, scope, freshness, completeness, drift, review-binding, verification, publish atomicity, and idempotency checks to the core/catalog. Mutations SHALL require expected draft revision or published fingerprint context, idempotency where applicable, and authorized lifecycle permission.

#### Scenario: Invalid activation preserves current state
- **WHEN** activation fails validation, compatibility, authorization, freshness, or drift checks
- **THEN** the service reports a safe rejection and the current active Bundle remains unchanged

#### Scenario: Rollback keeps immutable history
- **WHEN** an authorized operator rolls back to a previously valid version
- **THEN** the active pointer changes atomically and both old and new Bundle artifacts remain immutable and retrievable by fingerprint

#### Scenario: Publish delegates lifecycle enforcement
- **WHEN** a host requests publish for an approved assembly draft
- **THEN** the service delegates pending-assertion checks, verification, fingerprint computation, audit creation, idempotency, and supersession updates to core/catalog and returns only the bounded outcome

### Requirement: Assembly draft operations are explicit and revision guarded
The service SHALL expose create, read, edit, submit-for-review, assertion-decision, and approve operations for assembly drafts. Every mutation SHALL require expected `draft_revision`; stale mutations SHALL return conflict without changing draft content or review state.

#### Scenario: Draft creation returns lifecycle state
- **WHEN** an authorized author creates an assembly draft from manual input or discovery output
- **THEN** the response includes an opaque draft identifier, lifecycle state, draft revision, assertion counts, and no semantic bundle fingerprint

#### Scenario: Assertion review changes one assertion
- **WHEN** an authorized reviewer approves, rejects, or edits one assertion with the current draft revision
- **THEN** only the targeted assertion and affected review metadata change and the returned draft revision advances

#### Scenario: Stale assertion review is rejected
- **WHEN** an authorized reviewer submits an assertion decision with an outdated draft revision
- **THEN** the service returns a conflict and leaves the current draft unchanged

### Requirement: Admin publish returns audit and supersession references
Publish responses SHALL include bounded references to the published semantic fingerprint, bundle name, business version metadata, audit record, superseded predecessor when present, idempotency status, and activation eligibility. They SHALL NOT include canonical bytes, raw assertions, secrets, resolved DSNs, or raw operator identities unless separately authorized and bounded by the host.

#### Scenario: Publish response is bounded
- **WHEN** publish succeeds
- **THEN** the service returns safe identifiers and audit references sufficient for a host UI or release pipeline to display the result

#### Scenario: Idempotent publish reports existing artifact
- **WHEN** publish is retried with the same semantic payload and idempotency key or matching fingerprint
- **THEN** the service reports that the existing publication was reused rather than creating a duplicate

### Requirement: Admin service is optional and independently testable
The admin package SHALL be optional, keep transport frameworks and authentication dependencies outside the core import path, publish stable command/result schemas, and provide direct service contract tests plus security tests. Host HTTP/CLI/UI adapters are outside this requirement.

#### Scenario: Core remains transport-free
- **WHEN** an application imports `nl2data` or constructs the core facade without installing the admin extra
- **THEN** transport framework, admin service, and authentication modules remain unloaded

#### Scenario: Service schema matches supported operations
- **WHEN** CI generates and validates the versioned service schema
- **THEN** every documented operation has a bounded input/result model and unsupported operations are not advertised

### Requirement: Admin service validates and imports authoring documents safely
The Admin service SHALL expose bounded `validate_authoring` and `import_authoring` operations. Both operations SHALL require trusted host authentication and authorized tenant/source scope; import SHALL additionally require Assembly Author permission and role. Validation SHALL parse and validate without persistence. Import SHALL delegate deterministic lowering to core, derive the author reference from trusted authorization context, and create the draft through the existing tenant-scoped lifecycle boundary. Neither operation SHALL expose raw secrets, unrestricted parser exceptions, review metadata, native parser objects, or partial drafts.

#### Scenario: Validation has no side effects
- **WHEN** an authorized caller validates a correct authoring document
- **THEN** the service returns bounded normalized metadata and no draft is persisted

#### Scenario: Import creates one clean draft
- **WHEN** an authorized Author imports a valid document with a current source scope
- **THEN** the service creates one revision-zero draft and returns its safe summary without reviewing, approving, or publishing it

#### Scenario: Invalid input returns bounded diagnostics
- **WHEN** parsing, schema, reference, bounds, or safe-content validation fails
- **THEN** the service returns a bounded ordered diagnostic list with safe codes, authoring paths, and optional line/column locations, without echoing secret-like scalar values or raw backend exceptions

#### Scenario: Cross-scope import is denied before persistence
- **WHEN** the document source is outside the caller's authorized source set or tenant scope
- **THEN** import returns the existing safe authorization error and no information about another scope's drafts is revealed

### Requirement: Admin capabilities and schemas advertise authoring operations
The versioned Admin capability and schema surfaces SHALL describe authoring validation and import, including maximum input size, supported authoring API versions, required permissions and lifecycle role, and bounded result DTOs. They SHALL NOT advertise direct authoring-to-publish or authoring-to-approve operations.

#### Scenario: Host discovers authoring prerequisites
- **WHEN** an authorized host reads Admin capabilities or generated service schemas
- **THEN** it can discover supported authoring versions, validation/import operations, and their authorization and size requirements

