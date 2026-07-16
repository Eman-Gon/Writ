# Writ — MCP Server & End-to-End Spec

**Date:** July 16, 2026 · Sprint Jul 17 – Aug 7
**Supersedes:** nothing. Extends the demo into a deployable product.

---

## 0. What changes when you go end-to-end

In the demo you own everything: the agent, the connector, the ERP, the invoices. In production **you own one thing — the gateway.** The customer owns the agent.

That inversion breaks an assumption the demo was hiding. Your provenance model requires being in the ingestion path: source attributes are assigned by *your* code, before any model sees the artifact. But a real customer already has an AP agent that already reads their inbox. By the time it calls a tool, the invoice text is in its context — no envelope, no hash, no attestation type, no manifest. **Provenance was destroyed before you were invoked, and no middleware installed later can reconstruct it.**

So the deployment model is the product decision:

| Model | Provenance | Integration ask | Verdict |
|---|---|---|---|
| Writ owns ingestion (mailbox routes to us) | Strongest | Largest — we become document intake | **Design for this** |
| Customer instruments ingestion with our SDK | Good, depends on their discipline | Moderate | **Deploy as this** |
| Writ proxies the destination only | **None** — provenance-blind | Trivial | Not the product. This is Arcade's question, not ours. |

Same shape as the gateway/adapter split: the strong version defines correctness, the light version gets you installed.

---

## 1. The central insight — MCP *is* the lineage capture

In the demo, the adapter captured the context manifest because you wrote the agent. In production you don't. This looks like a problem and is actually the opposite:

> **If the only way to obtain document content is through Writ's MCP tool, then every fetch is a manifest entry.**

The agent cannot read a document without telling you it read it. You get context lineage with **zero framework hooks** — no AgentScope patching, no middleware, no forking. It works with any MCP-capable agent, which is the framework-agnostic claim made real rather than aspirational.

