## Why

The AI runtime currently sends a user prompt and authorized context to a provider, but it has no explicit provider-neutral contract for system instructions. This leaves responsibility for safety rules, Semantic View constraints, output requirements, and instruction versioning ambiguous, making each real provider likely to implement a different system prompt.

## What Changes

- Add an immutable, versioned `ModelInstructionBundle` contract in the core AI boundary.
- Define how system instructions, authorized semantic context, output schema/version, safety rules, and provenance fingerprints are assembled.
- Keep tenant, policy, Semantic View, model bundle, and IR constraints represented as safe references and bounded instructions rather than raw claims or physical queries.
- Add canonical serialization/fingerprinting and strict rejection of credentials, raw SQL/MQL, executable text, hidden policy material, and unbounded instructions.
- Make model invocation carry the instruction bundle or its safe projection without changing the vendor-neutral provider contract semantics.
- Keep provider packages responsible only for mapping the bundle to vendor-specific system/developer/user message formats.
- Add tests for prompt injection, context leakage, versioning, fingerprinting, and provider-neutral serialization.

## Capabilities

### New Capabilities

- `model-instruction-contract`: Provider-neutral system instruction, safety constraint, output schema, provenance, and fingerprint contract.

### Modified Capabilities

- `model-provider-boundary`: Model invocation SHALL carry a validated instruction contract, while vendor providers remain responsible only for transport/message mapping.
- `workflow-runtime-contract`: Workflow evidence SHALL include the instruction bundle fingerprint when a model invocation is performed.

## Impact

Affected areas include `src/nl2data_core/ai`, model context assembly, workflow evidence, provider contract tests, and documentation. The core gains no vendor SDK or HTTP dependency. This change does not implement an OpenAI client, define vendor message formats, or move semantic validation/governance authorization into prompt construction.
