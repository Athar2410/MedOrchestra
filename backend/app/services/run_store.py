"""Persist finished runs in Supabase (`runs` table, migrations/003_runs.sql).

No-ops when DATABASE_URL is unset (tests, offline dev). Synchronous psycopg — call via
`asyncio.to_thread` (psycopg async does not work on the Windows Proactor loop).
ponytail: a fresh connection per call (~0.2 s, once per case); pool it if traffic grows.
"""

import logging
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from app.config import get_settings

logger = logging.getLogger(__name__)


def _url() -> str:
    return get_settings().database_url


def save_run(row: dict[str, Any]) -> None:
    if not _url():
        return
    with psycopg.connect(_url(), connect_timeout=10) as conn:
        conn.execute(
            """insert into runs (id, status, case_input, report, events, error, duration_ms)
               values (%(id)s, %(status)s, %(case_input)s, %(report)s, %(events)s,
                       %(error)s, %(duration_ms)s)
               on conflict (id) do nothing""",
            {**row, **{k: Jsonb(row[k]) for k in ("case_input", "report", "events")}},
        )


def load_run(run_id: str) -> dict[str, Any] | None:
    if not _url():
        return None
    with psycopg.connect(_url(), connect_timeout=10) as conn:
        row = conn.execute(
            "select id, status, case_input, report, events, error from runs where id = %s",
            (run_id,),
        ).fetchone()
    if row is None:
        return None
    keys = ("id", "status", "case_input", "report", "events", "error")
    return dict(zip(keys, row, strict=True))


def list_runs(limit: int = 20) -> list[dict[str, Any]]:
    if not _url():
        return []
    with psycopg.connect(_url(), connect_timeout=10) as conn:
        rows = conn.execute(
            """select id, created_at, status, duration_ms,
                      case_input->>'chief_complaint',
                      report->>'urgency',
                      report->'diagnoses'->0->>'condition'
               from runs order by created_at desc limit %s""",
            (limit,),
        ).fetchall()
    keys = ("run_id", "created_at", "status", "duration_ms", "chief_complaint", "urgency",
            "top_diagnosis")  # fmt: skip
    return [dict(zip(keys, r, strict=True)) for r in rows]
