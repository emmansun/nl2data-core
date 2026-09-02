## MODIFIED Requirements

### Requirement: Durable verification evidence is safe and reloadable
The durable catalog SHALL persist a versioned bounded Verification Suite evidence envelope and an immutable publication-time `FrozenReleaseBinding` atomically with publication. The frozen binding SHALL contain the approved draft identity/revision, approved verification-plan fingerprint, tenant/source scope fingerprints, manifest fingerprint, Bundle fingerprint, policy profile/version, runner identity/version, and executor identity/capability fingerprint needed for future validation. Reload SHALL validate evidence against this immutable binding, the immutable Bundle/manifest/audit records, and envelope metadata; it SHALL NOT require or compare the current mutable assembly draft row. The envelope and binding SHALL contain no raw rows, scalar values, SQL/MQL, prompts, physical names, deployment references, credentials, native values, backend exception text, or mutable review state.

#### Scenario: Verification evidence survives restart
- **WHEN** a verified Bundle is published and a new catalog worker starts
- **THEN** the worker can retrieve the same bounded suite evidence identity and layer summary linked to the immutable publication fingerprint

#### Scenario: Later draft evolution does not invalidate history
- **WHEN** the originating draft returns to review, changes revision or verification plan, or is otherwise replaced after a successful publication
- **THEN** verification evidence for the prior immutable publication remains readable and validates against its frozen release binding

#### Scenario: Tampered evidence or frozen binding fails closed
- **WHEN** persisted verification evidence or its frozen release binding has an unsupported version or mismatched plan, runner, executor, manifest, approved revision, tenant/source, or Bundle fingerprint
- **THEN** reload and activation reject the publication evidence without consulting mutable draft state, exposing partial evidence, or changing the active pointer

#### Scenario: Legacy evidence migration is explicit
- **WHEN** an older publication has verification evidence but no frozen release binding
- **THEN** the catalog classifies it through an explicit legacy compatibility path or additive backfill procedure and never fabricates a production-valid binding from the current mutable draft
