# Reference OpenAI Agent Runtime

This independently runnable lab exercises a real OpenAI Responses API agent
through the public AI Governance Control Plane Agents Runtime, Replay, and
Causal Audit contracts. Core never imports this package or the OpenAI SDK.

```text
OpenAI Responses API → external runtime state → Core evidence ingestion
                                              → Intervention Policy
                                              → controlled Replay
                                              → external runtime /replay
                                              → Causal Audit lineage
```

The agent has one governed semantic tool: `transaction_risk.score`, with the
separate `reference-openai-fraud-risk/v1` evidence schema. OpenAI
function names cannot include dots, so the runtime uses the compliant provider
wire name `transaction_risk_score` and maps it to the governed tool identity.
The provider-issued `function_call.call_id` is retained as
`runtime_tool_call_id`; it is never invented by Core.

## Safety boundary

Core receives only:

- OpenAI function call ID, tool topology, evidence reference, schema, and
  digests;
- bounded decision score (`ALLOW=0.0`, `REVIEW=0.5`, `BLOCK=1.0`);
- policy-bound counterfactual reference/digest and Replay lineage.

Core does not receive prompts, function arguments or outputs, raw risk
evidence, model text, or reasoning. The runtime owns those values in its local
`OPENAI_AGENT_RUNTIME_STATE_DIR` so it can reconstruct a replay privately.
Core records only the model identifier and bounded inference settings (single
required function call, strict JSON schema, parallel calls disabled, and the
selected response-continuation mode) with the observed execution.

`OPENAI_AGENT_RUNTIME_RESPONSE_CONTINUATION=local` is the default. It uses two
stateless Responses requests with `store=false`: the runtime makes the tool
call, then supplies the private tool result to a fresh constrained decision
request. The provider does not retain Responses application state for the
continuation, and Core receives neither request's content.

Set `OPENAI_AGENT_RUNTIME_RESPONSE_CONTINUATION=provider_state` only when the
runtime must use native `previous_response_id` function-call continuation. It
sets `store=true`, so the provider-side Responses state is an explicit,
auditable retention choice. No response ID, prompt, tool payload, output, or
reasoning is ingested into Core. Review the OpenAI data controls for the
retention applicable to the account and project.

The static opaque replacement profile is intentionally inspectable without
revealing its content:

```text
reference: openai-agent-runtime://counterfactual/fraud-low-v1
digest:    sha256(canonical {"confidence":0.2,"risk_level":"LOW"})
```

At replay time the runtime resolves that reference locally, hashes the resolved
payload, and rejects the request before calling OpenAI if it differs from the
Core envelope digest.

## Canonical evidence digests

The runtime uses `replay-intervention-envelope/v1` and emits evidence digests
as `sha256:` followed by the SHA-256 of canonical JSON bytes. Canonical JSON is
UTF-8, compact (`","` and `":"` separators), and has lexically sorted object
keys. Arrays retain order; `null` remains `null`; JSON numbers retain their
Python JSON representation (`1` and `1.0` are distinct); NaN and infinities
are rejected. This is the same deterministic sorted-key/compact JSON approach
used by Core's structured-evidence resolver; Core does not receive the payload.

## Setup

Copy `.env.example` to `.env`, then provide an API key and an available model:

```sh
export OPENAI_API_KEY=...
export OPENAI_MODEL=...
export OPENAI_AGENT_RUNTIME_REPLAY_AUTH_TOKEN=local-openai-replay-secret
export OPENAI_AGENT_RUNTIME_STATE_DIR="$PWD/runtime-state"
export OPENAI_AGENT_RUNTIME_PUBLIC_REPLAY_ENDPOINT=http://127.0.0.1:8091/replay
```

Install the independently runnable lab and its development dependencies:

```sh
uv sync --extra dev
```

Run a real observed execution without Core:

```sh
uv run --extra dev openai-runtime run --scenario aligned
```

The `aligned` fixture uses `HIGH / 0.90`, so the constrained model decision is
`BLOCK`. The governed replacement is `LOW / 0.20`, so the replayed model
decision is `ALLOW`. The `ignored` fixture still calls the tool but has a
separate locked-account fact that keeps both outcomes at `BLOCK`.

## Enable controlled Replay in Core

Start the private runtime endpoint in one terminal:

