## ADDED Requirements

### Requirement: MongoDB facts are adapter-neutral and scope bounded
The adapter SHALL extract collection, nested field, operator, stage, tenant obligation, and result-shape facts for the common Governance and Workflow Runtime without exposing native driver objects or raw values.

#### Scenario: Unauthorized collection is denied
- **WHEN** a specification references a collection outside the authorized metadata view
- **THEN** validation returns a governance-safe denial before execution

### Requirement: Nested field and operator allowlists are enforced
The adapter SHALL represent nested fields as canonical dotted paths and SHALL reject unknown fields, operators, expressions, projections, and wildcard access unless explicitly enabled by the profile.

#### Scenario: Unknown nested field is rejected
- **WHEN** a filter or projection references a path outside the authorized field snapshot
- **THEN** validation rejects the artifact

### Requirement: Tenant obligations are verified before execution
For pooled tenant profiles, the adapter SHALL require a verified tenant predicate or equivalent trusted obligation in the structured specification; schema/database/deployment profiles SHALL require the corresponding routing evidence.

#### Scenario: Pooled query without tenant predicate is denied
- **WHEN** a pooled tenant query lacks the required verified tenant constraint
- **THEN** governance/adapter validation denies execution