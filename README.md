# Agent Change Control

Agent Change Control is a source-aware authorization gateway for changes proposed by autonomous agents. It checks whether the source of a value is authorized to modify a specific destination field before the change reaches an ERP, CRM, payment tool, or vendor database.

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
