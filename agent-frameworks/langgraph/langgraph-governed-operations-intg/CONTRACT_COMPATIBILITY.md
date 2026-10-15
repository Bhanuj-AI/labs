# Contract Compatibility Report

This report records the public contracts used by the LangGraph developer guide. It separates verified OSS behavior from Enterprise assumptions so the guide does not create a hidden dependency.

## OSS Contracts

| Capability | Public OSS contract | Lab use | Verification |
| --- | --- | --- | --- |
| Execution registration | `POST /api/v1/agent-executions` | Registers the actual LangGraph run ID as `external_execution_id`, returns canonical AIGP `execution_id`, and retains optional thread ID only as bounded metadata. | Unit tests prove same-thread runs remain distinct; optional integration test retrieves the created execution. |
| Delegation issuance | `POST /api/v1/runtime-action-delegations` | A separately configured issuer principal creates a bounded immutable grant after execution registration. Runtime credentials are never reused for issuance. | Unit test checks the issuer credential and binding; optional integration creates an issuance policy. |
| Runtime authorization | `POST /api/v1/runtime-action-authorizations/authorize` | Sends canonical execution ID, returned delegation ID/version/digest, registered action/resource, tool-argument digest, correlation, and idempotency key. Tenant/actor claims remain transport-owned. | Unit test validates request and PEP argument binding; local API integration can exercise real policy decisions. |
| Event ingestion | `POST /api/v1/agent-executions/{execution_id}/events` | Sends only supported `WORKFLOW_STEP`, `TOOL_CALL`, fixture-only `GOVERNANCE_DECISION`, and `ERROR` events. | Unit tests simulate accepted responses; optional integration test validates real acceptance and retrieval. |
| Event idempotency | Event `idempotency_key` | Node lifecycle events and each actual tool invocation use deterministic keys. | Unit and optional integration tests submit one duplicate payload and expect one logical event. |
| Terminal finalization | `POST /api/v1/agent-executions/{execution_id}/complete` | Uses only `SUCCEEDED`, `FAILED`, and `CANCELLED`. | Unit tests cover all three terminal states; optional integration test reads `SUCCEEDED`. |
| Evidence retrieval | `GET /api/v1/agent-executions/{execution_id}` | The guide uses `curl`; integration test reads execution plus timeline. | Optional integration test. |
| Tenant scope | Existing `X-AI-Governance-Organization-Id`, project, actor, correlation, and request headers | The adapter maps its environment-driven configuration to these headers. | Unit test checks emitted scope/correlation headers. |

The guide deliberately does not write to a BHANUJ database, ontology, graph, replay store, or Enterprise package.

`runtime_tool_call_id` is unique within an execution. The lab therefore emits
exactly one `TOOL_CALL` event, carrying the completed outcome, for each actual
SQLite invocation. An authoritative AIGP allow decision is already persisted as
`RUNTIME_ACTION_AUTHORIZATION`; the tool event cites that decision ID as its
causation ID. Offline fixtures alone use a separate,
`simulated-lab-fixture`-labelled `GOVERNANCE_DECISION` event.

## Runtime Authorization

The lab reuses the OSS versioned runtime-action authorization SDK and its
fail-closed `LangGraphOperationAuthorizer`. It does not call the generic
governance-decision interface. The adapter maps one protected SQLite mutation
to `operations.update_status` on a `sqlite.row` resource and validates the
server-derived actor, canonical execution, immutable delegation, action,
resource, tool-argument digest, idempotency key, and expiry before mutation.

The selected AIGP policy must target `AgentExecution` and opt in through
`runtime_action_authorization` metadata that registers the action and resource
type. The issuer is separately constrained by a
`runtime_action_delegation_issuance` policy. Missing/revoked/out-of-scope
delegation, missing or inactive policy, unregistered operation, denied policy,
invalid response, expiry, tool-argument mismatch, or transport failure blocks
the mutation.

`SimulatedOperationAuthorizer` is retained only for deterministic educational
scenarios and tests. Its evidence is always labelled `simulated-lab-fixture`.

## Enterprise Boundary

No Enterprise package, route, MCP tool, schema, environment flag, or persistence mechanism is imported, configured, or required by this guide. The guide has not verified an Enterprise-specific external operation-authorization API and makes no claim that one exists.

This preserves the OSS extension boundary: a future Enterprise implementation may supply an approved public adapter, but the LangGraph lab must not discover, import, or depend on Enterprise internals to obtain a policy decision.

## Replay and Causal Audit

The OSS runtime supports replay and causal-audit APIs only when the recorded runtime has the required provider capabilities and source/replay contract. This guide sends a provider-neutral version-`1` `ToolCallContext`, which makes the observed tool identity inspectable, but it does not supply a replay provider or controlled evidence resolver.

Accordingly, causal audit, deterministic replay, and counterfactual simulation are documented limitations. Execution finalization in this guide must not be interpreted as evidence sealing or replay eligibility.

## Implementation Sequence

1. Run the read-only LangGraph workflow without any BHANUJ configuration.
2. Configure tenant-scoped REST access and register the same workflow execution.
3. Use the returned canonical execution ID for every authorization, event,
   retrieval, and finalization call.
4. Emit bounded node, completed-tool, error, and terminal facts; connect an
   actual tool event to its authoritative decision when allowed.
5. Exercise AIGP allow/deny/outage behavior and the offline fixtures at the
   single protected-write boundary.
6. Exercise registration/evidence failures and cancelled/failed finalization.
7. Retrieve the persisted OSS timeline and run the optional real-API integration test.
