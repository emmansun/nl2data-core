## ADDED Requirements

### Requirement: MongoDB adapter produces safe core snapshots
The MongoDB metadata adapter SHALL implement the core metadata discovery contract and return immutable `MetadataSnapshot` values containing allowlisted collections, canonical dotted paths, normalized types/constraints where observed, bounded protected statistics, freshness, completeness, provenance, and canonical fingerprints.

#### Scenario: Authorized discovery returns bounded paths
- **WHEN** an authorized discoverer inspects an allowlisted MongoDB source within configured collection, path, sample, and timeout bounds
- **THEN** it returns a core `MetadataSnapshot` without raw documents, credentials, native clients, or unrestricted sample values

#### Scenario: MongoDB facts remain stable
- **WHEN** equivalent bounded observations are returned in different driver or document mapping orders
- **THEN** the resulting snapshot has equivalent canonical content and fingerprint

### Requirement: MongoDB observations preserve trust and completeness
The adapter SHALL mark dynamic paths according to their evidence as observed or inferred, record incomplete observations when bounded inspection cannot establish a complete schema, and never promote observations to authorization or complete schema authority.

#### Scenario: Bounded sampling is incomplete
- **WHEN** a collection is inspected only through a bounded document/path sample
- **THEN** the snapshot records incomplete/observed semantics and downstream activation can fail closed by policy

#### Scenario: Observation cannot grant access
- **WHEN** an observed or inferred path is present in a snapshot
- **THEN** it cannot independently grant View visibility, tenant access, mandatory filters, or execution authorization

### Requirement: MongoDB access is optional, bounded, and lazy
The adapter SHALL keep `pymongo` optional and lazily loaded, require a read-only/allowlisted discovery configuration, enforce collection/path/sample/time limits, and normalize unavailable, unauthorized, malformed, timeout, and bounds failures.

#### Scenario: Base import remains MongoDB-free
- **WHEN** an application imports `nl2data` without the MongoDB adapter extra
- **THEN** MongoDB modules and `pymongo` are not required or loaded

#### Scenario: Bounds stop discovery safely
- **WHEN** configured collections, nested paths, samples, or elapsed time exceed limits
- **THEN** discovery returns bounded/partial evidence or a normalized failure without continuing unbounded work

### Requirement: MongoDB package is independently installable and compatible
The adapter SHALL expose package-owned configuration and discoverer entry points, depend on the core contract, provide a temporary compatibility path for existing in-core imports where required, and document installation and migration.

#### Scenario: Package output composes with core
- **WHEN** a host installs the adapter and passes its discoverer to the core lifecycle
- **THEN** the discoverer satisfies the core protocol without a second metadata model

#### Scenario: Compatibility path preserves behavior
- **WHEN** an existing host uses the documented in-core discoverer path during the migration window
- **THEN** it receives equivalent normalized snapshots and safe errors while the package path is adopted

### Requirement: MongoDB package executes governed read-only pipelines
The MongoDB backend package SHALL implement the core `QueryAdapter` contract for validated read-only MongoDB pipelines, including lazy client lifecycle, collection/path scope checks, stage/operator validation, bounded results, protected scalar mapping, and normalized execution errors. It SHALL reuse core IR, compiler, guard, governance, authorization, and result-protection boundaries.

#### Scenario: Validated pipeline executes against MongoDB
- **WHEN** the core lifecycle supplies a validated authorized MongoDB artifact and current execution evidence
- **THEN** the package executes it through a read-only client and returns a bounded protected `ExecutionResult`

#### Scenario: Unsafe or unvalidated pipeline never executes
- **WHEN** a pipeline is malformed, unvalidated, outside authorized collection/path scope, contains unsupported operations, exceeds limits, or has stale snapshot evidence
- **THEN** the package rejects it before database execution with a normalized safe error

#### Scenario: MongoDB execution failure is safe
- **WHEN** connection, timeout, permission, unsupported-value, or result-mapping failure occurs
- **THEN** the package returns a normalized failure without URIs, raw pipeline values, credentials, documents, or backend exception text
