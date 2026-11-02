"""Inngest middleware: ids for every function call, and one line per step (PRD O1, O4, O8).

Inngest calls the app once per step. Before the call this binds the Inngest run id, the
function and the ids in the event (run, application, posting, listing); after it, one line
says which step ran, how long it took and whether it failed. Step lines that belong to a run
or an application are kept in log_event for the application timeline.
"""

import logging
import time
from typing import Any

import inngest

from observability import context
from observability.logs import event

log = logging.getLogger("jap.inngest")

EVENT_IDS = ("run_id", "application_id", "posting_id", "listing_id")


class LogMiddleware(inngest.Middleware):
    def __init__(self, client: inngest.Inngest, raw_request: object) -> None:
        super().__init__(client, raw_request)
        self._started = time.perf_counter()
        self._function = ""

    async def transform_input(self, ctx: Any, function: Any, steps: Any) -> None:
        data = dict(getattr(ctx.event, "data", None) or {})
        self._function = str(getattr(function, "id", "") or "")
        context.bind(
            inngest_run_id=getattr(ctx, "run_id", None),
            function=self._function,
            **{k: data.get(k) for k in EVENT_IDS},
        )
        self._started = time.perf_counter()

    async def transform_output(self, result: Any) -> None:
        ms = int((time.perf_counter() - self._started) * 1000)
        error = getattr(result, "error", None)
        step = getattr(result, "step", None)
        ids = context.current()
        keep = "application_id" in ids or "run_id" in ids
        if step is None:
            name, what = "inngest.function", f"{self._function} finished"
        else:
            op = str(getattr(step.op, "value", step.op))
            name, what = "inngest.step", f"{self._function} step {step.id} ({op})"
        if error is not None:
            event(
                log,
                f"{name}.failed",
                f"{what} failed in {ms} ms: {type(error).__name__}: {error}",
                level=logging.WARNING,
                keep=True,
                step=getattr(step, "id", None),
                duration_ms=ms,
                error_type=type(error).__name__,
            )
            return
        event(
            log,
            name,
            f"{what} in {ms} ms",
            keep=keep,
            step=getattr(step, "id", None),
            duration_ms=ms,
        )
