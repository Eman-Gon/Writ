# Week 1 reality-surface report

**Status:** offline integration complete; live credibility gate blocked on
external inputs and credentials.

## What is verified

- The Gmail connector sends the complete RFC 822 byte stream as one multipart
  `file` with an `IngestRequest` `meta` part. It never imports or invokes the
  local demo ingestor and never decomposes attachments.
- The connector and Fable's landed FastAPI `/v1/ingest` and `/v1/tasks`
  implementations pass an in-process contract test using the models in
  `wire.py`.
- Fable's landed stdio MCP server advertises exactly the four names in
  `MCP_TOOLS`. A contract test lists and fetches one ingested document, then
  confirms that the task manifest contains exactly the fetched envelope before
  a mutation is proposed.
- The stock-Claude launcher uses a replacement system prompt with no Writ
  coaching, disables built-in tools, uses strict MCP configuration, and fails
  its report if Claude's initialization event exposes anything other than the
  four Writ tools.
- The agent subprocess receives the task token but not the pipeline credential
  or Gmail access token.

## Live gate

The live run has not been claimed. This checkout currently has no Gmail access
token, pipeline endpoint or credential, authorized real invoice, or
authenticated Claude CLI session. Consequently there is no honest result yet
for whether the foreign agent cites evidence correctly without coaching and no
real-invoice `parse_paths_agree=False` data.

`fixtures/real/` remains intentionally empty rather than being populated with
generated or public "sample invoice" PDFs whose provenance cannot be shown.

## Contract and surface findings

1. Fable's current MCP server is stdio-only. It works for the local week-one
   harness when API and MCP processes share an absolute `STATEGUARD_DB`. It
   cannot pair with a remotely hosted API/database; a deployed remote transport
   remains an unresolved product surface.
2. `wire.py` has no pipeline-authenticated manifest inspection response. The
   harness compares fetch results with Fable's shared store for a local stdio
   run, but an external orchestrator cannot prove the server-side manifest
   through a frozen API.
3. `AuthenticationAssurance` has no email-transport value. Gmail DMARC pass (or
   both DKIM and SPF pass) is conservatively represented as `password`; all
   other inbound mail is `none`. It never becomes `mfa` or
   `phishing_resistant`. Fable should decide whether the wire schema needs a
   transport-specific assurance type before production reporting depends on
   this field.
4. The landed Fable HTTP surface uses `Authorization: Bearer` for the pipeline
   credential and matches the connector default. The connector keeps header
   and scheme configurable until that detail is documented in `wire.py`.

## Required live run

1. Supply an authorized Gmail mailbox token and a real invoice.
2. Run `python -m connectors.gmail --once` and retain its `document_id`.
3. Start Fable's API and stdio MCP server against the same database.
4. Authenticate the stock Claude CLI and run `python -m harness.foreign_agent`.
5. Preserve the generated report and record every real invoice whose ingestion
   returns `parse_paths_agree=False`.
