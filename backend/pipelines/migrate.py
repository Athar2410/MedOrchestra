"""Apply backend/migrations/*.sql to DATABASE_URL in filename order.

Migrations are written to be idempotent, so re-running is safe.

    .\\.venv\\Scripts\\python -m pipelines.migrate
"""

import sys
from pathlib import Path

import psycopg

from app.config import get_settings

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def main() -> int:
    url = get_settings().database_url
    if not url:
        print("DATABASE_URL is not set (backend/.env)")
        return 1
    with psycopg.connect(url, autocommit=True) as conn:
        for path in sorted(MIGRATIONS.glob("*.sql")):
            conn.execute(path.read_text(encoding="utf-8"))
            print(f"applied {path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
