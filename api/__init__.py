"""Writ HTTP surface — /v1/ingest and /v1/tasks. pipeline_credential only.

The agent's identity (task_token) has no route into this app. The MCP surface
(mcp/) is a separate process with a separate credential; the two meet only at
the shared store.
"""

from __future__ import annotations

from fastapi import FastAPI

from gateway.store import Store

from api.auth import make_pipeline_auth, pipeline_credential_from_env
from api.ingest import make_ingest_router
from api.tasks import make_tasks_router


def create_app(store: Store, pipeline_credential: str) -> FastAPI:
    app = FastAPI(title="Writ pipeline API", version="0.1.0")
    require_pipeline = make_pipeline_auth(pipeline_credential)
    app.include_router(make_ingest_router(store, require_pipeline))
    app.include_router(make_tasks_router(store, require_pipeline))
    return app


def create_app_from_env() -> FastAPI:
    """Composition root for `uvicorn --factory api:create_app_from_env`.
    Reads WRIT_PIPELINE_CREDENTIAL and STATEGUARD_DB."""
    return create_app(Store(), pipeline_credential_from_env())
