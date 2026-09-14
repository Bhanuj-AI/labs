# Govern a LangGraph workflow with BHANUJ - AI Governance Platform

This repository is the starting point for the **Govern a LangGraph workflow
with BHANUJ - AI Governance Platform** guide. It uses the AI Governance Control Plane
Agents Runtime API to observe one external LangGraph execution without
controlling it.

## What this example demonstrates

- Defining a sequential workflow with LangGraph nodes.
- Passing local fixture data through workflow state.
- Producing an explainable, deterministic insurance-claim decision without an
  LLM, cloud service, database, or credentials.
- Reporting bounded execution and LangGraph-node lifecycle evidence to Agents
  Runtime when explicitly configured.

## Architecture

```text
START
  |
  v
load_claim
  |
  v
check_policy
  |
  v
evaluate_evidence
  |
  v
make_decision
  |
  v
END
```

The decision is `APPROVED` only when both `policy_active` and
`damage_verified` are true. All other combinations are `REJECTED`.

When enabled, a separate reporter wraps each node. LangGraph still executes
the workflow; the reporter sends evidence in one direction to Agents Runtime.
It never changes the claim state or decision.

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

The `.env.example` file is included for guide consistency, but this example
does not need environment variables for its standalone workflow.

## Run

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

## Enable Agents Runtime evidence

Start the AI Governance Control Plane API in its documented development mode
from the OSS repository:

```bash
AI_GOVERNANCE_AUTH_MODE=development \
  uv run uvicorn ai_governance.api.app:app --reload
```

Then set the environment values in `.env.example` (or export them in your
shell):

```bash
export AI_GOVERNANCE_BASE_URL=http://localhost:8000
export AI_GOVERNANCE_ORGANIZATION_ID=org_default
export AI_GOVERNANCE_PROJECT_ID=project_default
python app.py
```

The runtime will print the platform execution ID and delivery status. Use that
ID to inspect the timeline with:

```bash
curl "$AI_GOVERNANCE_BASE_URL/api/v1/agent-executions/EXECUTION_ID" \
  -H "X-AI-Governance-Organization-Id: $AI_GOVERNANCE_ORGANIZATION_ID" \
  -H "X-AI-Governance-Project-Id: $AI_GOVERNANCE_PROJECT_ID"
```

The reporter uses only the existing public contracts:

- `POST /api/v1/agent-executions` to start one observed execution.
- `POST /api/v1/agent-executions/{execution_id}/events` for ordered
  `WORKFLOW_STEP` evidence.
- `POST /api/v1/agent-executions/{execution_id}/complete` to mark successful
  evidence delivery complete.

Each LangGraph node is a provider-neutral `WORKFLOW_STEP`, not a tool call.
Every node sends stable `step_id`, `step_name`, `lifecycle`, and
`source_kind="langgraph.node"` fields. The paired `STARTED` and `COMPLETED`
events use the same step ID. Node completions include only bounded facts: the
policy and damage booleans or final decision, never the raw claim payload.

This deterministic workflow sends no `TOOL_CALL` events because it invokes no
external tools. A future node that makes a real tool invocation would emit its
own nested `TOOL_CALL` evidence.

The Studio timeline for a successful run contains:

```text
load_claim · Step Started
load_claim · Step Completed
check_policy · Step Started
check_policy · Step Completed
evaluate_evidence · Step Started
evaluate_evidence · Step Completed
make_decision · Step Started
make_decision · Step Completed
```

### Delivery failure behaviour

Instrumentation is fail-open. If the governance API is unavailable or rejects
delivery, the LangGraph workflow still returns its own decision. The console
reports `DEGRADED`; the reporter stops sending events and does not send a
synthetic successful completion. This leaves only the evidence actually
accepted by Agents Runtime.

Run the tests with:

```bash
python -m pytest
```

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
├── governance.py
├── requirements.txt
├── tests/
│   ├── test_governance.py
│   └── test_workflow.py
└── workflow.py
```

## License

Licensed under the [Apache License 2.0](LICENSE).
