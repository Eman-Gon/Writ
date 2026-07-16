# Claude Code — StateGuard core (v2, supersedes v1)

## Read this first

v1 contradicted itself: it specified `commit(decision_record, mutation)` while also requiring commit-time revalidation, which needs the manifest and envelopes. **You resolved that correctly.** This spec ratifies your resolution and freezes the contract. Where this disagrees with v1, this wins.

## Current state

- **Codex's half is done and tested.** Connector, naive AP agent, mock ERP with the routing-alias hazard, five fixtures, `demo.py`. 7 tests passing.
- **Your half has landed.** `demo.py` reaches it — Act 1 prints, then fails at the broker call site on the signature.
- **`fixtures/envelopes.py` exists.** Codex exported real connector-generated envelopes. Use these, not hand-written ones — hand-built envelopes diverge from what the connector actually emits and hide span misalignment until it's expensive to find.

## Frozen contract — do not renegotiate

```python
# gateway/evaluate.py
def evaluate(proposal, manifest, envelopes, claim=None, mode="external"):
    """-> ExternalResponse | DiagnosticResponse"""

# gateway/broker.py
def commit(decision_record, mutation, manifest, envelopes, claim=None) -> str:
    """-> downstream_effect string"""
```

- Package root resolves as `gateway`. Not `stateguard.gateway`, not `src.gateway`. Codex imports `gateway.evaluate` and `gateway.broker`.
- `claim` is keyword-only in both.
- Codex is updating its call site to exactly this. If your implementation differs in parameter names or order, **change yours to match** — don't report a variance and wait.

## You own

```
gateway/registry.py   resolve field_path -> RegistryEntry
gateway/policy.py     the authority matrix
gateway/evaluate.py   the gate + shadow evaluation
gateway/broker.py     sole credential holder; idempotent commit
gateway/store.py      append-only decision records
pyproject.toml        dependencies (Codex does not create one)
```

Contract files — import, never edit: `schemas.py`, `normalize.py`.

## registry.py

`resolve(destination_system, field_path) -> RegistryEntry | None`

Match canonical field or alias. **`None` → DENY**, not "unknown, proceed." Registry completeness is a security property; a gap is a vulnerability.

| canonical_field | effect | sensitivity | equivalence_class |
|---|---|---|---|
| `ap.invoice.amount` | record_transaction | low | `txn` |
| `ap.invoice.due_date` | record_transaction | low | `txn` |
| `vendor.profile.address` | update_vendor_profile | medium | `profile` |
| `vendor.remittance.bank_account` | payment_routing | critical | `routing` |
| `vendor.remittance.note` | **payment_routing** | **critical** | `routing` |
| `memory.descriptive` | persist_memory_descriptive | medium | `mem_desc` |
| `memory.authoritative` | persist_memory_authoritative | critical | `mem_auth` |

`vendor.remittance.note` is free text and CRITICAL because the mock ERP parses it for routing. Effect, not name.

## policy.py

`decide(entry, support_quality, least_trusted_envelope, claim) -> (Decision, reason)`

Two axes: source attributes × business effect. Not a trust scalar. In order:

1. `entry is None` → DENY
2. `artifact_type == AMBIGUOUS` or `parse_paths_agree is False`, and sensitivity ≥ HIGH → DENY
3. sensitivity == CRITICAL:
   - requires a valid `AttestedClaim` matching `subject`, `canonical_field`, `value_hash == sha256(normalize_field(value, kind))`, `authentication == PHISHING_RESISTANT`, unexpired, unrevoked
   - no claim → DENY
   - `support_quality in (DERIVED, UNVERIFIED)` → DENY **regardless of claim**
4. sensitivity == HIGH: requires `STRUCTURED_ATTESTED` or `DIRECT_VERIFIED`; else QUARANTINE
5. sensitivity == MEDIUM: `UNVERIFIED` → QUARANTINE; else ALLOW
6. sensitivity == LOW: `DIRECT_VERIFIED` → ALLOW; else QUARANTINE

YAML-loaded with these as defaults. **No OPA/Rego** — stretch, not today.

