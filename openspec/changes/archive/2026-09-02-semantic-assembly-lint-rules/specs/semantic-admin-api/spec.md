## ADDED Requirements

### Requirement: Admin service exposes safe semantic assembly lint operations
The Admin service SHALL expose authorized side-effect-free lint operations for authoring documents and existing assembly drafts. Lint operations SHALL require trusted host authentication and authorized tenant/source scope. Draft lint SHALL require read permission and an expected draft revision. Authoring lint SHALL parse and validate input safely before linting and SHALL persist nothing. Results SHALL contain bounded lint profile metadata, diagnostic counts, blocking status, and ordered safe diagnostics only.

#### Scenario: Authoring lint has no persistence side effect
- **WHEN** an authorized caller lints a valid authoring document
- **THEN** the service returns bounded lint diagnostics and no draft, review state, verification evidence, audit record, publication, or activation state is created or changed

#### Scenario: Draft lint is revision guarded
- **WHEN** an authorized caller lints an existing draft with the current expected revision
- **THEN** the service returns lint diagnostics for that revision and leaves the draft unchanged

#### Scenario: Stale draft lint is rejected safely
- **WHEN** a caller submits draft lint with an outdated expected revision
- **THEN** the service returns the existing safe conflict response and does not return diagnostics for a different revision

#### Scenario: Admin lint result is bounded and redacted
- **WHEN** lint diagnostics reference sensitive labels, deployment bindings, value semantics, or verification-plan material
- **THEN** the Admin response includes only safe diagnostic codes, severities, paths, source marks, counts, profile metadata, and bounded messages without credentials, physical names, SQL/MQL, raw rows, native objects, or unrestricted scalar values