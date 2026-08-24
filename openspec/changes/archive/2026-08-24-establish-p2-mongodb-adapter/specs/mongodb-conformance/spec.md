## ADDED Requirements

### Requirement: MongoDB conformance is deterministic and protected
The conformance suite SHALL cover typed find, aggregate, count, invalid stages/operators, collection/field scope, BSON normalization, limits, tenant obligations, and safe evidence using fixed fake-driver fixtures.

#### Scenario: Repeated fake-driver conformance is stable
- **WHEN** the same specification, metadata snapshot, tenant scope, and fake documents are evaluated twice
- **THEN** artifact fingerprints, protected results, and mandatory assertions are identical

### Requirement: Optional real MongoDB availability is explicit
Real MongoDB integration SHALL be skipped or reported unavailable when the optional driver/service is absent and SHALL never be reported as passing under those conditions.

#### Scenario: Missing MongoDB service is not a pass
- **WHEN** the real MongoDB profile cannot connect
- **THEN** the case is marked unavailable/skipped with safe evidence and no success result

### Requirement: SQL/Mongo semantic equivalence is result based
Where shared logical fixture cases exist, conformance SHALL compare protected logical columns/rows and policy outcomes rather than SQL/MQL text.

#### Scenario: Equivalent logical queries match
- **WHEN** SQL and Mongo specifications answer the same controlled fixture question
- **THEN** their protected logical result shapes and values match