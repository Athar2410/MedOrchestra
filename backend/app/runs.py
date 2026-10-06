"""Runs a case through the graph in the background and buffers its events.

The POST that creates a run starts execution immediately; any number of SSE
subscribers can then replay the buffered events from an index (the SSE
`Last-Event-ID`) and tail new ones, so browser reconnects never re-run a case.
Finished runs are saved to Supabase (services/run_store.py); `load()` brings an evicted or
past run back so its stream can be replayed.
"""

import asyncio
import logging
import time
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from langgraph.graph.state import CompiledStateGraph
from pydantic_core import to_jsonable_python

from app.config import Settings
from app.schemas import CaseInput, ClinicalReport
from app.services import run_store

logger = logging.getLogger(__name__)

KEEPALIVE_SECONDS = 15.0


@dataclass
class Run:
    id: str
    case: CaseInput
    events: list[dict[str, Any]] = field(default_factory=list)
    report: ClinicalReport | None = None
    error: str | None = None
    done: bool = False
    changed: asyncio.Condition = field(default_factory=asyncio.Condition)

    @property
    def status(self) -> str:
        if not self.done:
            return "running"
        return "failed" if self.error else "completed"


class RunManager:
    def __init__(self, graph: CompiledStateGraph, settings: Settings) -> None:
        self._graph = graph
        self._settings = settings
        self._runs: OrderedDict[str, Run] = OrderedDict()
        self._tasks: set[asyncio.Task] = set()

    def get(self, run_id: str) -> Run | None:
        return self._runs.get(run_id)

    async def load(self, run_id: str) -> Run | None:
        """In-memory run, else a finished run from the database."""
        if run := self._runs.get(run_id):
            return run
        try:
            row = await asyncio.to_thread(run_store.load_run, run_id)
        except Exception:
            logger.exception("Loading run %s failed", run_id)
            return None
        if row is None:
            return None
        return Run(
            id=row["id"],
            case=CaseInput.model_validate(row["case_input"]),
            events=row["events"],
            report=ClinicalReport.model_validate(row["report"]) if row["report"] else None,
            error=row["error"],
            done=True,
        )

    def start(self, case: CaseInput) -> Run:
        run = Run(id=uuid.uuid4().hex, case=case)
        self._runs[run.id] = run
        while len(self._runs) > self._settings.max_runs_in_memory:
            self._runs.popitem(last=False)
        task = asyncio.create_task(self._execute(run))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return run

    async def _publish(self, run: Run, event: dict[str, Any]) -> None:
        async with run.changed:
            run.events.append(to_jsonable_python(event))
            run.changed.notify_all()

    async def _execute(self, run: Run) -> None:
        started = time.perf_counter()
        await self._publish(run, {"type": "run_started", "run_id": run.id})
        try:
            async with asyncio.timeout(self._settings.run_timeout_seconds):
                async for mode, chunk in self._graph.astream(
                    {"case": run.case, "reroute_count": 0},
                    stream_mode=["custom", "updates"],
                ):
                    if mode == "custom":
                        await self._publish(run, chunk)
                    elif "report" in chunk:
                        run.report = chunk["report"]["report"]
                        await self._publish(run, {"type": "report", "report": run.report})
        except TimeoutError:
            run.error = f"Run exceeded {self._settings.run_timeout_seconds:.0f}s timeout"
        except Exception as exc:
            logger.exception("Run %s failed", run.id)
            run.error = str(exc) or type(exc).__name__
        if run.error:
            await self._publish(run, {"type": "error", "message": run.error})
        status = "failed" if run.error else "completed"
        done = {"type": "done", "status": status}
        # Save before announcing `done`: the UI refreshes its run list on `done`, so a
        # later save raced it and the new run was missing from "Recent cases".
        await self._persist(run, status, [*run.events, done], started)
        # Set `done` and append the final event atomically so no subscriber can see
        # the run as finished without also seeing its `done` event.
        async with run.changed:
            run.done = True
            run.events.append(done)
            run.changed.notify_all()

    async def _persist(
        self, run: Run, status: str, events: list[dict[str, Any]], started: float
    ) -> None:
        # Best-effort: a database outage must not affect the live result.
        row = {
            "id": run.id,
            "status": status,
            "case_input": to_jsonable_python(run.case),
            "report": to_jsonable_python(run.report),
            "events": events,
            "error": run.error,
            "duration_ms": round((time.perf_counter() - started) * 1000),
        }
        try:
            await asyncio.to_thread(run_store.save_run, row)
        except Exception:
            logger.exception("Saving run %s failed", run.id)

    async def subscribe(self, run: Run, start: int = 0) -> AsyncIterator[tuple[int, dict] | None]:
        """Yield (index, event) from `start` until the run ends; None means send a keepalive."""
        index = start
        while True:
            async with run.changed:
                if index >= len(run.events) and not run.done:
                    try:
                        await asyncio.wait_for(run.changed.wait(), KEEPALIVE_SECONDS)
                    except TimeoutError:
                        pass
                pending = run.events[index:]
                finished = run.done
            if not pending and not finished:
                yield None
            for event in pending:
                yield index, event
                index += 1
            if finished and index >= len(run.events):
                return
