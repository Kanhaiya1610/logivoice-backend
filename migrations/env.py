from logging.config import fileConfig
from pathlib import Path

from alembic import context
from flask import current_app
from sqlalchemy.engine import Engine

from app.extensions import db
from app import models  # noqa: F401 - registers model metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = db.metadata


def get_url():
    return str(current_app.extensions["migrate"].db.engine.url).replace("%", "%%")


def ensure_sqlite_database_directory(connectable: Engine) -> None:
    """Create the parent of a file-backed SQLite database before connecting.

    GitHub Actions starts from a clean checkout, so the ignored ``instance/``
    directory may not exist yet. This guard lives in the migration entry point
    as well as the application factory because Alembic owns the connection at
    this stage.
    """

    if connectable.url.get_backend_name() != "sqlite":
        return

    database = connectable.url.database
    if not database or database == ":memory:":
        return

    database_path = Path(database)
    if not database_path.is_absolute():
        database_path = Path.cwd() / database_path
    database_path.parent.mkdir(parents=True, exist_ok=True)


def run_migrations_offline():
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = current_app.extensions["migrate"].db.engine
    ensure_sqlite_database_directory(connectable)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
