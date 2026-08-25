## Why

The project has accumulated substantial runtime, governance, metadata, workflow, and provider capabilities, but most guidance is concentrated in a long README and OpenSpec artifacts intended for development. New users, integrators, and maintainers need task-oriented documentation that explains how to install, compose, operate, extend, and troubleshoot the library without reading historical design material.

## What Changes

- Reorganize documentation into task-oriented user, developer, architecture, operations, and reference sections under `docs/`.
- Provide a staged bilingual documentation path: English is the normative source, while Chinese covers the highest-value entry, architecture, fingerprint, configuration, and security pages with explicit synchronization status.
- Reduce the root README to a concise product overview, installation path, minimal example, support matrix, limitations, and documentation links.
- Add a complete quickstart covering public facade composition and a governed query flow.
- Add architecture documentation for Semantic IR, Semantic View/Bundle, compiler/governance boundaries, tenant isolation, workflow state, Memory, and package boundaries.
- Add developer guidance for environment setup, testing, adding adapters/providers, and local service profiles.
- Add operations guidance for PostgreSQL shared state, Redis Memory, metadata discovery/drift, secrets, health, rollback, and troubleshooting.
- Add reference documentation for configuration, error codes, capability/support matrix, compatibility, and package installation.
- Align `packages/nl2data-openai/README.md` with the provider installation and local live-test guidance.
- Add documentation validation to CI for Markdown links, example syntax, and generated/package consistency where practical.

## Capabilities

### New Capabilities

- `production-documentation`: Structured user, developer, architecture, operations, and reference documentation for the supported library capabilities.

### Modified Capabilities

- `public-library-conformance`: Documentation examples SHALL use only supported public imports and verified installation paths.
- `configuration-foundation`: Configuration reference documentation SHALL reflect the implemented bounded configuration models and optional dependency profiles.

## Impact

Affected files include `README.md`, `packages/nl2data-openai/README.md`, new `docs/` content, and CI documentation checks. No runtime code, public API, dependency, or behavior changes are required. OpenSpec artifacts remain the design/change history and are not duplicated verbatim as end-user documentation.