```sh
uv run --extra dev openai-runtime serve --host 127.0.0.1 --port 8091
```

This runtime consumes only the published, versioned Plugin API contract
`bhanuj-governance-plugin-api>=1.0,<2`; it does not depend on the Core package
or a Core checkout. Install this runtime, including its canonical
`bhanuj.governance.plugins` entry point, into the same Python environment used
by the Core replay worker:

```sh
uv sync --extra dev
uv pip install .
```

Before the Plugin API wheel is published, this lab's developer-only
`tool.uv.sources` override points to the Plugin API package. It never points to
Core and is not part of the published wheel metadata.

## Release verification

Release candidates must build without developer source overrides and install
from wheels into a fresh environment. Build the Plugin API wheel from the same
release candidate first, then build this runtime:

```sh
# Plugin API package directory
uv build --no-sources --wheel --out-dir dist

# This runtime directory
uv build --no-sources --wheel --out-dir dist

python -m venv /tmp/bhanuj-plugin-test
source /tmp/bhanuj-plugin-test/bin/activate
pip install /path/to/bhanuj_governance_plugin_api-*.whl
pip install dist/openai_agent_runtime-*.whl
pip check
```

This proves that `[tool.uv.sources]` is developer convenience only. The
published runtime wheel depends solely on the bounded
`bhanuj-governance-plugin-api>=1.0,<2` package contract, not on a Core checkout.

The worker must have the matching fixed endpoint and service token. The
observed runtime freezes this endpoint; the adapter compares it exactly before
making a network request.

```sh
export AI_GOVERNANCE_OPENAI_AGENT_RUNTIME_REPLAY_ENDPOINT=http://127.0.0.1:8091/replay
export AI_GOVERNANCE_OPENAI_AGENT_RUNTIME_REPLAY_AUTH_TOKEN=local-openai-replay-secret
uv run -m ai_governance.workers.replay_worker_runtime
```

For a Docker worker, install this package in the worker image and use an
address reachable from that container (usually `host.docker.internal`, not
`127.0.0.1`). Do not add an OpenAI dependency to Core itself.

Set the Core API scope values too:

```sh
export AI_GOVERNANCE_BASE_URL=http://127.0.0.1:8000
export AI_GOVERNANCE_ORGANIZATION_ID=org_default
export AI_GOVERNANCE_PROJECT_ID=project_default
```

If Core protects its API, pass an already-issued, short-lived bearer token:

```sh
export AI_GOVERNANCE_API_BEARER_TOKEN=<access-token>
```

The reference runtime forwards that token only as an HTTP `Authorization`
header to Core. It does not acquire tokens, retain client credentials, or
include either in replay metadata or the governed envelope.

Then invoke the full public path. The first command creates and activates the
opaque-reference `REPLACE` policy; retain the returned policy ID/version for
the second scenario because Core allows only one active policy per tool/schema.

```sh
uv run --extra dev openai-runtime causal-audit --scenario aligned
uv run --extra dev openai-runtime causal-audit \
  --scenario ignored --policy-id <policy-id> --policy-version <version>
```

Expected Causal Audit classifications are `EVIDENCE_ALIGNED` and
`EVIDENCE_IGNORED`, respectively. The CLI prints IDs, bounded outcomes, and
durable Causal Audit lineage only.

## Tests

Credential-free runtime checks, including unknown reference, digest mismatch,
malformed model decision, and authenticated envelope validation:

```sh
uv run --extra dev pytest -q
```

To run the adapter's Plugin API envelope contract checks directly:

```sh
uv run --extra dev pytest tests/test_core_replay_adapter.py -q
```

The real OpenAI test is deliberately opt-in:

```sh
RUN_OPENAI_INTEGRATION=1 uv run --extra dev pytest -m openai tests/test_openai_integration.py -q
```

After Core, its plugin-enabled worker, and this runtime endpoint are all
running, the complete public Core test is also opt-in:

```sh
RUN_OPENAI_CAUSAL_AUDIT=1 uv run --extra dev pytest -m integration tests/test_causal_audit_integration.py -q
```

The implementation follows the [Responses API](https://platform.openai.com/docs/api-reference/responses)
function-call and structured-output contracts. It iterates the response output
items to find the provider function call and sends `function_call_output` back
using its actual `call_id`.
