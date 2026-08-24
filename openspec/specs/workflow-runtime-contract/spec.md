# workflow-runtime-contract Specification

## Purpose
TBD - created by archiving change establish-p2-governed-workflow-runtime. Update Purpose after archive.
## Requirements
### Requirement: Core workflow runtime is framework-neutral
The system SHALL define a provider-neutral `WorkflowRuntime` contract with typed immutable execution context, stage results, deadlines, cancellation, safe errors, and protected evidence, without requiring LangGraph or another workflow framework.

#### Scenario: Runtime contract imports without LangGraph
- **WHEN** the core package is installed without LangGraph
- **THEN** the workflow runtime contracts import and deterministic runtime composition remains usable

### Requirement: Workflow stages and mandatory gates are explicit
The runtime SHALL expose ordered stages for memory, intent, IR building, IR/view validation, deterministic compilation, artifact guarding, governance, authorization, execution, protection, persistence, and completion, and SHALL reject execution when a required prior gate is missing, stale, or inconsistent.

#### Scenario: Adapter cannot bypass governance
- **WHEN** a workflow attempts to execute before current IR/view validation, compilation, artifact guard, governance, and authorization gates pass
- **THEN** the adapter is not invoked and a safe workflow rejection is produced

#### Scenario: Compiler cannot bypass artifact guard
- **WHEN** a compiler returns a physical artifact without a successful backend guard result
- **THEN** the workflow stops before governance authorization and adapter execution

### Requirement: Cancellation and deadlines are bounded
Every stage that can perform external work SHALL receive a bounded deadline/cancellation context and SHALL produce a typed timeout or cancellation outcome without leaking native task/provider objects. The runtime SHALL reverify compiler, artifact, governance, authorization, and effective-limit evidence immediately before adapter execution.

#### Scenario: Cancelled workflow stops before execution
- **WHEN** cancellation is observed after IR validation but before adapter execution
- **THEN** no adapter call starts and the workflow returns a safe cancelled outcome

#### Scenario: Stale authorization stops execution
- **WHEN** policy, view, capability, tenant, artifact, or limit evidence changes after authorization issuance but before execution
- **THEN** the workflow rejects the request before adapter access

