from sqlalchemy import inspect, text
from alembic import command
import pytest
from common.database import Base
from migrate import initialize_schema, migration_config
from common.models import User
from common.passwords import verify_password


@pytest.fixture
def migration_engine(session_factory):
    engine = session_factory.kw["bind"]
    Base.metadata.drop_all(engine)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
    yield engine
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS alembic_version"))


def test_legacy_tasks_get_owner_without_losing_steps(session_factory, migration_engine):
    engine = migration_engine
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE todos (id INTEGER PRIMARY KEY, title VARCHAR, description VARCHAR, completed BOOLEAN)"))
        connection.execute(text("CREATE TABLE todo_steps (id INTEGER PRIMARY KEY, todo_id INTEGER REFERENCES todos(id), description VARCHAR, completed BOOLEAN)"))
        connection.execute(text("INSERT INTO todos VALUES (42, 'Legacy task', 'Keep me', true)"))
        connection.execute(text("INSERT INTO todo_steps VALUES (84, 42, 'Finished step', true)"))
    initialize_schema(engine, "demo", "bootstrap-password")
    initialize_schema(engine, "demo", "different-password")
    with session_factory() as db:
        user = db.query(User).one()
        assert verify_password("bootstrap-password", user.password_hash)
        row = db.execute(text("SELECT owner_id, completed FROM todos WHERE id=42")).one()
        assert row.owner_id == user.id
        assert row.completed
        assert db.execute(text("SELECT completed FROM todo_steps WHERE id=84")).scalar()
    if engine.dialect.name == "postgresql":
        columns = {column["name"]: column for column in inspect(engine).get_columns("todos")}
        assert columns["owner_id"]["nullable"] is False


def test_empty_database_upgrade_matches_models_and_downgrade_preserves_tasks(migration_engine):
    engine = migration_engine
    initialize_schema(engine, "demo", "bootstrap-password")
    with engine.connect() as connection:
        config = migration_config()
        config.attributes["connection"] = connection
        command.check(config)
        with connection.begin():
            owner = connection.execute(text("SELECT id FROM users")).scalar_one()
            connection.execute(text("INSERT INTO todos(id,title,completed,owner_id) VALUES (42,'Task',false,:owner)"), {"owner": owner})
        command.downgrade(config, "0001_tasks")
        assert connection.execute(text("SELECT title FROM todos WHERE id=42")).scalar_one() == "Task"
        connection.commit()
        config.attributes.update(bootstrap_username="demo", bootstrap_password="bootstrap-password")
        command.upgrade(config, "head")
        assert connection.execute(text("SELECT owner_id FROM todos WHERE id=42")).scalar_one() is not None
