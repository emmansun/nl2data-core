## MODIFIED Requirements

### Requirement: Stable public import boundary
The public package SHALL expose the P0/P1/P2 engine, request, context, outcome, capability, clarification, workflow-handle, workflow-status, and error contracts from `nl2data`, and application code SHALL NOT need to import `nl2data_core` internals. Trusted tenant claims and native provider types SHALL remain outside public client inputs and outputs.

#### Scenario: Public imports are available
- **WHEN** an application imports the documented public symbols from `nl2data`
- **THEN** the imports succeed without importing a database driver, identity provider, HTTP framework, or workflow framework

#### Scenario: Internal implementation types do not cross the boundary
- **WHEN** a public model or facade result is serialized
- **THEN** it contains only documented protected models, safe errors, bounded identifiers, and fingerprints