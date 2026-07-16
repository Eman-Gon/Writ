"""POST /v1/tasks — task-token minting. pipeline_credential only.

Task boundaries are declared by the customer's orchestrator, never by the
agent. There is deliberately no agent-facing begin-task primitive: an agent
that can begin a task can shed taint by starting a fresh one. The orchestrator
mints the task with an explicit document allowlist — one task ≈ one invoice —
and passes the token into the agent's MCP client config for that run.

Only a sha256 of the token is stored; possession of the database does not
yield usable task tokens.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends

from gateway.store import Store
from wire import TaskRequest, TaskResponse

TOKEN_PREFIX = "wt_"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def make_tasks_router(store: Store, require_pipeline) -> APIRouter:
    router = APIRouter(dependencies=[Depends(require_pipeline)])

    @router.post("/v1/tasks", response_model=TaskResponse)
    def create_task(request: TaskRequest) -> TaskResponse:
        task_id = f"task_{uuid.uuid4().hex[:20]}"
        token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=request.ttl_seconds)
        store.create_task(
            task_id,
            request.task_ref,
            hash_token(token),
            expires_at.isoformat(),
            request.document_ids,
        )
        return TaskResponse(task_id=task_id, task_token=token, expires_at=expires_at)

    return router
