# Observe Claude Agent SDK subagents with BHANUJ Platform

This lab runs one real Claude Agent SDK session with a main agent and one
specialized research subagent. The subagent makes one local `lookup_policy`
call through an in-process MCP server; the main agent uses its result to answer
the user. There is no database, web access, filesystem tool, or additional
agent.

**Claude Agent SDK owns execution. BHANUJ Platform records provider-neutral
runtime evidence independently.** BHANUJ OSS exposes the Agent Executions API
used when the optional evidence connection is configured.

```text
Main Agent
  -> Research Subagent
      -> lookup_policy (local in-process MCP tool)
  -> Main Agent final answer
                    |
                    v
           BHANUJ Platform runtime evidence
```

Claude Agent SDK references: [subagents](https://code.claude.com/docs/en/agent-sdk/subagents),
[Python SDK](https://code.claude.com/docs/en/agent-sdk/python), and
[hooks](https://code.claude.com/docs/en/agent-sdk/hooks).

## What this demonstrates

- A programmatic `AgentDefinition` named `research-policy-subagent`.
- A main agent that uses the public `Agent` tool to delegate exactly once.
- A subagent restricted to the single custom MCP tool
  `mcp__policy__lookup_policy`.
- Public `SubagentStart`, `SubagentStop`, `PreToolUse`, and `PostToolUse` hooks
  mapped to bounded BHANUJ Platform evidence.

The top-level built-in tool surface is restricted to `Agent`; the subagent has
only `lookup_policy`. Neither agent can create further subagents.

## Prerequisites

- Python 3.11+
- An Anthropic API key and access to your selected Claude model
- `ANTHROPIC_API_KEY` and `CLAUDE_MODEL` in the environment
- Optionally, a running BHANUJ OSS instance for Agents Runtime evidence

## Setup

```bash
cd labs/agent-frameworks/anthropic/claude-agent-subagent-intg
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...
export CLAUDE_MODEL=claude-sonnet-4-6
```

`.env.example` lists the required and optional environment values. Do not
commit credentials.

## Run without BHANUJ Platform

Leave all `AI_GOVERNANCE_*` variables unset:

```bash
python app.py
```

The terminal prints Claude's final answer and a public Claude session ID.
The response wording is model-generated; the local policy fixture is
deterministic.

```text
Policy: POL-1001
Claude session ID: c3af9461-81a9-4567-9449-2e1f09f1c35b
Final answer: Here's what the Research Subagent found for **policy POL-1001**:

| Field | Details |
|---|---|
| **Policy ID** | POL-1001 |
| **Status** | ✅ ACTIVE |
| **Coverage Limit** | $5,000 |

**✅ The claim can proceed.** Policy POL-1001 is currently **active**, so there are no policy-status blockers. Just ensure the claim amount falls within the **$5,000 coverage limit** before finalizing.
BHANUJ Platform evidence: disabled
```

## Enable BHANUJ Platform evidence

Start BHANUJ OSS using the authentication and tenancy settings appropriate to
your deployment, then configure its existing public Agents Runtime scope:

```bash
export AI_GOVERNANCE_BASE_URL=http://localhost:8000
export AI_GOVERNANCE_ORGANIZATION_ID=org_default
export AI_GOVERNANCE_PROJECT_ID=project_default
python app.py

Policy: POL-1001
Claude session ID: 1bdeefb6-21ef-4cde-8a6f-a07b03525bcb
Final answer: Great news! Here's the verdict based on the Research Subagent's findings:

✅ **The claim can proceed.**

Here's a quick summary for policy **POL-1001**:

| Detail | Value |
|---|---|
| **Status** | Active |
| **Coverage Limit** | $5,000 |
| **Claim Eligible** | Yes |

Since the policy is **active** and explicitly flagged as eligible for claim processing, there are no blockers. The claim can move forward, up to the **$5,000 coverage limit**.
BHANUJ Platform evidence: delivered
BHANUJ Platform execution ID: e26e449c-ea13-4849-8bf8-f03209aa1ac9
```

The caller-created public Claude `session_id` is reused as the external
execution identity. When delivery succeeds, the CLI prints a BHANUJ Platform
execution ID for Studio inspection.

## Evidence mapping and privacy

For the expected run shape, Agents Runtime receives:

```text
EXECUTION_STARTED
WORKFLOW_STEP  Main Agent               STARTED
MODEL_CALL     Main Agent               ordinal=1
WORKFLOW_STEP  Research Subagent        STARTED
MODEL_CALL     Research Subagent        ordinal=2
TOOL_CALL      lookup_policy            SUCCEEDED
MODEL_CALL     Research Subagent        ordinal=3
WORKFLOW_STEP  Research Subagent        COMPLETED
MODEL_CALL     Main Agent               ordinal=4
WORKFLOW_STEP  Main Agent               COMPLETED
EXECUTION_COMPLETED  SUCCEEDED
```

`SubagentStart` and `SubagentStop` provide the subagent lifecycle. Public
`AssistantMessage.parent_tool_use_id` distinguishes a subagent model response
from a main-agent response without reading message content. The current Python
hook input does not expose an explicit main-agent step ID, so this guide does
not manufacture `parent_step_id`; Studio shows the ownership handoff through
the ordered main/subagent/main timeline.

Evidence includes only bounded operational facts: agent scope, model, ordinal,
safe numeric usage, stop reason, tool latency, and a hashed tool-use ID. It
never contains prompts, responses, reasoning, tool arguments, tool results,
credentials, or raw SDK/hook payloads.

## Fail-open behaviour

Every BHANUJ Platform REST call has a short one-second timeout and no retry
loop. If evidence delivery is unavailable, the application reports that state
but does not alter Claude's subagent choice, tool execution, final answer, or
exception behaviour. It does not fabricate successful evidence.

## Inspect in Agents Runtime

Use the printed BHANUJ Platform execution ID, or the Claude session ID as the
correlation value, to find the run in Agents Runtime. You should see the
ordered Main Agent → Research Subagent → Main Agent workflow boundaries and a
single `lookup_policy` tool event marked as executing in the research subagent.

## Repository structure

```text
claude-agent-subagent-intg/
├── .env.example
├── .gitignore
├── LICENSE
├── README.md
├── agent.py          # main agent, AgentDefinition, and tool restrictions
├── app.py            # one independently runnable Claude session
├── governance.py     # fail-open BHANUJ Platform evidence adapter
├── policy.py         # deterministic local MCP custom tool
├── requirements.txt
└── fixtures/
    └── policies.json
```
