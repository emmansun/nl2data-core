## MODIFIED Requirements

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