"""Программный запуск тех же ревизий Alembic, что использует CLI."""

from pathlib import Path
from alembic import command
from alembic.config import Config


def migration_config():
    return Config(str(Path(__file__).with_name("alembic.ini")))


def initialize_schema(engine, username: str, password: str) -> None:
    config = migration_config()
    config.attributes.update(bootstrap_username=username, bootstrap_password=password)
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