## evaluate.py

1. Resolve destination via registry.
2. **Verify evidence binding:**
   ```python
   from normalize import normalize_field, field_kind_for
   kind = field_kind_for(entry.canonical_field)   # UnknownFieldKind -> DENY, not crash
   span_text = envelope.raw_text[ref.span_start:ref.span_end]
   verified = normalize_field(proposal.proposed_value, kind) == normalize_field(span_text, kind)
   ```
   Confirm `artifact_hash` unchanged first. **Never** apply `normalize_text()` to `raw_text` — the connector did that at ingestion and spans index into the result.
3. **Compute least-trusted ancestor from `manifest.envelope_ids`, NOT from `proposal.evidence`.** This is the whole design. Walk `parent_envelope_id` chains. A derived value inherits the least-trusted thing in context.
4. Classify `support_quality`:
   - `DIRECT_VERIFIED` — span verified AND the citing envelope is the only ancestor in the manifest
   - `STRUCTURED_ATTESTED` — a valid `AttestedClaim` covers it
   - `DERIVED` — span verified but less-trusted envelopes are also in the manifest
   - `UNVERIFIED` — span failed, envelope missing, or hash mismatch
5. `policy.decide(...)`
6. **Shadow evaluation — never short-circuit.** Evaluate every control even after one denies. Record `controls_evaluated: list[ControlResult]`; set `catch_type` = `single_control` (exactly one would deny), `defense_in_depth` (2+), or `allowed`. Controls: `evidence_binding`, `lineage`, `field_authority`, `attestation_present`, `subject_scope`, `claim_validity`.
7. Write `DecisionRecord`. Return per `mode`; **default `external`** (opaque).

## broker.py

- **Sole holder of destination credentials.** Nothing else in the repo imports ERP or payment config.
- Refuses anything without `decision == ALLOW` and a persisted `DecisionRecord`.
- **Commit-time revalidation:** re-run `evaluate(proposal, manifest, envelopes, claim)` immediately before executing. This is why the signature takes manifest and envelopes.
- Idempotency keyed on `proposal_id`; a duplicate is a no-op returning the prior effect. At-most-once. Do not claim exactly-once.
- Endpoint allowlist; unknown endpoint → refuse.
- Records `executed` and `downstream_effect` via a follow-up event — records are append-only, never updated in place.

## Rules

- **No LLM anywhere in this workstream.** If you reach for one, the design is wrong.
- No `trusted: bool` on anything. Trust is computed per decision, never stored on the thing being judged.
- No function `(SourceEnvelope) -> AttestedClaim`. Ever. That's the laundering attack.
- Fail closed on every ambiguity at HIGH+.
- No speculative abstraction. Single-use code stays concrete.

## Acceptance

**Run Act 1 alone, first.** It's the seam test — it exercises the whole path and must ALLOW.

If Act 1 denies with `support_quality=UNVERIFIED`, that's span misalignment, not policy:
```python
print(repr(envelope.raw_text[ref.span_start:ref.span_end]))
print(repr(proposal.proposed_value))
```
before any field normalization. If those look right, the mismatch is inside `normalize_field`.

Then all five:

| Act | Fixture | Expect |
|---|---|---|
| 1 | clean invoice | `ALLOW` — utility must not regress |
| 2 | poisoned invoice | `DENY`, `catch_type == "defense_in_depth"` |
| 3 | forwarded email | `DENY`, **`support_quality == DERIVED`** |
| 4 | attested form | `ALLOW`, `executed`, `downstream_effect == "bank_account_updated"` |
| 5 | note aliasing | `DENY` — registry says note is payment_routing |

Plus: unregistered field → DENY. Duplicate proposal → no duplicate effect.

**Act 3 is the one that matters.** The span *should pass* — the IBAN really is in the admin's email. It must deny because the manifest caught the invoice ancestor. Assert on `support_quality == DERIVED` and `least_trusted_ancestor == <invoice envelope id>`, not just the decision. If it denies because the span failed, it's green for the wrong reason and the architecture is broken.
