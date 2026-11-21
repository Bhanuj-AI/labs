# LangGraph execution-assurance lab v2

This is a new, focused companion lab. It does **not** change or reuse the
existing `langgraph-execution-assurance-lab` tutorial.

It shows the supported `langgraph/v2` evidence-replay boundary:

```text
Frozen plan + private recorded evidence
                 ↓
LangGraph ToolNode call
                 ↓
Resolver matches, checks, and consumes exactly one planned call
                 ↓
Recorded evidence or the one approved replacement is returned
```

The original tool implementation is never supplied to the replay `ToolNode`.
There is no original-tool fallback, network call, or production side effect in
this lab.

## What you will verify

The tests use the public `v0.3.0` adapter tag and a real LangGraph `ToolNode`.
They show that:

1. recorded private evidence is returned without invoking the original tool;
2. an approved intervention returns only its replacement evidence;
3. unknown tool names and mismatched arguments fail closed;
4. independent async calls resolve in one session;
5. concurrent duplicate calls consume a planned call at most once; and
6. an unfinished session is marked `INCOMPLETE` at finalization.

## Install and run

```sh
cd labs/agent-frameworks/langgraph/langgraph-execution-assurance-lab-v2
python -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade -r requirements.txt

python -c "from importlib.metadata import version; assert version('aigp-langgraph-replay-adapter') == '0.3.0'"
python -c "from importlib.metadata import version; assert version('bhanuj-governance-plugin-api') == '1.2.0rc1'"

PYTHONPATH=. python -m pytest -q
python run_lab.py
```

The sample output has two successful paths: first the recorded evidence, then
the policy-approved replacement. All evidence content in this lab is synthetic.

## Read the example in plain language

`replay_examples.py` creates a frozen list of expected calls. Each expected call
has a tool name, canonical argument digest, occurrence number, evidence
reference, and evidence digest. The private evidence text stays in the runtime
store.

When LangGraph asks `ToolNode` to run `account_lookup`, the v2 session creates
its own resolver-backed tool with that name. It does not receive the historical
`account_lookup` Python function. The resolver atomically checks the request,
checks any declared dependencies, consumes the one matching planned call, and
selects the corresponding private evidence. The evidence store verifies the
digest before returning the content to `ToolNode`.

An intervention is not a new tool call. It is a frozen binding for one planned
call. If that call is reached, its approved replacement evidence is returned;
otherwise the session remains incomplete.

## The boundary is deliberately small

`langgraph/v2` covers calls that pass through the declared LangGraph
`ToolNode`. It does **not** intercept or make claims about:

- arbitrary custom graph nodes;
- direct provider or SDK calls;
- provider-hosted tools; or
- other agent frameworks.

Those surfaces are declared unsupported in the capability. They must not be
treated as safe just because a nearby `ToolNode` call was replayed.

## Session completion and OSS worker acceptance

The session summary is necessary evidence, but it is not by itself a successful
Causal Audit replay attestation. A runtime integration that submits an
execution-replay result to AIGP OSS must return the typed execution trace and
attestation. The OSS replay worker compares those against the frozen manifest,
resolution plan, intervention binding, tool-boundary coverage, and evidence-only
safety declarations before accepting the result.

This focused lab tests the public ToolNode surface without requiring a running
control plane or private runtime data. The control-plane acceptance tests live
with the worker in `ai-governance-oss/tests/unit/test_replay_execution.py`.
The separation is intentional: this lab proves local ToolNode interception;
the worker independently decides whether returned evidence is sufficient for a
governed replay result.

## Live Docker tutorial

The `live/` companion runs the complete path against the local Docker stack:

1. it records one synthetic `account_lookup` ToolNode call in AIGP OSS;
2. it saves the raw tool arguments and two synthetic evidence values only in
   `live/runtime-data/langgraph-v2-snapshots.db`;
3. it creates and activates a reference-only replacement policy;
4. it queues a Causal Audit; and
5. the opt-in worker resolves the planned call through a real ToolNode without
   loading or calling an original tool implementation.

First rebuild the platform with the Core validation change, then build only the
replay worker with the external adapter and its private snapshot mount:

```sh
cd ai-governance-oss
docker compose build ai-governance-platform
docker compose -f docker-compose.yml -f docker-compose.langgraph-v2.yml \
  build ai-governance-replay-worker
docker compose -f docker-compose.yml -f docker-compose.langgraph-v2.yml \
  up -d --force-recreate ai-governance-platform ai-governance-replay-worker
```

Set the normal local tenant, actor, and short-lived service-token variables.
`AIGP_RUNTIME_TOKEN` may be omitted when the same service identity is allowed
to record execution evidence and administer the intervention policy. Tokens do
not belong in a shell history, a capture file, or this repository.

```sh
export AIGP_BASE_URL=http://localhost:8000
export AIGP_ORGANIZATION_ID=org_default
export AIGP_PROJECT_ID=project_default
export AIGP_RUNTIME_ACTOR_ID=<runtime-service-actor>
export AIGP_ISSUER_ACTOR_ID=<policy-admin-actor>
export AIGP_ISSUER_TOKEN=<short-lived-token>
# Optional when the runtime uses a distinct service identity:
# export AIGP_RUNTIME_TOKEN=<short-lived-token>

cd ../labs/agent-frameworks/langgraph/langgraph-execution-assurance-lab-v2
python -m venv live/.venv
. live/.venv/bin/activate
python -m pip install -r requirements.txt
python live/run_live_lab.py full
```

The final JSON names the Studio execution, Causal Audit, and Replay views for
screen capture. It deliberately contains IDs, opaque references, and digests
only. The SQLite snapshot is ignored by Git and mounted read-only into the
worker alone; neither the API nor Studio can read it.

The live tutorial is still a declared `langgraph.tool-node` boundary. It proves
evidence resolution for ToolNode calls and worker-side evidence validation. It
does not intercept custom graph nodes, direct SDK calls, provider-hosted tools,
or arbitrary workflow code, and it is not sandbox-level isolation.
