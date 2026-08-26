## Purpose

Define an independently installable MongoDB backend integration implementing the core provider-neutral contracts.

## Requirements

### Requirement: MongoDB adapter implements the generic lifecycle
The MongoDB adapter SHALL implement the canonical `QueryAdapter` lifecycle with `adapter_type="mongodb"`, `query_language="mql"`, explicit async mode, and backend-specific models confined to the specialization package.

#### Scenario: MongoDB adapter satisfies the protocol
- **WHEN** a configured MongoDB adapter is inspected against `QueryAdapter`
- **THEN** it satisfies the generic protocol without adding MongoDB methods to core

### Requirement: MongoDB query specifications are structured and read-only
The adapter SHALL accept only typed specifications for `find`, `aggregate`, and `count_documents` and SHALL reject writes, administrative commands, JavaScript, shell text, unsupported stages, and unbounded operations.

#### Scenario: Write operation is rejected
- **WHEN** a query specification requests insert, update, delete, map-reduce, or an administrative command
- **THEN** validation rejects it before any driver call

#### Scenario: Bounded aggregate is accepted
- **WHEN** a typed aggregate specification uses allowlisted stages and bounded output
- **THEN** it produces a canonical validated artifact fingerprint

### Requirement: MongoDB results are protected scalar results
The adapter SHALL normalize supported BSON values into bounded scalar `ExecutionResult` rows and SHALL reject unsupported native values, raw cursors, native documents, or oversized output before the public boundary.

#### Scenario: Unsupported BSON value fails safely
- **WHEN** execution returns a BSON value outside the configured normalization allowlist
- **THEN** execution returns a normalized safe adapter error without exposing the value

### Requirement: Optional MongoDB dependencies are lazy
The base package SHALL import and test MongoDB models and fake-driver behavior without PyMongo, while real driver profiles SHALL fail as unavailable when the optional driver or service is missing.

#### Scenario: Base import does not require PyMongo
- **WHEN** a user installs the base library without the MongoDB extra
- **THEN** `import nl2data` and generic workflow contracts succeed without importing PyMongo
