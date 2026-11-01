"""Миграции через DATABASE_URL; PostgreSQL lock защищает параллельный запуск API."""

import os
from alembic import context
from sqlalchemy import create_engine, text
from common.database import Base
from common import models

config = context.config


def run(connection):
    sqlite = connection.dialect.name == "sqlite"
    if sqlite:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.commit()
    try:
        with connection.begin():
            if connection.dialect.name == "postgresql":
                connection.execute(text("SELECT pg_advisory_xact_lock(68431901)"))
            context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True,
                              render_as_batch=sqlite)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        if sqlite:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.commit()


if context.is_offline_mode():
    raise RuntimeError("These data migrations require an online database connection")
elif config.attributes.get("connection") is not None:
    run(config.attributes["connection"])
else:
    engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    with engine.connect() as connection:
        run(connection)