**The constraint this creates, stated honestly:** if the agent has another path to the documents (the customer's own inbox tool), the manifest is incomplete, and the forwarded-email laundering attack succeeds — agent reads the invoice out-of-band, fetches the admin's email through Writ, cites the email, span passes, manifest shows only the email → `DIRECT_VERIFIED` → allowed. **Manifest completeness is load-bearing.**

**Why this is tractable:** the customer is not the adversary. The invoice sender is. A customer configuring "this agent's document sources are Writ only" is a legitimate configuration assertion, like any security control. Customer configuration is trusted; document content is not. That's the threat model, and it's honest.

**The failure mode is bounded even so:** a proposal with no evidence refs classifies `UNVERIFIED` → denied for CRITICAL. So out-of-band reading gives an attacker nothing for high-impact fields unless they can also route a laundering artifact through Writ. Fails closed, degrades gracefully.

---

## 2. Two APIs, two identities

This is the credential boundary applied to the integration surface itself.

```
┌──────────────────────────────────────────────────────────────┐
│ CUSTOMER'S PIPELINE          identity: pipeline_credential    │
│  POST /v1/ingest             -> creates envelopes             │
│  POST /v1/tasks              -> mints task token              │
│  The agent has NO credential for either.                      │
└───────────────────────────┬──────────────────────────────────┘
                            │ task_token
                            ▼
┌──────────────────────────────────────────────────────────────┐
│ CUSTOMER'S AGENT             identity: task_token (scoped)     │
│  MCP: writ_list_documents / writ_fetch_document /             │
│       writ_propose_mutation / writ_check_status               │
│  Cannot ingest. Cannot mint tasks. Cannot read policy.        │
│  Cannot approve. Holds no destination credentials.            │
└───────────────────────────┬──────────────────────────────────┘
                            ▼
┌──────────────────────────────────────────────────────────────┐
│ WRIT GATEWAY  (evaluate -> broker -> destination)             │
└──────────────────────────────────────────────────────────────┘
                            ▲
┌───────────────────────────┴──────────────────────────────────┐
│ HUMAN SURFACES               identity: authenticated user      │
│  Approval UI (two-person, digest-bound)                        │
│  Attested vendor-change portal -> emits AttestedClaim          │
│  Audit + quarantine review                                     │
│  NOT reachable via MCP. Ever.                                  │
└──────────────────────────────────────────────────────────────┘
```

**If the agent can call `/v1/ingest`, the entire product is void** — it could assign itself `attestation_type: SYSTEM_SIGNED`. This separation is not deployment hygiene; it is the security model.

---

## 3. Task scoping — and the over-tainting trap

**The problem.** If the manifest is per-MCP-connection, a long-running agent accumulates every document it ever fetched, and every proposal inherits the least-trusted one. That is *exactly* FIDES's documented limitation #2 reappearing in your own system — the thing you exist to beat.

**The trap.** The obvious fix is `writ_begin_task()` as an agent tool. **Do not do this.** An agent that can begin a task can shed taint by starting a fresh one. That's laundering, self-served.

**The resolution.** Task boundaries are declared by the customer's orchestrator, not the agent:

```
POST /v1/tasks                         (pipeline_credential)
  { "task_ref": "invoice-INV-882", "document_ids": [...] }
  -> { "task_token": "...", "expires_at": "..." }
```

The orchestrator passes `task_token` into the agent's MCP client config for that run. The manifest is scoped to the task. The agent cannot mint one.

One task ≈ one invoice. That keeps manifests small and taint precise — which is the whole granularity advantage.

---

## 4. MCP tool surface

Four tools. Nothing else. Every addition is an attack surface.

### `writ_list_documents(queue: str) -> [DocumentRef]`

Returns `{document_id, artifact_type, received_at, sender_display}`. **No content.** Listing is not fetching and does not enter the manifest.

### `writ_fetch_document(document_id: str) -> {envelope_id, text, artifact_type}`

**This call is the manifest entry.** Records `(task_id, envelope_id)` server-side, then returns `envelope.raw_text` — the normalized text, so agent-visible offsets equal span offsets. Fetching a child fetches only that child; nesting does not auto-fetch parents, but the parent chain is recorded for lineage.

Returns `artifact_type` so a well-behaved agent can self-limit. **Never** returns trust, authority, or attestation. The agent has no business reasoning about those, and telling it invites it to try.

### `writ_propose_mutation(destination, field_path, value, evidence[]) -> {decision, proposal_id, message, effect?}`

**Evaluate and commit are one call.** The agent holds no credentials, so a separate `commit` primitive would be a TOCTOU window with no upside. Internally: `evaluate()` → if ALLOW, `broker.commit()` → return.

`evidence[]` is `[{envelope_id, span_start, span_end, extraction_path}]` — the agent's *nomination*, verified server-side against the hash-pinned envelope.

Returns per `ExternalResponse`: opaque on denial. No rule id, no control name, no lineage, no support quality. **Diagnostic mode is never reachable over MCP.**

On `REQUIRE_APPROVAL`: returns `{decision: "require_approval", proposal_id}`. The agent's job ends there.

### `writ_check_status(proposal_id) -> {decision, effect?}`

For polling a pending approval. Opaque on denial.

### Deliberately absent

`writ_ingest` · `writ_begin_task` · `writ_approve` · `writ_read_policy` · `writ_check_mutation` (dry-run)

The dry-run is worth naming: it would be convenient for agent planning and it is an oracle. A compromised agent could probe the policy boundary for free. Omit it; revisit only with a rate limit and a threat-model argument.

---

## 5. Ingestion API

```
POST /v1/ingest                        (pipeline_credential)
  multipart: raw_bytes
  json: { declared_type, ingestion_channel, origin_principal,
          authentication_assurance, received_at }
  -> { document_id, envelope_ids[], artifact_type, parse_paths_agree }
```

Returns `envelope_ids[]` plural — one artifact yields many envelopes when nested (email + attachment + archive members). **Children inherit nothing.**

Two deployment modes, same code path:
- **Writ-owned:** we poll Gmail/Graph and call ingest ourselves.
- **Customer-pushed:** their pipeline calls ingest before dispatching the agent.

`parse_paths_agree: false` in the response is a signal to the customer that this artifact will fail closed on high-impact fields. Surface it — don't let it be a silent denial later.

---

## 6. Human surfaces (never MCP)

**Approval UI.** Shows the artifact, the proposed mutation, **the full provenance chain**, and old → new value. Two-person for CRITICAL; initiator cannot approve. Bound to the mutation digest; any change invalidates. Expires.

*The approver seeing provenance is the control.* This is precisely what ask-first mode fails to show. A JSON fixture is not a substitute — build the real page.

**Attested change portal.** Structured form → emits `AttestedClaim`. Phishing-resistant auth. Not an upgrade of any artifact — a new independent assertion. There is no code path from `SourceEnvelope` to `AttestedClaim`.

**Audit + quarantine review.** Read-only. Source → proposal → decision → effect.

---

## 7. Delegation

Same principle as the demo: **split by trust boundary.** Fable owns what must be correct; Sol owns what must be real.

### Claude Code (Fable) — the authorization surface

Already owns `gateway/`. Natural extension: everything where a mistake is a vulnerability.

```
mcp/server.py          the four tools, task-token auth, external mode only
mcp/session.py         task-scoped manifest accumulation
api/ingest.py          envelope creation, nested decomposition, hash pinning
api/tasks.py           task token minting (pipeline credential only)
api/approval.py        digest binding, two-person, replay protection, expiry
gateway/*              existing core, extended
```

Why Fable: the manifest is the security model; task tokens are the taint boundary; external-mode-only over MCP is a leak that would be invisible in testing and fatal in production. These need the workstream that already refuses to work around a contradiction.

**Rules:** no LLM anywhere in this workstream. Agent identity can never reach ingest or tasks. `DiagnosticResponse` must be structurally unreachable from `mcp/` — not a flag that defaults off; **unreachable**.

### Codex (Sol) — the reality surface

Already owns `connector/`, `agent/`, `mock/`. Natural extension: everything that proves it works outside your own head.

```
connectors/gmail.py    real inbox -> POST /v1/ingest
adapters/xero.py       real ERP sandbox (destination behind the broker)
harness/foreign_agent/ an agent you did NOT write, MCP client, any framework
ui/approval/           React page against api/approval
fixtures/real/         actual invoices, actual formats
```

Why Sol: it built the multi-path parsing and the fixtures, and it reported a blocker rather than stubbing a gateway. The foreign-agent harness is the credibility test and needs a workstream that will report "this doesn't work with a real agent" instead of quietly adjusting the agent.

**Rules:** the foreign agent must be one you didn't write — a stock MCP client with Qwen or Claude, no Writ-specific coaching in its prompt. If it only works with your agent, you have a demo. The approval UI calls Fable's API; it never evaluates. Real invoices only — no synthetic PDFs in `fixtures/real/`.

### The seam

`mcp/server.py` (Fable) ← `harness/foreign_agent` (Sol)
`api/ingest.py` (Fable) ← `connectors/gmail.py` (Sol)
`api/approval.py` (Fable) ← `ui/approval` (Sol)
`gateway/broker.py` (Fable) → `adapters/xero.py` (Sol)

**Freeze the wire contracts before either starts.** The broker-signature incident cost you an evening on a two-arg spec that contradicted itself three bullets later. HTTP + MCP schemas are worse — the failure is a 422 at midnight, not a `TypeError`. Write the request/response shapes into a contract file, run them once, and let neither side change them unilaterally.

---

## 8. Sequencing — three weeks, two people

**Week 1 — the surface exists**
Fable: MCP server + four tools + task tokens + ingest API (envelope creation, nested decomposition).
Sol: Gmail connector → ingest; foreign-agent harness with a stock MCP client.
**Gate:** an agent you didn't write fetches a real invoice through Writ and proposes a mutation. Manifest correct.

**Week 2 — the effects are real**
Fable: approval API (digest, two-person, replay, expiry); broker → real destination.
Sol: Xero sandbox adapter; approval UI; real invoice fixtures.
**Gate:** Act 3 reproduced end-to-end — foreign agent, real inbox, real ERP. Forwarded poisoned invoice denied with `support_quality=DERIVED`. Attested change approved by two humans and committed to Xero.

**Week 3 — the claim is measured**
Both: coarse-taint baseline + the ablation across configurations; stratified families; Track E utility adversary.
**Gate:** the two-axis table — security *and* benign utility — across no-enforcement / coarse-taint / no-ancestry / full Writ.

**Cut before you cut anything else:** RecallGuard's memory store, AgentScope adapter, the eval fleet at scale, self-hosted inference. All of them are worth less than a foreign agent working against a real ERP.

---

## 9. Honest risks

- **Manifest completeness is a configuration assumption**, not an enforced invariant. If the customer's agent has another document path, laundering works. Fails closed for CRITICAL without evidence — but *not* if the attacker can route a laundering artifact through Writ. State this in the threat model; do not let it be discovered.
- **Xero/QuickBooks will have fields you didn't anticipate**, which is precisely why real beats mock — effect-equivalence classes are enumerated by hand, and a real API is where you find out what you missed.
- **The foreign agent may simply fail to use the tools correctly.** That is a finding, not a bug to paper over. If a stock agent can't cite evidence properly, the tool surface is wrong and you need to know in week 1.
- **Three weeks is tight for four real integrations.** If something slips, slip the ERP to a mock and keep the foreign agent — the agent is what proves the product; the ERP proves the plumbing.
