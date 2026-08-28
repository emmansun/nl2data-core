# semantic-model-bundles Delta

## ADDED Requirements

### Requirement: New optional semantic members are fingerprint-stable when unset
Introducing a new optional semantic member (value semantics, and later calculated fields or metrics) SHALL NOT change the fingerprint of any bundle whose contents do not declare the member. The member's content SHALL enter the fingerprint domain only when set.

#### Scenario: Bundles without value semantics keep their identity
- **WHEN** value-semantics support lands and an existing bundle's fields declare none
- **THEN** the bundle fingerprint and all bundle-derived fingerprints are unchanged

#### Scenario: Descriptor-level fingerprints feed snapshot compatibility unchanged
- **WHEN** a descriptor leaves the new member unset
- **THEN** its catalog fingerprint is unchanged and existing snapshot compatibility relationships hold

### Requirement: ValueSemantics content changes require republication against the new snapshot
A bundle whose descriptor adopts or edits any ValueSemantics content SHALL be republished against the resulting catalog snapshot; validation against a newer snapshot without republication SHALL fail closed with a compatibility issue. The documented upgrade path SHALL cover: content change, catalog snapshot fingerprint change, bundle republication, and stale-evidence re-audit.

#### Scenario: Old-snapshot bundle fails validation after a content change
- **WHEN** a descriptor's ValueSemantics content is edited and a bundle built from the prior snapshot is validated against the new snapshot
- **THEN** validation fails with a compatibility issue naming the snapshot mismatch

#### Scenario: Republication restores validity
- **WHEN** the bundle is rebuilt and republished against the new snapshot
- **THEN** validation succeeds and previously issued evidence for the old bundle identity is treated as stale
