# Contract Compatibility Report

This report records the public contracts used by the LangGraph developer guide. It separates verified OSS behavior from Enterprise assumptions so the guide does not create a hidden dependency.

## OSS Contracts

| Capability | Public OSS contract | Lab use | Verification |
| --- | --- | --- | --- |
| Execution registration | `POST /api/v1/agent-executions` | Registers `runtime_provider=langgraph`, a stable external execution ID, correlation ID, and bounded metadata. | Optional integration test retrieves the created execution. |
| Event ingestion | `POST /api/v1/agent-executions/{execution_id}/events` | Sends only supported `WORKFLOW_STEP`, `TOOL_CALL`, `GOVERNANCE_DECISION`, and `ERROR` events. | Unit tests simulate accepted responses; optional integration test validates real acceptance and retrieval. |
| Event idempotency | Event `idempotency_key` | Node lifecycle events and each actual tool invocation use deterministic keys. | Unit and optional integration tests submit one duplicate payload and expect one logical event. |
| Terminal finalization | `POST /api/v1/agent-executions/{execution_id}/complete` | Uses only `SUCCEEDED`, `FAILED`, and `CANCELLED`. | Unit tests cover all three terminal states; optional integration test reads `SUCCEEDED`. |
| Evidence retrieval | `GET /api/v1/agent-executions/{execution_id}` | The guide uses `curl`; integration test reads execution plus timeline. | Optional integration test. |
| Tenant scope | Existing `X-AI-Governance-Organization-Id`, project, actor, correlation, and request headers | The adapter maps its environment-driven configuration to these headers. | Unit test checks emitted scope/correlation headers. |

The guide deliberately does not write to a BHANUJ database, ontology, graph, replay store, or Enterprise package.

`runtime_tool_call_id` is unique within an execution. The lab therefore emits exactly one `TOOL_CALL` event, carrying the completed outcome, for each actual SQLite invocation. It records the preceding simulated authorization as a separate `GOVERNANCE_DECISION`; it does not invent a request-phase tool event with the same native identity.

## Policy Authorization Gap

OSS has a generic governance-decision interface, but its target vocabulary covers platform governance entities such as candidates, experiments, prompt/model/dataset versions, evaluation results, jobs, and governance decisions. It is not a public contract for externally authorizing an operational database action with actor, tenant, action, resource, and execution facts.

Therefore this guide does **not** call the generic decision interface to authorize SQLite. `SimulatedOperationAuthorizer` is a local deterministic fixture. Its decision observations are labelled `simulated-lab-fixture` and never presented as BHANUJ policy decisions.

An authoritative future integration would require a separately approved, versioned public operation-authorization contract. It must be introduced by the control plane, not invented by this lab.

## Enterprise Boundary

No Enterprise package, route, MCP tool, schema, environment flag, or persistence mechanism is imported, configured, or required by this guide. The guide has not verified an Enterprise-specific external operation-authorization API and makes no claim that one exists.

This preserves the OSS extension boundary: a future Enterprise implementation may supply an approved public adapter, but the LangGraph lab must not discover, import, or depend on Enterprise internals to obtain a policy decision.

## Replay and Causal Audit

The OSS runtime supports replay and causal-audit APIs only when the recorded runtime has the required provider capabilities and source/replay contract. This guide sends a provider-neutral version-`1` `ToolCallContext`, which makes the observed tool identity inspectable, but it does not supply a replay provider or controlled evidence resolver.

Accordingly, causal audit, deterministic replay, and counterfactual simulation are documented limitations. Execution finalization in this guide must not be interpreted as evidence sealing or replay eligibility.

## Implementation Sequence

1. Run the read-only LangGraph workflow without any BHANUJ configuration.
2. Configure tenant-scoped REST access and register the same workflow execution.
3. Emit bounded node, completed-tool, simulated-decision, error, and terminal facts.
4. Exercise local simulated allow/deny/outage behavior at the single protected-write boundary.
5. Exercise registration/evidence failures and cancelled/failed finalization.
6. Retrieve the persisted OSS timeline and run the optional real-API integration test.
