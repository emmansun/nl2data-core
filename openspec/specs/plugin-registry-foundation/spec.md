## Purpose

Define the validated plugin manifest registry with bounded discovery and lifecycle rules.

## Requirements

### Requirement: Plugin manifests are validated
The plugin foundation SHALL validate manifest schema version, identity, package entry point, categories, compatibility declarations, capabilities, permissions, and content digest format.

#### Scenario: Invalid manifest is rejected
- **WHEN** a manifest omits required identity data or declares malformed compatibility information
- **THEN** registry registration fails before activation

### Requirement: Plugin descriptors are immutable
The registry SHALL store immutable plugin descriptors containing resolved identity, manifest fingerprint, capabilities, granted permissions, and activation status.

#### Scenario: Registered descriptor cannot be mutated
- **WHEN** application code attempts to alter a registered plugin descriptor
- **THEN** the mutation is rejected and registry state remains unchanged

### Requirement: Capability resolution is explicit
The registry SHALL resolve plugins by declared capability and compatible version, and SHALL fail closed when required permission or contract compatibility is absent.

#### Scenario: Incompatible capability is not resolved
- **WHEN** a plugin declares a capability outside the requested contract version range
- **THEN** resolution returns no usable plugin and does not activate the incompatible declaration

### Requirement: P0 does not execute plugin code
P0 registration and resolution SHALL NOT import, install, or execute arbitrary plugin code.

#### Scenario: Registry is declarative
- **WHEN** a valid manifest is registered
- **THEN** only validated descriptor data is stored and no plugin entry point is invoked
