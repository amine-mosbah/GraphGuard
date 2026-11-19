"""Background job execution — running the pipeline off the request path.

Role
----
Defines the Celery application and the task that executes a pipeline run.

Why a job queue at all
----------------------
A full run of the default configuration measures **7.1 s** end to end. That is far too long to hold
an HTTP request open: the browser would show a spinner for seven seconds, a reverse-proxy read
timeout would eventually cut it, and a worker process would be blocked on numerical computation
instead of serving other analysts. Dispatching to a worker makes the API's job "accept the request
and return an identifier", which stays in the tens of milliseconds no matter how long the analysis
takes.

The honest scope note, which belongs in the report: at *this* data volume an in-process background
task would also work. The queue is chosen for the deployment profile — several analysts, runs that
grow with telemetry volume, and a worker that can be scaled or restarted independently of the API
— not because 7 s is unmanageable today.

Eager mode
----------
When no broker is configured the Celery app runs tasks eagerly, in-process. That keeps the test
suite and a fresh checkout working with no Redis, exercising exactly the same task function that
the worker would run.

Inputs / outputs
----------------
Input: a persisted ``runs`` row identifier.
Output: that row updated to ``succeeded`` or ``failed``, with its alerts and chains written.
"""

from __future__ import annotations

import logging
from typing import Any

from celery import Celery

from graphguard.platform.db import session_scope
from graphguard.platform.models import Run, RunStatus
from graphguard.platform.runs import execute_run
from graphguard.platform.settings import get_settings

logger = logging.getLogger(__name__)


def build_celery_app(*, eager: bool | None = None) -> Celery:
    """Construct the Celery application from deployment settings."""
    settings = get_settings()
    app = Celery("graphguard", broker=settings.redis_url, backend=settings.redis_url)
    app.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        # A pipeline run is not idempotent from the queue's point of view — it writes
        # rows — so a task must not be redelivered to another worker just because the
        # first was slow. Acknowledge late enough to survive a crash, but only retry
        # explicitly.
        task_acks_late=True,
        task_reject_on_worker_lost=False,
        worker_prefetch_multiplier=1,
        task_always_eager=bool(eager) if eager is not None else settings.environment == "test",
        task_eager_propagates=True,
    )
    return app


celery_app = build_celery_app()


@celery_app.task(name="graphguard.execute_run", bind=True)
def execute_run_task(self: Any, run_id: str) -> dict[str, Any]:
    """Execute a queued run and persist its results.

    Opens its own session: a Celery task runs in a different process from the request that queued
    it, so no session, transaction or ORM object can be shared across that boundary — only the
    identifier travels.
    """
    logger.info("executing run %s", run_id)
    with session_scope() as session:
        run = session.get(Run, run_id)
        if run is None:
            raise ValueError(f"no such run: {run_id}")
        if run.status is not RunStatus.QUEUED:
            # Guard against a redelivered message re-running work that already
            # completed, which would duplicate alerts and reset triage state.
            logger.warning("run %s is %s, not queued — skipping", run_id, run.status.value)
            return {"run_id": run_id, "status": run.status.value, "skipped": True}
        execute_run(session, run)
        return {"run_id": run_id, "status": run.status.value, "skipped": False}


def dispatch(run_id: str) -> str | None:
    """Queue ``run_id`` for execution; returns the Celery task id, or ``None`` when eager."""
    result = execute_run_task.delay(run_id)
    return None if celery_app.conf.task_always_eager else str(result.id)
