# External connectors

`gmail.py` polls Gmail's real REST API and forwards each complete RFC 822
message to Fable's `POST /v1/ingest`. It never decomposes attachments and never
imports the demo's local ingestor.

Use a Gmail OAuth access token with the read-only scope. Token refresh belongs
to the deployment's credential provider; the connector accepts the current
token through `GMAIL_ACCESS_TOKEN` and never persists it.

API references: [list messages](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/list)
and [get a raw message](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/get).

```bash
export GMAIL_ACCESS_TOKEN=...
export WRIT_API_BASE_URL=https://writ.example
export WRIT_PIPELINE_CREDENTIAL=...

python -m connectors.gmail --once
```

Each successful ingestion prints one JSON object containing the Gmail message
ID and Writ `document_id`. Continuous polling is the default; `--once` is useful
for deployment checks. Processed Gmail IDs are stored in
`.writ-gmail-state.json` only after Writ accepts the raw message.

The pipeline authorization header defaults to `Authorization: Bearer ...`.
Until Fable freezes that HTTP detail, deployments can set
`WRIT_PIPELINE_HEADER` and `WRIT_PIPELINE_SCHEME` without changing code.

The connector logs `parse_paths_agree=False` at error level as a finding.
Gmail-authenticated DMARC (or both DKIM and SPF) maps conservatively to the
schema's lowest non-none value, `password`; no email result maps to `mfa` or
`phishing_resistant`.
