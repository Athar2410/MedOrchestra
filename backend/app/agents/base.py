"""Wraps agent functions as LangGraph nodes that emit streaming events.

Every agent is `async def agent(state, ctx) -> dict` returning a partial state
update. The wrapper emits `agent_started` / `agent_completed` / `agent_failed`
around it; the agent itself can emit `agent_thinking` via `ctx.think()` or any
other event via `ctx.emit()`. Events go to LangGraph's custom stream, which
`app.runs` relays to the client as SSE.
"""

import functools
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from langgraph.config import get_stream_writer
from pydantic_core import to_jsonable_python

from app.graph.state import ClinicalState
from app.schemas import AgentName

# Agents that can run more than once per case because of a Critique re-route.
RETRYABLE_AGENTS: set[AgentName] = {"diagnostician", "critique"}

# State keys that are bookkeeping, not agent output worth showing in the UI.
_INTERNAL_KEYS = {"agent_logs", "reroute_count", "case"}


@dataclass
class AgentContext:
    agent: AgentName
    attempt: int
    _write: Callable[[dict[str, Any]], None]

    def emit(self, type: str, **data: Any) -> None:
        self._write({"type": type, "agent": self.agent, "attempt": self.attempt, **data})

    def think(self, message: str) -> None:
        self.emit("agent_thinking", message=message)


def _stream_writer() -> Callable[[dict[str, Any]], None]:
    # Outside a graph run (agents called directly in tests or evaluation) events are dropped.
    try:
        return get_stream_writer()
    except RuntimeError:
        return lambda _event: None


AgentFn = Callable[[ClinicalState, AgentContext], Awaitable[dict[str, Any]]]


def agent_node(name: AgentName) -> Callable[[AgentFn], Callable[[ClinicalState], Awaitable[dict]]]:
    def decorator(fn: AgentFn) -> Callable[[ClinicalState], Awaitable[dict]]:
        @functools.wraps(fn)
        async def node(state: ClinicalState) -> dict[str, Any]:
            attempt = state.get("reroute_count", 0) + 1 if name in RETRYABLE_AGENTS else 1
            ctx = AgentContext(name, attempt, _stream_writer())
            ctx.emit("agent_started")
            started = time.perf_counter()
            try:
                update = await fn(state, ctx)
            except Exception as exc:
                ctx.emit("agent_failed", error=str(exc))
                raise
            duration_ms = round((time.perf_counter() - started) * 1000)
            output = {k: v for k, v in update.items() if k not in _INTERNAL_KEYS}
            ctx.emit(
                "agent_completed",
                duration_ms=duration_ms,
                output=to_jsonable_python(output),
            )
            log = f"{name} (attempt {attempt}) completed in {duration_ms} ms"
            return {**update, "agent_logs": [log]}

        return node

    return decorator
