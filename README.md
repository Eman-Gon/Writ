# Writ

Writ is a source-aware authorization gateway for changes proposed by autonomous agents. It checks whether the source of a value is authorized to modify a specific destination field before the change reaches an ERP, CRM, payment tool, or vendor database.

This repository contains the interactive procurement and accounts-payable product demo.

## Run locally

```bash
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000). The root route redirects to the control center.

## Demo scenarios

- `ACC-2046`: an invoice-sourced amount update is allowed and executed.
- `ACC-2048`: an invoice-sourced bank account change is blocked.
- `ACC-2051`: a payment-routing change is quarantined for human approval.

The demo uses deterministic in-memory data. Decisions reset when the page is refreshed; no live business system is modified.

## Repository layout

- `src/` contains the Next.js product demo.
- `gateway/` contains the Python decision engine; `connector/`, `agent/`, and
  `mock/` contain its demo integrations.
- `fixtures/` and `tests/` contain Python test data and coverage.
- `docs/` contains the Writ MCP specification and archived implementation
  handoff prompts.
- `schemas.py`, `normalize.py`, and `wire.py` are shared Python contract modules
  and remain at the root so every Python package imports the same definitions.

## Python core (StateGuard gateway)

The gateway that actually makes these decisions lives in this repo as a Python workstream: `gateway/` (registry, policy, evaluate, broker, store), its periphery in `connector/`, `agent/`, `mock/`, and `fixtures/`, and the shared contract files `schemas.py` and `normalize.py` at the root. See `gateway/README.md` for the seam contract.

```bash
python3.12 -m venv .venv          # requires Python >= 3.10 (PEP 604 unions in schemas.py)
.venv/bin/pip install -e ".[dev]"
.venv/bin/python demo.py          # five-act command-line walkthrough
.venv/bin/python -m pytest        # full suite
```

Run both from the repo root. `demo.py` writes decision records to `./stateguard.db` (gitignored, safe to delete).
