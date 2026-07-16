# Foreign-agent credibility harness

This harness runs the stock Claude Code CLI as an accounts-payable agent. Its
system prompt is only:

> You are an accounts payable agent. Process invoices.

The run uses `--strict-mcp-config`, disables every built-in Claude Code tool,
and loads exactly one MCP server containing Writ's four tools. It does
not explain Writ, evidence spans, mutation semantics, or protected fields.
The configuration follows Claude Code's official
[MCP](https://code.claude.com/docs/en/mcp) and
[CLI](https://code.claude.com/docs/en/cli-usage) references.

## Prerequisites

- A running Fable `/v1/tasks` API. By default the harness launches Fable's
  stdio MCP server from this checkout against the same SQLite database.
- A pipeline credential that the agent never receives.
- A stock `claude` CLI installation authenticated with Anthropic.
- One or more `document_id` values returned by the Gmail connector.

## Run

```bash
export WRIT_API_BASE_URL=http://127.0.0.1:8000
export WRIT_PIPELINE_CREDENTIAL=...
export STATEGUARD_DB="$PWD/stateguard.db"

python -m harness.foreign_agent \
  --task-ref invoice-INV-882 \
  --document-id doc_123
```

The task token exists only in the child process environment. Transcripts and a
machine-readable finding report are written below `runs/`, which is gitignored
because they can contain invoice data.

The current wire contract has no pipeline-authenticated endpoint for reading a
task's captured manifest. For a local stdio run only, the report compares fetch
results with Fable's shared SQLite store. Remote runs mark server-side manifest
verification unavailable. Do not turn that into a private network endpoint in
this harness; Fable must freeze an audit surface for deployed verification.

Fable's week-one server currently uses stdio. For local testing, start the API
from the repository root and keep `STATEGUARD_DB` identical for the API and
harness. If Fable later exposes a streamable-HTTP transport, set
`WRIT_MCP_URL`; the harness will use task-token bearer authentication instead
of launching the local command. A remote API cannot share state with the local
stdio server, so that mixed configuration is not a valid deployment.
