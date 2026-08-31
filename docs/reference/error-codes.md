# Error Codes

> **Reader**: everyone looking up a specific error. **Prerequisites**:
> none. Error codes are stable machine-readable identifiers; `retryable`
> means the same request may be retried safely without changes.

## Error record shape

Every public error carries a stable `ErrorRecord`: `code`, `category`,
`message`, `retryable`, safe scalar `details` (redacted at construction),
and `cause_type`. Serialization never includes credentials, raw query
payloads, or native provider exception objects.

## Error codes by category

### Configuration

| Code | Retryable | Meaning |
| --- | --- | --- |
| `UNSUPPORTED_SCHEMA_VERSION` | No | Declared schema version not supported; activation fails closed |
| `INVALID_CONFIGURATION` | No | Invalid core configuration (unknown field, bad value) |
| `PROTECTED_FIELD_OVERRIDE` | No | Protected core field overridden through extensions |
| `MALFORMED_CONFIGURATION` | No | Malformed configuration document |

### Lifecycle

| Code | Retryable | Meaning |
| --- | --- | --- |
| `ENGINE_NOT_READY` | No | Operation before initialization, or from a state that cannot proceed |
| `ENGINE_DRAINING` | No | New query submitted while draining |
| `ENGINE_CLOSED` | No | Operation on a closed facade/engine |
| `ASYNC_REQUIRED` | No | Sync convenience used inside an active event loop |

Assembly/admin control-plane failures use bounded lower-case outcome codes
rather than query `ErrorCode` members:

| Code | Retryable | Meaning |
| --- | --- | --- |
| `conflict` | After reread | Stale `draft_revision`; reload the draft before retrying the mutation |
| `authorization_denied` | No | Trusted lifecycle role/scope is absent or denied |
| `draft_not_approved` | No | Publish requested before the draft reached `approved` |
| `pending_assertions` | No | At least one assertion lacks a valid review binding |
| `separation_of_duties_failed` | No | Host lifecycle role-separation policy rejected publication |
| `bundle_identity_mismatch` | No | Emitted Bundle identity differs from the frozen approved draft |
| `bundle_emission_failed` / `verification_failed` | Depends on host | Safe publish callback failure; no partial publication is committed |
| `manifest_mismatch` / `audit_mismatch` | No | Manifest or audit reference does not match the emitted Bundle fingerprint |
| `version_exists` | No | Business version is already assigned to different semantic content |
| `verification_plan_binding_mismatch` / `verification_policy_mismatch` | No | Approved plan or selected policy differs from the frozen publication input |
| `verification_evidence_required` / `verification_evidence_mismatch` | No | Required evidence is absent, failed, stale, forged, or bound to different identities |

Verification case/layer statuses are `passed`, `failed`, `skipped`,
`unavailable`, `timed_out`, and `not_run`; only `passed` satisfies a required
item. Bounded issue codes such as `capability_mismatch`, `candidate_drift`,
`assertion_mismatch`, `semantic_contract_mismatch`, and
`layer_deadline_exhausted` are verification evidence metadata, not public query
`ErrorCode` members. See [Verification Suite operations](../operations/verification-suite.md).

Publish issue codes are returned in `PublishAssemblyResult.issues`. A successful
identical-content retry reports `reused`, not an error. Unknown backend failures
cross the admin boundary only as `internal_service_error` with bounded text.

### Validation / input

| Code | Retryable | Meaning |
| --- | --- | --- |
| `INVALID_INPUT` | No | Invalid request input (bounds, shape) |
| `PLAN_VALIDATION_FAILED` | No | Plan/IR validation failed |

### Multi-entity planning

| Code | Retryable | Meaning |
| --- | --- | --- |
| `JOIN_PATH_NOT_FOUND` | No | No authorized relationship path connects the requested entities |
| `JOIN_PATH_AMBIGUOUS` | No | More than one shortest authorized join path exists |
| `JOIN_EDGE_UNAUTHORIZED` | No | A relationship or entity is outside the authorized view |
| `MULTI_ENTITY_UNSUPPORTED` | No | Multi-entity intent resolved but the runtime has no `JoinPlanner` bound |

