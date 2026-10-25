# End-to-end gap report

## Exercised through real OSS APIs

- Agent execution registration, issuer-scoped delegation, action authorization,
  delegation revocation, execution receipts, execution detail, and manifest
  rejection through Causal Audit eligibility.
- The banking mutation is a real SQLite transaction with a durable mutation ID.
  Recovery reads that business evidence and does not re-invoke it.
- A successful `authorized` execution obtains its OSS-issued replay-manifest
  hash and stores a private, tenant-bound snapshot through the independently
  owned LangGraph adapter. The snapshot contains no copy of the banking
  database and AIGP OSS never opens it.
- The worker extension consumes `RuntimeReplaySource/v1`, resolves the private
  snapshot, verifies tenant, adapter, manifest, integrity, and isolation
  bindings, runs a LangGraph graph over an in-memory copy, and returns bounded
  replay evidence to OSS.
- The canonical OSS integration test
  `tests/integration/test_langgraph_replay_adapter.py` drives receipt →
  manifest → replay worker → adapter → causal audit and asserts the original
  banking SQLite database remains `PENDING`.

## Required deployment inputs

The lab intentionally does not create policy, seed credentials, or start an
OSS worker. Supply an active authorization policy, an active opaque-reference
intervention policy, and the external adapter in the process which runs the
replay worker. These are control-plane and runtime deployment responsibilities,
not lab substitutes.

The private snapshot database must be the same runtime-owned database for the
successful `authorized` run and the replay worker. If it is missing, tampered
with, belongs to another tenant, names a different adapter, or has a different
manifest hash, the adapter rejects replay rather than executing a business
operation.

## Deliberately not simulated

1. **Adapter availability:** OSS eligibility does not claim that a current
   worker deployment has an adapter. A queued audit can fail operationally when
   `langgraph/v1` is not installed; that is correct fail-closed behaviour.
2. **Independent proof of external mutation:** receipts and adapter output are
   authenticated runtime attestations. AIGP does not impersonate SQLite or
   claim independent proof that a historical business mutation occurred.
3. **Automatic retry after uncertainty:** the recovery path reads the mutation
   log and supersedes `UNKNOWN`; it never automatically retries an operation
   with uncertain external side effects.
4. **Live Studio availability:** Studio inspection is documented where the
   deployment exposes Agents Runtime and Causal Audit pages. The lab does not
   require Studio or introduce Enterprise dependencies.

No product functionality was added to hide these limits. The adapter remains
provider-neutral at the Plugin API boundary and runtime-specific only inside
the external LangGraph distribution.
