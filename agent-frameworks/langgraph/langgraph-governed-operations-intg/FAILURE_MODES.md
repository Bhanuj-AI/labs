# Failure modes and recovery boundaries

This guide keeps agent execution and AIGP evidence delivery separate. LangGraph owns the SQLite operation; the BHANUJ OSS API owns only evidence that it has accepted through its public API. The table below states the observable behavior for every demonstrated failure.

| Condition | Scenario or trigger | LangGraph / SQLite outcome | AIGP evidence behavior | Developer checkpoint |
| --- | --- | --- | --- | --- |
| Registration unavailable | `registration-failure` | Read completes; `OPS-001` remains `PENDING`. | Reporter becomes `DEGRADED`; it cannot append events or fabricate a terminal execution. | CLI prints `READ_COMPLETED`, `DEGRADED`, and no AIGP execution ID. |
| Event ingestion unavailable | `evidence-failure` | Read completes; `OPS-001` remains `PENDING`. | Registration was accepted, then each event delivery retries at most three times. Reporter becomes `DEGRADED` and does not send a terminal success. | CLI prints `READ_COMPLETED`, `DEGRADED`; retrieved execution is incomplete rather than falsely completed. |
| AIGP authorization unavailable or malformed | `aigp` with a timeout, rejected response, unavailable service, or missing SDK/configuration | Protected write fails closed; `OPS-001` remains `PENDING`. | AIGP may have no accepted authorization event. When execution evidence is still healthy, the adapter records bounded `AuthorizationUnavailable` error evidence and finalizes `FAILED`. | CLI prints `GOVERNANCE_UNAVAILABLE`; never treat a retryable evidence status as authorization. |
| AIGP authorization denied or expired | `aigp` with no matching active policy, a deny, expired response, or binding mismatch | No SQLite mutation or `TOOL_CALL`. | AIGP retains `RUNTIME_ACTION_AUTHORIZATION` decision evidence when it returned a decision; denial is a handled workflow outcome. | CLI prints `WRITE_DENIED`; inspect the reason code and decision event. |
| Simulated authorizer unavailable | `outage` | Protected write fails closed; `OPS-001` remains `PENDING`. | When delivery is healthy, records the local simulated `GOVERNANCE_DECISION`, bounded `AuthorizationUnavailable` `ERROR`, then finalizes `FAILED`. | CLI prints `GOVERNANCE_UNAVAILABLE`; retrieved terminal status is `FAILED`. |
| Simulated authorization denied | `denied` | No SQLite tool call or mutation; `OPS-001` remains `PENDING`. | When delivery is healthy, records the simulated denial and finalizes `SUCCEEDED` because the denial is a handled workflow outcome. No `TOOL_CALL` is emitted because no tool was invoked. | CLI prints `WRITE_DENIED`; decision source is `simulated-lab-fixture`. |
| Interrupted lifecycle | `readonly --interrupt-after-read` | No write occurs; `OPS-001` remains `PENDING`. | Emits bounded interruption error evidence when supported, then finalizes `CANCELLED`. | CLI prints `INTERRUPTED`; retrieved terminal status is `CANCELLED`. |
| Workflow exception | Missing SQLite record in the deterministic test | The original exception is raised; no successful operation is reported. | Emits bounded `ERROR` evidence when delivery is healthy, then finalizes `FAILED`. | `test_failed_execution_finalizes_as_failed_and_records_error_evidence` passes. |
| Duplicate event delivery | The duplicate probe in the local-API integration test | No agent operation is retried by this check. | Reuses the exact deterministic idempotency key and payload; OSS returns one logical persisted event. | Integration test retrieves exactly one `probe`/`STARTED` event. |

## Retry and recovery rules

Only transient evidence transport failures are retried, with a maximum of three attempts. Authentication failures, validation failures, and uniqueness conflicts are not retried. A reporter that reaches `DEGRADED` stops subsequent evidence delivery for that workflow run; it does not guess whether the remote service persisted a request.

The `registration-failure` and `evidence-failure` commands use a lab-only fault injector around the normal REST client. They are deterministic demonstrations, not a replacement transport and not fault-injection infrastructure for a deployment.

To retry a real run, begin a **new** LangGraph execution with a new external execution ID after the underlying problem is resolved. Do not reuse an execution ID with changed payloads. Inspect any successfully registered prior execution with the Chapter 6 `GET /api/v1/agent-executions/{execution_id}` command before deciding whether to run again.

The real-API pytest case is deliberately opt-in: it requires `BHANUJ_LAB_INTEGRATION_ENABLED=true` in addition to the target URL. An exported URL alone must never make the deterministic default suite depend on an available server or the caller's ambient credentials. When enabled, a `401` is reported as a configuration error; use the Chapter 2 development-auth server or supply a valid `BHANUJ_LAB_INTEGRATION_ACCESS_TOKEN`.

## What this guide does not recover

- It does not retry or compensate a SQLite mutation after an evidence-delivery failure.
- It does not claim that accepted `TOOL_CALL` evidence proves the external
  resource stayed unchanged after authorization; AIGP is a time-of-check
  decision service and the LangGraph boundary performs enforcement.
- It does not demonstrate causal audit, evidence sealing, deterministic replay, or counterfactual replay.
- It does not access BHANUJ persistence directly to repair, query, or alter evidence.
