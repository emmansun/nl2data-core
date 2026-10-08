## Why

WrenAI MDL is a useful existing modeling format, while NL2Data already has a bounded semantic-assembly authoring format and lifecycle. An offline, fail-closed importer lets teams reuse supported MDL semantics without adding a competing modeling language or bypassing the existing authoring validation and review gates.

## What Changes

- Add an offline CLI that converts an explicitly supported WrenAI MDL subset into semantic assembly authoring YAML.
- Define deterministic mappings for supported models/entities, fields, relationships, and simple measures; require explicit caller input for source and bundle identity rather than inferring deployment or lifecycle authority.
- Return stable, source-located mapping error codes for unsupported constructs, ambiguous references, invalid expressions, and authoring-validation failures. Never silently drop or approximate unsupported semantics.
- Validate generated YAML with the existing `SemanticAssemblyAuthoringLoader` before writing it; write output atomically only after the complete conversion and validation succeed.
- Document supported MDL constructs, mapping rules, CLI usage, and error codes. No network calls, catalog introspection, draft persistence, review, approval, or publication are part of the importer.

## Capabilities

### New Capabilities

- `wren-mdl-importer`: Bounded offline conversion of supported WrenAI MDL semantics to validated semantic assembly authoring YAML, with deterministic output and explicit mapping diagnostics.

### Modified Capabilities

None. The importer targets the existing authoring schema and loader without changing their contract.

## Impact

- New importer module and CLI entry point, reusing the existing semantic assembly authoring models, loader, validator, and exporter.
- New fixtures for supported and rejected MDL inputs, deterministic conversion tests, CLI tests, and user documentation.
- No runtime query path, adapter, semantic assembly lifecycle, YAML schema, or network dependency changes.