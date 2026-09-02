## ADDED Requirements

### Requirement: Admin service exposes bounded audit-evidence inspection
The Admin service SHALL expose authorized audit-evidence inspection operations for assembly drafts, assertions, publications, Bundle fingerprints, activations, and rollbacks. Inspection SHALL require trusted host authentication, authorized tenant/source scope, and the relevant read or audit permission. Results SHALL be ordered, bounded, cursor-capable when needed, and contain only safe audit-evidence entries, trail metadata, counts, fingerprints, statuses, and opaque host audit references.

#### Scenario: Draft trail inspection is scoped and bounded
- **WHEN** an authorized caller requests the audit-evidence trail for a draft ID and optional revision range
- **THEN** the service returns only entries in the caller's tenant/source scope, ordered by lifecycle sequence, with bounded count and no unsafe payload material

#### Scenario: Assertion trail inspection explains review history
- **WHEN** an authorized caller requests the trail for one assertion ID within a draft or published manifest
- **THEN** the service returns bounded review/edit/approval/publication evidence for that assertion without exposing raw reviewed content beyond safe payload hashes and references

#### Scenario: Publication trail inspection links readiness evidence
- **WHEN** an authorized caller requests audit evidence for a published Bundle fingerprint
- **THEN** the response links publication, accepted manifest, verification evidence, lint readiness reference when present, activation or rollback entries, and publish audit reference using safe fingerprints and statuses only

### Requirement: Admin audit inspection never mutates lifecycle state
Admin audit-evidence inspection SHALL be side-effect-free. It SHALL NOT create review decisions, approval bindings, lint results, verification evidence, audit entries, publications, activations, rollbacks, or retention changes.

#### Scenario: Inspection has no side effects
- **WHEN** a caller inspects audit evidence for a draft, assertion, publication, activation, or rollback
- **THEN** all lifecycle revisions, review states, active pointers, publication records, verification evidence, and audit-evidence entries remain unchanged

### Requirement: Admin capabilities and schemas advertise audit-evidence operations
The versioned Admin capability and schema surfaces SHALL describe supported audit-evidence inspection operations, required permissions, subject lookup keys, result bounds, cursor behavior when applicable, and redaction guarantees. Unsupported raw event-log export or natural-language query execution SHALL NOT be advertised as an Admin audit operation.

#### Scenario: Host discovers audit inspection prerequisites
- **WHEN** an authorized host reads Admin capabilities or generated service schemas
- **THEN** it can discover audit-evidence inspection operations, their authorization requirements, supported subject keys, maximum result bounds, and safe result DTOs