### Workflow

| Code | Retryable | Meaning |
| --- | --- | --- |
| `INVALID_TRANSITION` | No | Illegal workflow state transition |
| `BUDGET_EXCEEDED` | No | Attempt/retry budget exceeded |
| `WORKFLOW_TIMEOUT` | Yes | Workflow exceeded its deadline |
| `WORKFLOW_CANCELLED` | No | Cooperative cancellation persisted; resume fails fast |
| `RETRY_EXHAUSTED` | No | Retry budget exhausted |
| `APPROVAL_REQUIRED` | No | Internal approval-required event (not a public status) |
| `STALE_CHECKPOINT` | No | Checkpoint incompatible with current view/context |
| `WORKFLOW_RECOVERABLE` | Yes | Recoverable workflow condition |
| `DUPLICATE_REQUEST` | No | Request id already completed; replayed idempotently |
| `IDEMPOTENCY_CONFLICT` | No | Idempotency key reused with a different request |

### Governance / authorization

| Code | Retryable | Meaning |
| --- | --- | --- |
| `GOVERNANCE_DENIED` | No | Policy evaluation denied |
| `AUTHORIZATION_REJECTED` | No | Authorization rejected (scope/policy binding) |
| `TENANT_CONTEXT_REJECTED` | No | Trusted tenant context missing/inactive/mismatched |
| `RESULT_PROTECTION_FAILED` | No | Result normalization/protection failed |

### Adapter (SQL)

| Code | Retryable | Meaning |
| --- | --- | --- |
| `SQL_REJECTED` | No | SQL rejected before execution (spec/allowlist/limits) |
| `SQL_EXECUTION_FAILED` | Yes | Execution failed (transient) |
| `FIXTURE_UNAVAILABLE` | No | SQLite fixture unavailable |
| `FIXTURE_VERIFICATION_FAILED` | No | Fixture verification failed |

### Adapter (MongoDB)

| Code | Retryable | Meaning |
| --- | --- | --- |
| `MONGO_REJECTED` | No | MongoDB operation rejected before any driver call |
| `MONGO_EXECUTION_FAILED` | Yes | Execution failed (transient) |
| `MONGO_UNAVAILABLE` | Yes | Driver missing or service unreachable |

### Model provider

| Code | Retryable | Meaning |
| --- | --- | --- |
| `MODEL_INVOCATION_FAILED` | Yes* | Model invocation failed; internal `model_code` in details |
| `CLARIFICATION_REQUIRED` | No | Ambiguous input; clarification returned |

\*Retryability is inherited from the internal model error record. Provider
failures surface publicly as `MODEL_INVOCATION_FAILED`; the internal
`ModelErrorCode` travels in `details.model_code` and is never a public
code: `MODEL_TIMEOUT`, `PROVIDER_UNAVAILABLE` (retryable), `INVALID_REQUEST`,
`MALFORMED_RESPONSE`, `OUTPUT_LIMIT_EXCEEDED`, `RETRY_EXHAUSTED`,
`UNSAFE_OUTPUT`, `INSTRUCTION_VERSION_INCOMPATIBLE`,
`INSTRUCTION_BOUNDS_EXCEEDED`, `UNSAFE_INSTRUCTION_CONTENT`,
`UNKNOWN_MODEL_ERROR`, `VS_001`, `VS_002`, `VS_SNAPSHOT_UNAVAILABLE`.

Value-semantics resolution failures (v4.1) are **resolution-stage**
errors raised in the intent resolver before the IR freezes — never
compiler errors: `VS_001` (the internal VALUE_UNKNOWN member: a filter
value is not a known business term for a mapped field; details carry the
field, the attempted value, and the bounded known business terms),
`VS_002` (the internal VALUE_OPERATOR_DISALLOWED member: a mapped field
filtered with an operator other than `eq`/`in`), and
`VS_SNAPSHOT_UNAVAILABLE` (the internal VALUE_SNAPSHOT_UNAVAILABLE
member: the bundle-referenced descriptor snapshot is unavailable or its
catalog fingerprint does not match the authorized view — value
resolution fails closed).

