# Observe Multi-Agent handoffs - OpenAI Agent SDK

This standalone lab demonstrates a native OpenAI Agents SDK handoff. A claims
triage agent transfers ownership to a policy-validation specialist, which calls
one real local `lookup_policy` function tool and returns the final result.

Start with the simpler [OpenAI Agents SDK tool integration](../openai-agents-tool-intg/)
if you have not yet seen the single-agent pattern. This directory has its own
requirements, fixture, and runtime; it does not depend on that lab.

## What this demonstrates

- A `Claims Triage Agent` hands ownership to a `Policy Validation Agent` through
  the public `handoff()` / `handoffs` SDK mechanism.
- The handoff tool is explicitly named `transfer_to_policy_validation`.
- The specialist calls local `lookup_policy` and returns the final answer.
- One `Runner.run(...)` invocation maps to one optional BHANUJ Agents Runtime
  execution.

## Workflow

```text
Claims Triage Agent
  → transfer_to_policy_validation (native SDK handoff)
Policy Validation Agent
  → lookup_policy (real local function tool)
  → final response
```

An SDK handoff is an orchestration ownership transition, even though the SDK
exposes it to the model as a tool. Platform therefore records the handoff as an
outgoing and incoming `WORKFLOW_STEP` transition—not as `TOOL_CALL` evidence.
`TOOL_CALL` remains reserved for the real `lookup_policy` invocation.

## Expected evidence timeline

```text
EXECUTION_STARTED
WORKFLOW_STEP  Claims Triage Agent       STARTED
MODEL_CALL     Claims Triage Agent       ordinal 1
WORKFLOW_STEP  Claims Triage Agent       COMPLETED  transition=HANDOFF
WORKFLOW_STEP  Policy Validation Agent   STARTED    transition=HANDOFF
MODEL_CALL     Policy Validation Agent   ordinal 2
TOOL_CALL      lookup_policy
MODEL_CALL     Policy Validation Agent   ordinal 3
WORKFLOW_STEP  Policy Validation Agent   COMPLETED
EXECUTION_COMPLETED  SUCCEEDED
```

The outgoing and incoming workflow-step events have peer semantics: no
`parent_step_id` is manufactured for the handoff. Each agent activation gets a
separate, deterministic execution-local step ID.

Model evidence records only model identifier, agent name, ordinal, latency,
status, and safe usage counts. Tool evidence records only tool identity,
latency, status, and the public tool-call ID when available. The adapter never
sends prompts, conversation history, handoff arguments, model output,
reasoning, tool arguments, tool output, or raw SDK objects.

## Prerequisites

- Python 3.12 or later
- An OpenAI API key and an available model
- BHANUJ OSS only when sending runtime evidence

## Setup and run

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
export OPENAI_API_KEY=...
export OPENAI_MODEL=gpt-5-mini
python app.py
```

The lab pins `openai-agents==0.22.2`. The model’s exact wording can vary, but
the workflow forces the native handoff and local `POL-100` lookup.

## Enable BHANUJ runtime evidence

Start BHANUJ OSS in development mode from its repository:

```bash
AI_GOVERNANCE_AUTH_MODE=development \
  uv run uvicorn ai_governance.api.app:app --reload
```

In another terminal, configure the existing public runtime contract and rerun
the same application:

```bash
export AI_GOVERNANCE_BASE_URL=http://localhost:8000
export AI_GOVERNANCE_ORGANIZATION_ID=org_default
export AI_GOVERNANCE_PROJECT_ID=project_default
python app.py
```

The CLI prints the execution ID and `BHANUJ evidence status: SUCCEEDED`
when all evidence was accepted. Find that ID in Agents Runtime to inspect the
active-agent transition and the single `lookup_policy` tool call.

The adapter reuses only these public endpoints:

- `POST /api/v1/agent-executions`
- `POST /api/v1/agent-executions/{execution_id}/events`
- `POST /api/v1/agent-executions/{execution_id}/complete`

## Failure isolation

Evidence delivery is fail-open: a timeout, rejection, or outage never
changes handoff selection, tool execution, model output, or the original SDK
exception. Delivery uses one short non-retrying attempt and never fabricates a
successful execution.

If the run fails before the handoff, the triage step is marked `FAILED`. If it
fails after the transfer, triage remains `COMPLETED` and only the active policy
step is marked `FAILED`, followed by execution completion with `FAILED`.

## Test

```bash
python -m pytest
```

The credential-free tests use the SDK’s public scripted test model to verify
the actual callback ordering, agent transition, activation-safe IDs, real tool
call, failure paths, and fail-open delivery behavior.

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
    └── test_handoff.py
```

## OpenAI Agents SDK references

- [SDK overview](https://openai.github.io/openai-agents-python/)
- [Lifecycle hooks (`RunHooks`)](https://openai.github.io/openai-agents-python/ref/lifecycle/)
- [Handoffs](https://openai.github.io/openai-agents-python/handoffs/)
- [Function tools](https://openai.github.io/openai-agents-python/tools/)

## License

Licensed under the [Apache License 2.0](LICENSE).
