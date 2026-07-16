"""Pipeline-credential check for the /v1/* surface.

One identity may reach these endpoints: the customer's orchestration pipeline.
The agent's task_token is a different credential in a different format and
must never authenticate here — if it could, the agent could ingest artifacts
with attributes of its choosing and the product is void. Compared in constant
time; missing, malformed, or non-matching credentials are indistinguishable.
"""

from __future__ import annotations

import hmac
import os

from fastapi import Header, HTTPException

_ENV_VAR = "WRIT_PIPELINE_CREDENTIAL"


def pipeline_credential_from_env() -> str:
    credential = os.environ.get(_ENV_VAR, "")
    if not credential:
        raise RuntimeError(f"{_ENV_VAR} is not set; the /v1/* surface cannot start without it")
    return credential


def make_pipeline_auth(pipeline_credential: str):
    """Returns a FastAPI dependency bound to one credential value."""
    if not pipeline_credential:
        raise ValueError("pipeline_credential must be non-empty")

    def require_pipeline(authorization: str = Header(default="")) -> None:
        scheme, _, presented = authorization.partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(
            presented.strip(), pipeline_credential
        ):
            raise HTTPException(status_code=401, detail="pipeline credential required")

    return require_pipeline
