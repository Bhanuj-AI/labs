# Claude API basics with optional AIGP evidence

This lab contains the first two exercises in the Claude + AIGP learning path:

1. `first_application.py` sends one Claude Messages API request.
2. `first_tool.py` gives Claude one application-owned `lookup_policy` tool.

They use the official Anthropic Python SDK directly.  The tool example does
not use the Claude Agent SDK: the application receives Claude's tool request,
runs the local deterministic function, and supplies the result in the next
Messages API call.

## Boundary

Claude selects a tool; this application executes it.  AIGP is optional and
receives a separate bounded execution record only.  It never receives the
prompt, Claude response, tool arguments, tool result, credential, or raw SDK
object.  If the AIGP API is unavailable, these two non-protected examples keep
their normal Claude behavior and report evidence delivery as unavailable.

```text
Claude Messages API -> application -> local lookup_policy -> Claude Messages API
                                  \
                                   -> optional bounded AIGP evidence
```

## Prerequisites

- Python 3.12+
- Anthropic API key and access to the model you choose
- `ANTHROPIC_API_KEY` and `CLAUDE_MODEL` set in the shell
- Optional: an AIGP OSS API with tenant scope and authentication configured

The checked dependency set is pinned in `requirements.txt`:
`anthropic==2.3.0` and `pytest==8.3.5`.

## Setup and local checks

```bash
cd labs/agent-frameworks/anthropic/claude-api-basics-intg
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
pytest -q
export ANTHROPIC_API_KEY=...
export CLAUDE_MODEL=...
```

Keep credentials in your shell or an ignored `.env`; `.env.example` contains
only variable names.

## Guide 1: first application

```bash
python first_application.py
```

Expected output shape (the response wording is model-generated):

```text
Claude response: <one-sentence response>
Claude execution ID: <uuid>
AIGP evidence: disabled
```

## Guide 2: first tool

```bash
python first_tool.py
```

The successful run asks Claude to call `lookup_policy` for `POL-1001`.  The
fixture is deterministic, but Claude's final wording is not.

```text
Policy: POL-1001
Claude response: <answer based on active policy POL-1001>
Claude execution ID: <uuid>
AIGP evidence: disabled
```

Run the deliberate failure experiment with a missing record:

```bash
python first_tool.py POL-404
```

The application returns a `POLICY_NOT_FOUND` tool result to Claude.  The run
is still a completed interaction if Claude handles that result; it is not
evidence that a successful policy lookup occurred.

## Add optional AIGP runtime evidence

Start and authenticate the OSS API using the deployment's documented method,
then configure only the existing public Agent Executions scope:

```bash
export AI_GOVERNANCE_BASE_URL=http://localhost:8000
export AI_GOVERNANCE_ORGANIZATION_ID=org_default
export AI_GOVERNANCE_PROJECT_ID=project_default
# Set only for a token-authenticated deployment:
# export AI_GOVERNANCE_ACCESS_TOKEN=...
python first_tool.py
```

When delivery succeeds, the terminal also prints the platform-owned AIGP
execution ID.  Retrieve it through:

```text
GET /api/v1/agent-executions/{execution_id}
```

The first-tool timeline can contain two `MODEL_CALL` events and one
`TOOL_CALL`; the tool event reports a hash of the Claude tool-use identifier,
status, reason code, and latency.  It does not retain the native tool input or
output.

## Validation status

`pytest -q` and Python compilation are credential-free checks.  A real Claude
call requires an Anthropic credential, and end-to-end evidence delivery
requires a running, authenticated OSS API.  Do not claim either live result
until those values are available and the returned AIGP timeline has been
inspected.
