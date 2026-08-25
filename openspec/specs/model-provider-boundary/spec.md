# model-provider-boundary Specification

## Purpose
TBD - created by archiving change establish-p2-ai-runtime-boundary. Update Purpose after archive.
## Requirements
### Requirement: Provider-neutral bounded model invocation
The system SHALL define an asynchronous `ModelProvider` contract that accepts an immutable bounded invocation request and returns a typed structured response envelope or a normalized safe model error without exposing provider-native clients or exceptions. Real provider packages, including the OpenAI provider, SHALL implement this contract without moving intent validation, governance, authorization, or retry policy into the provider.

#### Scenario: Deterministic fake provider satisfies the contract
- **WHEN** a fake provider receives a valid invocation request
- **THEN** it returns a reproducible structured response with stable usage metadata and no network dependency

#### Scenario: Real provider is substitutable
- **WHEN** a real provider receives a valid invocation request
- **THEN** it returns the same transport-neutral `ModelResponse` shape or a normalized `ModelInvocationError` without exposing vendor types

#### Scenario: Provider call exceeds a bound
- **WHEN** an invocation exceeds its timeout, attempt, input, or output bound
- **THEN** the call ends with a safe structured model error and no unbounded retry occurs

### Requirement: Provider output is immutable and fingerprintable
Model invocation configuration, structured output, usage metadata, and normalized errors SHALL reject unknown fields, remain immutable, and expose canonical fingerprints that exclude secrets and raw credentials.

#### Scenario: Equivalent responses have the same fingerprint
- **WHEN** equivalent provider responses are serialized with different mapping insertion orders
- **THEN** their protected fingerprints are identical

#### Scenario: Provider credentials never enter evidence
- **WHEN** a provider error contains credential or connection information
- **THEN** the normalized error contains only safe error code, category, message, and redacted details

### Requirement: Vendor providers remain outside the core dependency graph
The core and public package SHALL not import or require vendor model SDKs. Vendor implementations SHALL be optional packages behind the provider protocol and SHALL be importable or installable independently.

#### Scenario: Core import boundary remains clean
- **WHEN** an application imports `nl2data` and `nl2data_core.ai` without any vendor extra
- **THEN** no OpenAI or other vendor SDK is loaded and the fake provider remains usable

