## Purpose

Define the stable public models, structured errors, import boundary, and protected outcome contract for NL2Data applications.
## Requirements
### Requirement: Stable public import boundary
The public package SHALL expose the P0/P1 engine, request, context, outcome, capability, clarification, and error contracts from `nl2data`, and application code SHALL NOT need to import `nl2data_core` internals. Trusted tenant claims SHALL remain internal host-integration data rather than public client authorization inputs.

#### Scenario: Public imports are available
- **WHEN** an application imports the documented public symbols from `nl2data`
- **THEN** the imports succeed without importing a database driver, identity provider, or transport framework

#### Scenario: Tenant claims are not client authority
- **WHEN** a public request includes an untrusted tenant hint
- **THEN** the public boundary does not treat it as effective authorization context

### Requirement: Immutable public models
Public request, context, outcome, result, and capability models SHALL reject unknown fields and SHALL be immutable after construction.

#### Scenario: Invalid public input is rejected
- **WHEN** a public model receives an unknown field or an invalid bounded value
- **THEN** model validation raises a structured validation error

#### Scenario: Valid model cannot be mutated
- **WHEN** application code attempts to change a field on a constructed public model
- **THEN** the operation is rejected and the original model remains unchanged

### Requirement: Structured public errors
The public error contract SHALL provide a stable code, category, human-readable message, retryability indicator, and safe structured details without exposing secrets or native provider objects.

#### Scenario: Error serializes safely
- **WHEN** an internal failure is converted to a public error
- **THEN** serialization contains stable safe fields and excludes credentials, raw query payloads, and provider exception objects

### Requirement: Protected result boundary
Public query outcomes SHALL expose only protected result contracts and SHALL not expose native cursors, connections, driver-specific values, or raw workflow state. Outcome status SHALL be consistent with its payload: a successful outcome SHALL contain a protected result, while failed, rejected, and not-configured outcomes SHALL contain no result and SHALL carry a safe structured error.

#### Scenario: Unsupported execution returns no raw result
- **WHEN** the P0 engine has no configured executable workflow
- **THEN** it returns an explicit not-configured outcome without a raw result or internal state payload

#### Scenario: Successful execution requires a protected result
- **WHEN** a workflow returns a successful public outcome
- **THEN** the outcome contains a scalar-only protected result and no native execution object

#### Scenario: Rejected execution cannot contain a result
- **WHEN** governance rejects a query or validation fails
- **THEN** the public outcome has rejected or failed status, no result, and a safe structured error

