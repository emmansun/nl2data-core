## MODIFIED Requirements

### Requirement: Stable public import boundary
The public package SHALL expose the P0/P1 engine, request, context, outcome, capability, clarification, and error contracts from `nl2data`, and application code SHALL NOT need to import `nl2data_core` internals. Trusted tenant claims SHALL remain internal host-integration data rather than public client authorization inputs.

#### Scenario: Public imports are available
- **WHEN** an application imports the documented public symbols from `nl2data`
- **THEN** the imports succeed without importing a database driver, identity provider, or transport framework

#### Scenario: Tenant claims are not client authority
- **WHEN** a public request includes an untrusted tenant hint
- **THEN** the public boundary does not treat it as effective authorization context