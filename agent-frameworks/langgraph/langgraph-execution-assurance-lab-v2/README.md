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

The tests use the public `v0.2.0` adapter tag and a real LangGraph `ToolNode`.
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

python -c "from importlib.metadata import version; assert version('aigp-langgraph-replay-adapter') == '0.2.0'"
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
