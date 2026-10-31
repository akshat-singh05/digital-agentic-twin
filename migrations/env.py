"""
Alembic environment.

Runs against a connection passed in by app/db_migrations.py
(``config.attributes["connection"]``) or, from the CLI, the app's engine.
"""

from alembic import context
from sqlalchemy import text

import app.models  # noqa: F401  — register every table on Base.metadata
from app.database import Base, engine

target_metadata = Base.metadata


def _run(connection):
    # SQLite batch migrations rebuild tables (create copy → drop old).
    # With foreign keys on, dropping a parent table would cascade-delete
    # its children, so enforcement is paused for the migration.
    is_sqlite = connection.dialect.name == "sqlite"
    if is_sqlite:
        connection.execute(text("PRAGMA foreign_keys=OFF"))
        connection.commit()

    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=is_sqlite,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()

    if is_sqlite:
        connection.execute(text("PRAGMA foreign_keys=ON"))
        connection.commit()


def run_migrations_online():
    connection = context.config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    with engine.connect() as connection:
        _run(connection)


if context.is_offline_mode():
    raise RuntimeError("Offline (SQL script) migrations are not supported.")

run_migrations_online()
