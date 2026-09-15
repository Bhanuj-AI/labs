# Connect a Claude Agent SDK workflow to BHANUJ Platform

This small lab runs a real Claude Agent SDK workflow that calls one local
`lookup_policy` tool and returns a natural-language policy decision. It has no
database, cloud service, web access, filesystem tool, or subagent.

The example demonstrates that **Claude Agent SDK owns execution** while
**BHANUJ Platform records provider-neutral runtime evidence** independently.
BHANUJ OSS provides the Agent Executions API used when evidence is enabled.

## What Claude Agent SDK does here

Claude Agent SDK exposes custom Python tools through an in-process MCP server.
This lab creates that server in the same process, gives Claude only the
`mcp__policy__lookup_policy` tool, and reads deterministic data from
`fixtures/policies.json`.

```text
Claude Policy Validation Agent
  -> Claude model call
  -> lookup_policy (local in-process MCP tool)
  -> Claude model call
  -> final answer
                 |
                 v
        BHANUJ Platform runtime evidence
```

Useful Claude references: [Agent SDK Python guide](https://code.claude.com/docs/en/agent-sdk/python),
[hooks](https://code.claude.com/docs/en/agent-sdk/hooks), and the
[Agent SDK overview](https://code.claude.com/docs/en/agent-sdk).

## Prerequisites

- Python 3.11+
- An Anthropic API key with access to the model you select
- `ANTHROPIC_API_KEY` and `CLAUDE_MODEL` set in your shell
- Optionally, a running BHANUJ OSS instance for Agents Runtime evidence

## Setup

```bash
cd labs/agent-frameworks/anthropic/claude-agent-tool-intg
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Use `.env.example` as a reference, then set `ANTHROPIC_API_KEY` and
`CLAUDE_MODEL` in your environment. Do not commit a credential-bearing `.env`
file if you choose to maintain one locally.

## Run without BHANUJ Platform

Leave all `AI_GOVERNANCE_*` values empty and run:

```bash
python app.py
```

Claude uses `lookup_policy` exactly once for `POL-1001`, then prints its final
answer. The exact wording is model-generated; the policy fixture is
deterministic.

```text
Policy: POL-1001
Claude session ID: <uuid>
Final answer: Policy POL-1001 is active, so the claim can proceed.
BHANUJ Platform evidence: disabled
```

## Enable BHANUJ Platform runtime evidence

Set the optional values in your shell (or source an equivalent local file):

```ini
AI_GOVERNANCE_BASE_URL=http://localhost:8000
AI_GOVERNANCE_ORGANIZATION_ID=org_default
AI_GOVERNANCE_PROJECT_ID=project_default
```

Use the tenancy/authentication settings required by your BHANUJ OSS deployment.
Then run the same command. The caller-created public Claude `session_id` is the
stable external execution ID. If delivery succeeds, the terminal prints the
BHANUJ Platform execution ID for Studio inspection.

## Expected evidence timeline

For a successful run that has two public Claude `AssistantMessage` turns:

```text
EXECUTION_STARTED
WORKFLOW_STEP  Policy Validation Agent  STARTED
MODEL_CALL     ordinal=1
TOOL_CALL      lookup_policy  SUCCEEDED
MODEL_CALL     ordinal=2
WORKFLOW_STEP  Policy Validation Agent  COMPLETED
EXECUTION_COMPLETED  SUCCEEDED
```

The workflow-step source kind is `claude_agent_sdk.session`. Model and tool
events contain only bounded operational metadata: model/ordinal/safe numeric
usage/stop reason and tool ordinal/latency/hashed tool-use ID. They never store
a prompt, conversation, Claude text, tool input, tool output, or credentials.

## Tool exposure and safety

`tools=[]` disables Claude Agent SDK built-in tools. `allowed_tools` then
pre-approves only `mcp__policy__lookup_policy`; it does not itself filter a
tool list. The lab uses `strict_mcp_config=True`, no skills or plugins, and
`permission_mode="dontAsk"`. It does not use `bypassPermissions`.

## Fail-open behaviour

Evidence transport uses a one-second bounded request timeout. If BHANUJ
Platform is unavailable, the application reports that evidence was unavailable
but preserves Claude's tool execution, result, and exceptions. It does not
invent successful evidence or retry in a way that delays the workflow.

## Inspect in Agents Runtime

When evidence is delivered, open Agents Runtime in BHANUJ Platform and search
for the printed BHANUJ Platform execution ID (or its Claude session ID
correlation). Inspect the ordered runtime events; payload content is
intentionally absent.

## Repository structure

```text
claude-agent-tool-intg/
├── .env.example
├── .gitignore
├── LICENSE
├── README.md
├── agent.py          # SDK/MCP tool surface and prompt
├── app.py            # independently runnable Claude workflow
├── governance.py     # fail-open BHANUJ Platform adapter
├── policy.py         # deterministic local policy custom tool
├── requirements.txt
└── fixtures/
    └── policies.json
```
