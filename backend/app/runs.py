"""Runs a case through the graph in the background and buffers its events.

The POST that creates a run starts execution immediately; any number of SSE
subscribers can then replay the buffered events from an index (the SSE
`Last-Event-ID`) and tail new ones, so browser reconnects never re-run a case.
Runs live in memory only; Phase 5 persists finished runs to Supabase.
"""

import asyncio
import logging
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from langgraph.graph.state import CompiledStateGraph
from pydantic_core import to_jsonable_python

from app.config import Settings
from app.schemas import CaseInput, ClinicalReport

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
        # Set `done` and append the final event atomically so no subscriber can see
        # the run as finished without also seeing its `done` event.
        async with run.changed:
            run.done = True
            run.events.append({"type": "done", "status": run.status})
            run.changed.notify_all()

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
