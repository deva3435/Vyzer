"""Apply Alembic migrations without destroying an existing local Vyzer DB.

Legacy development databases were created with SQLAlchemy create_all and may
not have an alembic_version table. If the current schema tables already exist,
we stamp the initial migration rather than attempting to recreate them.
"""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from app.config import settings
from app.database import engine


def main() -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    config = Config("alembic.ini")
    if "alembic_version" not in tables and {"users", "conversations", "messages"}.issubset(tables):
        command.stamp(config, "0001_initial")
    command.upgrade(config, "head")


if __name__ == "__main__":
    main()
