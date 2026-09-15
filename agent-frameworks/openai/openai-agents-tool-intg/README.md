# Connect OpenAI Agents SDK workflow to BHANUJ - AI Governance Platform

This small lab uses one OpenAI Agents SDK agent and one local function tool to
validate a policy. It shows a clear ownership boundary: the Agents SDK owns the
agent run and tool routing; BHANUJ OSS receives independent, one-way runtime
evidence.

## What this demonstrates

- One `Policy Validation Agent` built with the public `openai-agents` SDK.
- One local `lookup_policy` function tool backed by `fixtures/policies.json`.
- A named public `tool_choice="lookup_policy"` for the first model turn. The
  SDK resets that choice after the tool runs, allowing the normal final model turn.
- Optional, fail-open Agents Runtime evidence through public REST endpoints.

## Workflow

```text
Policy Validation Agent started
  → model call 1 (lookup_policy forced)
  → lookup_policy (local fixture)
  → model call 2 (final answer)
  → Policy Validation Agent completed
```

When evidence is enabled, one `Runner.run(...)` invocation creates one platform
execution. Its runtime timeline is:

```text
EXECUTION_STARTED
WORKFLOW_STEP  Policy Validation Agent  STARTED
MODEL_CALL     ordinal 1
TOOL_CALL      lookup_policy
MODEL_CALL     ordinal 2
WORKFLOW_STEP  Policy Validation Agent  COMPLETED
EXECUTION_COMPLETED  SUCCEEDED
```

The workflow-step events use `source_kind="openai_agents.agent"`. Model and
tool events contain only model identifier, ordinal, latency, safe token counts,
status, and the public tool-call ID when supplied by the SDK. Prompts,
instructions, messages, tool arguments/results, model output, reasoning, and
raw SDK payloads are never sent.

## Prerequisites

- Python 3.12 or later
- An OpenAI API key and a model available to that key
- The BHANUJ OSS API only when you want to send runtime evidence

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Use `.env.example` as a reference, then set these two values in your shell:

```bash
export OPENAI_API_KEY=...
export OPENAI_MODEL=gpt-5-mini
```

This lab pins `openai-agents==0.22.2`.

## Run the agent

```bash
python app.py
```

The exact wording comes from the configured model, but it performs the local
`POL-100` lookup and reports an active policy. No BHANUJ connection is needed
for this run.

## Enable BHANUJ runtime evidence

Start BHANUJ OSS in development mode from its repository:

```bash
AI_GOVERNANCE_AUTH_MODE=development \
  uv run uvicorn ai_governance.api.app:app --reload
```

Then set the deployment’s supported runtime scope headers through these lab
variables and run the same command again:

```bash
export AI_GOVERNANCE_BASE_URL=http://localhost:8000
export AI_GOVERNANCE_ORGANIZATION_ID=org_default
export AI_GOVERNANCE_PROJECT_ID=project_default
python app.py
```

The app prints a BHANUJ execution ID and `BHANUJ evidence status: SUCCEEDED`
when the complete evidence sequence is accepted. Find that execution ID in
Agents Runtime to inspect the same ordered event timeline.

The adapter reuses these public contracts:

- `POST /api/v1/agent-executions`
- `POST /api/v1/agent-executions/{execution_id}/events`
- `POST /api/v1/agent-executions/{execution_id}/complete`

## Failure behavior

Evidence delivery is deliberately fail-open. A connection failure, timeout, or
rejected evidence request never changes the OpenAI agent output, local tool
result, SDK routing, or application exception. Delivery uses one bounded
attempt with no retries. It never reports a fabricated successful execution.

If the agent itself fails after its step started, the adapter attempts a
`WORKFLOW_STEP FAILED` event followed by execution completion with `FAILED`.

## Test

```bash
python -m pytest
```

The tests are credential-free. They cover the successful timeline, stable
execution correlation, safe model/tool evidence, the named first-turn tool
choice, and fail-open delivery behavior.

## Repository structure

```text
.
├── .env.example
├── .gitignore
├── LICENSE
├── README.md
├── agent.py
├── app.py
├── fixtures/
│   └── policies.json
├── governance.py
├── policy.py
├── requirements.txt
└── tests/
    └── test_governance.py
```

## OpenAI Agents SDK references

- [SDK overview](https://openai.github.io/openai-agents-python/)
- [Lifecycle hooks (`RunHooks`)](https://openai.github.io/openai-agents-python/ref/lifecycle/)
- [Function tools](https://openai.github.io/openai-agents-python/tools/)
- [Agent configuration](https://openai.github.io/openai-agents-python/ref/agent/)

## License

Licensed under the [Apache License 2.0](LICENSE).