### Calculated fields (v4.2)

| Code | Retryable | Meaning |
| --- | --- | --- |
| `CF_001` | No | Calculated-field expression rejected (operator outside the closed whitelist, non-int constant, non-numeric leaf, bounds exceeded, declared output type does not match the inferred type) |
| `CF_002` | No | Invalid reference inside a calculated-field expression (unknown field id, or composition: a calculated field referencing another calculated field) |
| `CF_003` | No | A selection references an undeclared calculated-field name; fails closed at IR validation |
| `CF_004` | No | Bidirectional pii isolation: a calculated-field expression references a `pii: true` field (definition time), or a `pii` declaration is applied to a field already referenced by a declared calculated field (bundle validation time). Future field-masking policy targets must join the same check. |
| `CF_005` | No | Runtime zero-division failure under the `error` policy; structured execution failure, never a partial result |

`CF_001` and `CF_002` fail at descriptor definition time (and are
re-validated fail-closed at compile time); `CF_003` fails at IR
validation; `CF_004` is enforced **bidirectionally** because masking is
enforced by adapter post-processing on output columns (see the
[pii masking ADR](../architecture/adr-pii-masking-enforcement-point.md))
— a derived output column would carry unmasked-derived values past that
enforcement point, so either arrival order is rejected;
`CF_005` is the execution-stage translation of a zero division under
the declared `error` `zero_division_policy` (MongoDB server code 16608,
SQLite fail-closed at compile time because it cannot enforce the
policy). Evaluation-layer attribution codes CF_HIT, CF_COMPILE_FAIL,
CF_NOT_DECLARED, and CF_NOT_REFERENCED are bounded
evidence metadata, not error codes — see
[Semantic layer](../architecture/semantic-layer.md#calculated-field-attribution).

### Durable state

| Code | Retryable | Meaning |
| --- | --- | --- |
| `STORE_UNAVAILABLE` | Yes | State store unreachable/timeout/pool failure |
| `STORE_TIMEOUT` | Yes | State store command timeout |
| `LEASE_BUSY` | Yes | Lease held by another owner |
| `FENCING_REJECTED` | No | Stale owner after takeover; never retried |

### Metadata

| Code | Retryable | Meaning |
| --- | --- | --- |
| `METADATA_DISCOVERY_FAILED` | Yes | Discovery failed |
| `METADATA_UNAVAILABLE` | Yes | Source unreachable |
| `METADATA_UNAUTHORIZED` | No | Discovery not authorized (allowlist/tenant) |
| `METADATA_BOUNDS_EXCEEDED` | No | Discovery bounds exceeded |

### Evaluation / plugin / telemetry / fallback

| Code | Retryable | Meaning |
| --- | --- | --- |
| `EVALUATION_FAILED` | No | Evaluation run failed |
| `INVALID_MANIFEST` | No | Plugin manifest invalid |
| `CAPABILITY_NOT_RESOLVED` | No | Capability could not be resolved |
| `TELEMETRY_SINK_FAILURE` | Yes | Telemetry sink failure (degraded, not fatal) |
| `NOT_CONFIGURED` | No | No executable runtime configured (safe fallback) |
| `INTERNAL_ERROR` | No | Unknown failure; message redacted |

## Retry guidance

- Retryable errors (`retryable: true`) may be retried with the same
  request; categories `adapter`, `telemetry`, and `workflow` default to
  retryable.
- Configuration, lifecycle, governance, authorization, and validation
  errors are never retryable — fix the input or deployment.
- `FENCING_REJECTED` and `STALE_CHECKPOINT` indicate a newer owner or
  context; resume through the current owner/context, do not retry the
  stale path.
- Ambiguous post-execution states are surfaced for reconciliation —
  never silently replayed or claimed as success.
