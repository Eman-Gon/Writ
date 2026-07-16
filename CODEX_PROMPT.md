# Codex — StateGuard periphery (v2, supersedes v1)

## Read this first

The broker signature mismatch is **a spec error on my side, not a build error on yours.** v1 said `commit(decision_record, mutation)` while also requiring commit-time revalidation — which re-runs `evaluate()` internally and therefore needs the manifest and envelopes. The two-arg signature was impossible. Your call site was built to a broken spec.

Also worth saying plainly: when the core was missing, you reported the blocker instead of stubbing a local gateway. That was the right call and it's the behavior this spec depends on.

## Current state

- **Your half is done.** Connector with shared normalization and independent child attribution, naive AP agent with context manifests, mock ERP with the routing-alias hazard, five fixtures, `demo.py`, `fixtures/envelopes.py`. 7 tests passing. Forwarded-email child lineage preserved.
- **The core has landed.** `demo.py` prints Act 1, then fails at the broker call site.
- **One change is needed from you:** the call site.

## Frozen contract — do not renegotiate

```python
# you import these; you never reimplement them
from gateway.evaluate import evaluate
from gateway import broker

response = evaluate(proposal, manifest, envelopes, claim=claim, mode="diagnostic")
effect   = broker.commit(decision_record, mutation, manifest, envelopes, claim=claim)
```

- `claim` is keyword-only in both.
- Package root resolves as `gateway`. Claude Code is conforming to exactly this.
- Update `demo.py` to match. Nothing else in your half changes.

## You own

```
connector/ingest.py    artifact -> SourceEnvelope(s)
agent/ap_agent.py      reads invoice, proposes typed mutations
mock/erp.py            vendor master + AP ledger + payment simulator
fixtures/              the documents + envelopes.py
demo.py                the CLI walkthrough
```

Contract files — import, never edit: `schemas.py`, `normalize.py`.
You do not create `pyproject.toml`. Claude Code owns dependencies.

## Invariants to hold (already built — do not regress them)

- **Content inspection wins; declared type never does.** Disagreement → `ArtifactType.AMBIGUOUS`.
- **Nested artifacts inherit nothing.** `parent_envelope_id` is lineage only; every attribute assigned independently. If you ever copy attributes parent → child, that is the attack.
- **`normalize_text()` once, at ingestion.** Store as `raw_text`. Spans index into that string. Never re-normalize later — offsets shift.
- **Forwarding is not attesting.** An admin forwarding an invoice yields `attestation_type=NONE`. `USER_ATTESTED` only when a human submits structured field values through the admin portal.
- **The agent stays naive.** Do not harden it, do not sanitize its output, do not make it defensive. It is the untrusted component. If it doesn't try to change the bank account, the demo has no villain.
- **The agent has no path to the ERP.** Only `evaluate()`, then on allow, `broker.commit()`. If your agent code can reach the payment endpoint directly, the security model is broken and the demo is a lie.
- **No LLM in the connector.** Deterministic parsing only.
- **`mock/erp.py` parses `remittance.note` for routing hints.** That hazard is the point — it makes the aliasing test real rather than theater.

## demo.py — five acts, CLI, no UI

```
ACT 1  clean invoice     -> ALLOW    "invoice posted, $4,200"
ACT 2  poisoned invoice  -> DENY     print BOTH grounds + lineage chain
ACT 3  forwarded email   -> DENY     print support_quality=DERIVED
                                     "admin forwarded it; forwarding is not attesting"
ACT 4  attested form     -> ALLOW    broker commits, bank account updated
ACT 5  note aliasing     -> DENY     "remittance.note is payment_routing"
```

For each act print: source artifact → what the agent proposed → decision → **why** → lineage chain.

Run in `mode="diagnostic"` so the reasons are visible. Note in a comment that production defaults to `external` (opaque) and diagnostic output exists only for the harness.

**No exception swallowing.** If something fails, it fails loudly with a traceback. A demo that dies silently in front of an engineer is worse than one that crashes.

## Acceptance

`python demo.py` runs all five acts, no manual intervention.

**Act 3 is the one that matters.** It must print a lineage chain showing the invoice as least-trusted ancestor **even though the agent cited only the admin's email**. The span check should *pass* — the IBAN really is in that email. The manifest is what catches it.

If Act 3 passes because the span failed rather than because the manifest caught the ancestor, it's green for the wrong reason. Assert `support_quality == DERIVED`, not just the decision.

**Act 1 is the one to check first.** It exercises the whole path. If it denies with `support_quality=UNVERIFIED`, that's span misalignment between your `raw_text` and the gateway's comparison — not a policy bug. Print `repr(raw_text[start:end])` next to `repr(proposed_value)` before field normalization and report what you see rather than working around it.

Report which acts pass and which fail, with `support_quality` and `catch_type` for each.
