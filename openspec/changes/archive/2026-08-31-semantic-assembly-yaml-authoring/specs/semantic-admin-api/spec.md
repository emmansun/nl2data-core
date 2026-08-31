## ADDED Requirements

### Requirement: Admin service validates and imports authoring documents safely
The Admin service SHALL expose bounded `validate_authoring` and `import_authoring` operations. Both operations SHALL require trusted host authentication and authorized tenant/source scope; import SHALL additionally require Assembly Author permission and role. Validation SHALL parse and validate without persistence. Import SHALL delegate deterministic lowering to core, derive the author reference from trusted authorization context, and create the draft through the existing tenant-scoped lifecycle boundary. Neither operation SHALL expose raw secrets, unrestricted parser exceptions, review metadata, native parser objects, or partial drafts.

#### Scenario: Validation has no side effects
- **WHEN** an authorized caller validates a correct authoring document
- **THEN** the service returns bounded normalized metadata and no draft is persisted

#### Scenario: Import creates one clean draft
- **WHEN** an authorized Author imports a valid document with a current source scope
- **THEN** the service creates one revision-zero draft and returns its safe summary without reviewing, approving, or publishing it

#### Scenario: Invalid input returns bounded diagnostics
- **WHEN** parsing, schema, reference, bounds, or safe-content validation fails
- **THEN** the service returns a bounded ordered diagnostic list with safe codes, authoring paths, and optional line/column locations, without echoing secret-like scalar values or raw backend exceptions

#### Scenario: Cross-scope import is denied before persistence
- **WHEN** the document source is outside the caller's authorized source set or tenant scope
- **THEN** import returns the existing safe authorization error and no information about another scope's drafts is revealed

### Requirement: Admin capabilities and schemas advertise authoring operations
The versioned Admin capability and schema surfaces SHALL describe authoring validation and import, including maximum input size, supported authoring API versions, required permissions and lifecycle role, and bounded result DTOs. They SHALL NOT advertise direct authoring-to-publish or authoring-to-approve operations.

#### Scenario: Host discovers authoring prerequisites
- **WHEN** an authorized host reads Admin capabilities or generated service schemas
- **THEN** it can discover supported authoring versions, validation/import operations, and their authorization and size requirements
