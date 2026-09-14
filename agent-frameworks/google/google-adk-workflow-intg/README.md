# Integrate Google ADK Workflow with BHANUJ

This lab is a small, deterministic [Google Agent Development Kit (ADK)](https://google.github.io/adk-docs/) workflow with optional BHANUJ Agents Runtime evidence. It uses eight local `FunctionNode` stages, an in-memory ADK session, and fixture data only—no model, cloud account, database, or credentials are needed.

## What this demonstrates

- A real Google ADK [Workflow] (https://adk.dev/workflows/) with explicit `FunctionNode` stages.
- A deterministic insurance-claim result: `APPROVED` only when the policy is active and damage is verified.
- A fail-open, one-way evidence boundary: Google ADK still owns execution and its business result.
- An explicit mapping of each ADK node to `WORKFLOW_STEP` evidence with `source_kind="google_adk.node"`.

## Workflow

```text
START
  |
  v
validate_claim → load_policy → check_coverage → check_policy
  |
  v
calculate_payout → record_decision → notify_customer → complete_claim
  |
  v
END
```

When evidence is enabled, every one of the eight nodes emits a paired `STARTED` and `COMPLETED` `WORKFLOW_STEP` event. The sample makes no external tool invocation, so it emits **zero `TOOL_CALL` events**.

## Prerequisites

- Python 3.12 or later
- `pip`

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

The dependency is pinned to `google-adk==2.9.0`.

## Run the standalone workflow

```bash
python app.py
```

Expected output:

```text
Claim: CLM-001
Policy active: true
Damage verified: true
Decision: APPROVED
```

## Enable BHANUJ runtime evidence

Start the AI Governance Control Plane OSS API in development mode from its repository:

```bash
AI_GOVERNANCE_AUTH_MODE=development \
  uv run uvicorn ai_governance.api.app:app --reload
```

Then configure the lab in another terminal:

```bash
export AI_GOVERNANCE_BASE_URL=http://localhost:8000
export AI_GOVERNANCE_ORGANIZATION_ID=org_default
export AI_GOVERNANCE_PROJECT_ID=project_default
python app.py
```

The CLI prints a BHANUJ execution ID when the execution-start request is accepted. Use that ID in Agents Runtime to inspect the evidence timeline.

The lab reuses the public Agents Runtime contracts:

- `POST /api/v1/agent-executions` creates an observed execution with the root ADK invocation ID as both `external_execution_id` and `correlation_id`.
- `POST /api/v1/agent-executions/{execution_id}/events` appends ordered `WORKFLOW_STEP` events.
- `POST /api/v1/agent-executions/{execution_id}/complete` completes the evidence record.

Each event uses a stable hash of the public ADK invocation ID, run ID, and node path. It records bounded `adk_node_path`, `adk_run_id`, and attempt-count facts only. It does not send the raw claim fixture or an inferred tool call.

### Delivery failure behaviour

Evidence is deliberately fail-open. If BHANUJ is unavailable, the adapter preserves the ADK node return value, exception, state, and routing. It does not create a synthetic successful completion; Agents Runtime retains only the evidence it accepted.

## Test

```bash
python -m pytest
```

The tests cover the approved decision, both rejected decisions, all eight ADK node lifecycle pairs, stable IDs, and the absence of inferred tool calls.

## Repository structure

```text
.
├── .env.example
├── .gitignore
├── LICENSE
├── README.md
├── app.py
├── fixtures/
│   └── claim.json
├── governed_workflow.py
├── requirements.txt
├── tests/
│   └── test_workflow.py
└── workflow.py
```

## License

Licensed under the [Apache License 2.0](LICENSE).
