# gateway — StateGuard trusted core

The seam, for the periphery workstream (connector / agent / mock ERP / demo):

```python
from gateway import evaluate, value_hash, broker
```

## evaluate

```python
response = evaluate(proposal, manifest, envelopes, claim=None, mode="external")
```

- `envelopes`: every `SourceEnvelope` for the run — a `dict[str, SourceEnvelope]`
  or an iterable. Must include parents referenced by `parent_envelope_id`;
  a manifest id (or ancestor) missing from `envelopes` fails closed to
  `UNVERIFIED`.
- `mode="external"` (default) returns an opaque `ExternalResponse`.
  `mode="diagnostic"` returns a `DiagnosticResponse` with the full
  `DecisionRecord` — harness/demo only.
- `manifest.run_id` must equal `proposal.run_id` or evaluation fails closed.
- Every call appends a `DecisionRecord` (SQLite at `$STATEGUARD_DB`, default
  `./stateguard.db`).

## broker

```python
gateway.broker.configure({"vendor_master": erp_executor, "ap_ledger": ledger_executor})
effect = gateway.broker.commit(record, mutation, manifest, envelopes, claim=claim)
```

- `configure(...)` is called once from demo.py (the composition root). An
  executor is `Callable[[ProposedMutation, DecisionRecord], str]` returning the
  concrete `downstream_effect` string. Executors own the destination
  credentials; nothing else may.
- The caller supplies `manifest` and `envelopes` (and `claim`, keyword-only, if
  one applies): `commit` re-runs `evaluate()` against them immediately before
  executing (TOCTOU guard). A revalidation that does not return ALLOW refuses.
- Duplicate `proposal_id` is a no-op returning the prior effect (at-most-once).
- Refusals raise `gateway.broker.BrokerRefused`; no business effect occurred.

## What the connector must set for the checks to bind

- **`raw_text` is the output of `normalize.normalize_text()`**, applied once at
  ingestion; spans index into it. The gateway never re-normalizes raw_text.
- **`scope.subject_scope` on the vendor_change_form envelope** must name the
  vendor, e.g. `["vendor_123"]`. `AttestedClaim.subject` is checked against the
  union of `subject_scope` over the *verified cited* envelopes — that is how a
  claim for vendor_123 becomes useless against a mutation about vendor_456.
  Empty scope ⇒ no claim can bind ⇒ CRITICAL mutations deny.
- **`scope.field_scope`** should list the canonical fields the source may
  assert (e.g. the form: `["vendor.remittance.bank_account"]`). At HIGH+
  sensitivity with no valid claim, a field outside every cited envelope's
  field_scope trips the `field_authority` control.
- **`AttestedClaim.value_hash`** must be computed with
  `gateway.value_hash(canonical_field, value)` (sha256 over the
  field-normalized value), and `AttestedClaim.canonical_field` must be the
  registry-canonical name (e.g. `vendor.remittance.bank_account`).
