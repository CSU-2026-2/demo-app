import json
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.todos import router
from crud import todos as crud
from common.database import get_db
from common.models import TodoItem, TodoStep
from crud.todos import create_todo
from schemas.todos import TodoCreate
from worker import main as worker


@pytest.fixture(autouse=True)
def kafka_sender(monkeypatch):
    sender = Mock()
    monkeypatch.setattr(crud, "send_todo_created_event", sender)
    return sender


def new_todo(db, title="Learn Python"):
    return create_todo(db, TodoCreate(title=title, description="Practice"))


def record(value, offset=7):
    return SimpleNamespace(value=value, topic="todos-created", partition=0, offset=offset)


def test_frontend_is_served_with_assets():
    from main import app
    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert '<html lang="ru">' in response.text
        for path in ("/static/app.js", "/static/style.css"):
            assert client.get(path).status_code == 200
        assert client.get("/static/nonexistent.js").status_code == 404


def test_api_crud_and_nested_steps(db):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as client:
        assert client.get("/todos").json() == []
        body = {"title": "Learn Python", "description": "Practice", "completed": False}
        response = client.post("/todos", json=body)
        assert response.status_code == 200
        todo_id = response.json()["id"]
        db.add(TodoStep(todo_id=todo_id, description="Read a tutorial", completed=False))
        db.commit()
        assert len(client.get("/todos").json()[0]["steps"]) == 1
        body["completed"] = True
        assert client.put(f"/todos/{todo_id}", json=body).json()["completed"] is True
        assert client.get(f"/todos/{todo_id}").json()["steps"][0]["completed"] is False
        assert client.delete(f"/todos/{todo_id}").status_code == 200
        assert db.query(TodoStep).count() == 0
        assert client.get(f"/todos/{todo_id}").status_code == 404
        assert client.put(f"/todos/{todo_id}", json=body).status_code == 404
        assert client.delete(f"/todos/{todo_id}").status_code == 404


def test_kafka_failure_keeps_task_and_allows_retry(db, kafka_sender):
    kafka_sender.side_effect = RuntimeError("Kafka unavailable")
    todo = new_todo(db)
    assert db.query(TodoItem).count() == 1
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as client:
        assert client.post(f"/todos/{todo.id}/generate").status_code == 503
        kafka_sender.side_effect = None
        assert client.post(f"/todos/{todo.id}/generate").status_code == 202
        kafka_sender.assert_called_with(todo_id=todo.id, title=todo.title)
        assert client.post("/todos/999/generate").status_code == 404


def test_step_completion_and_task_ownership(db, kafka_sender):
    todo = new_todo(db)
    other = new_todo(db, "Other task")
    step = TodoStep(todo_id=todo.id, description="Practice", completed=False)
    db.add(step)
    db.commit()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as client:
        assert client.patch(f"/todos/{other.id}/steps/{step.id}", json={"completed": True}).status_code == 404
        assert client.patch(f"/todos/{todo.id}/steps/{step.id}", json={"completed": True}).json()["completed"] is True
        kafka_sender.reset_mock()
        assert client.post(f"/todos/{todo.id}/generate").json()["status"] == "ready"
        kafka_sender.assert_not_called()


@pytest.mark.parametrize("title", ["", "   ", "x" * 201])
def test_invalid_task_title_is_rejected(db, title):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as client:
        assert client.post("/todos", json={"title": title}).status_code == 422


def test_task_completes_with_last_step_and_reopens_when_unchecked(db):
    todo = new_todo(db)
    steps = [TodoStep(todo_id=todo.id, description=text, completed=False) for text in ("First", "Last")]
    db.add_all(steps)
    db.commit()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as client:
        def patch_step(step, completed):
            response = client.patch(f"/todos/{todo.id}/steps/{step.id}", json={"completed": completed})
            assert response.status_code == 200
        patch_step(steps[0], True)
        assert client.get(f"/todos/{todo.id}").json()["completed"] is False
        patch_step(steps[1], True)
        assert client.get("/todos").json()[0]["completed"] is True
        patch_step(steps[1], True)
        assert client.get(f"/todos/{todo.id}").json()["completed"] is True
        patch_step(steps[0], False)
        assert client.get(f"/todos/{todo.id}").json()["completed"] is False


@pytest.mark.parametrize("content", ["", "[]", '[null]', '[" "]', "Service unavailable"])
def test_invalid_llm_plan_is_retried(content, monkeypatch):
    client = Mock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )
    monkeypatch.setattr(worker, "get_llm_client", lambda: client)
    with pytest.raises(ValueError):
        worker.llm_generate_steps("Title")


def test_llm_fenced_json_plan(monkeypatch):
    client = Mock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='```json\n[" Read ", "Practice"]\n```'))]
    )
    monkeypatch.setattr(worker, "get_llm_client", lambda: client)
    assert worker.llm_generate_steps("Title") == ["Read", "Practice"]


def test_description_is_sent_to_llm_and_ten_steps_are_kept(monkeypatch):
    steps = [f"Step {index}" for index in range(1, 11)]
    client = Mock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(steps)))]
    )
    monkeypatch.setattr(worker, "get_llm_client", lambda: client)
    assert worker.llm_generate_steps("Explain Kubernetes", "В 10 шагах") == steps
    messages = client.chat.completions.create.call_args.kwargs["messages"]
    assert json.loads(messages[1]["content"]) == {
        "title": "Explain Kubernetes", "description": "В 10 шагах"
    }
    assert "ровно столько шагов" in messages[0]["content"]


def test_worker_uses_current_description_and_saves_ten_steps(db, monkeypatch):
    todo = new_todo(db)
    todo.description = "В 10 шагах"
    db.commit()
    steps = [f"Step {index}" for index in range(1, 11)]
    generate = Mock(return_value=steps)
    monkeypatch.setattr(worker, "llm_generate_steps", generate)
    worker.process_message(db, todo.id, "Stale event title")
    generate.assert_called_once_with(todo.title, "В 10 шагах")
    assert [step.description for step in db.query(TodoStep).order_by(TodoStep.id)] == steps
    worker.process_message(db, todo.id, "Repeated event")
    generate.assert_called_once()


def test_worker_commit_failure_replay_is_idempotent(db, session_factory, monkeypatch):
    todo = new_todo(db)
    todo_id = todo.id
    db.close()
    generate = Mock(return_value=["Read", "Practice"])
    monkeypatch.setattr(worker, "llm_generate_steps", generate)
    monkeypatch.setattr(worker, "SessionLocal", session_factory)
    consumer = Mock()
    consumer.commit.side_effect = RuntimeError("commit failed")
    msg = record(json.dumps({"todo_id": todo_id, "title": "Stale title"}).encode())
    with pytest.raises(RuntimeError):
        worker.handle_record(consumer, msg)
    consumer.commit.side_effect = None
    worker.handle_record(consumer, msg)
    assert db.query(TodoStep).count() == 2
    generate.assert_called_once_with("Learn Python", "Practice")
    offsets = consumer.commit.call_args.args[0]
    assert next(iter(offsets.values())).offset == 8


def test_worker_llm_failure_does_not_commit(db, session_factory, monkeypatch):
    todo_id = new_todo(db).id
    db.close()
    monkeypatch.setattr(worker, "SessionLocal", session_factory)
    monkeypatch.setattr(worker, "llm_generate_steps", Mock(side_effect=RuntimeError("LLM down")))
    consumer = Mock()
    with pytest.raises(RuntimeError):
        worker.handle_record(consumer, record(json.dumps({"todo_id": todo_id}).encode()))
    consumer.commit.assert_not_called()
    assert db.query(TodoStep).count() == 0


@pytest.mark.parametrize("payload", [b"broken JSON", b"[]", b'{"todo_id":true}', b'{"todo_id":-1}', None])
def test_invalid_events_are_skipped(payload):
    consumer = Mock()
    worker.handle_record(consumer, record(payload))
    consumer.commit.assert_called_once()


def test_deleted_todo_event_is_acknowledged(db, session_factory, monkeypatch):
    monkeypatch.setattr(worker, "SessionLocal", session_factory)
    consumer = Mock()
    worker.handle_record(consumer, record(b'{"todo_id":999}'))
    consumer.commit.assert_called_once()
    assert db.query(TodoStep).count() == 0


def test_concurrent_deliveries_generate_one_plan(db, session_factory, monkeypatch):
    if db.bind.dialect.name != "postgresql":
        pytest.skip("Requires PostgreSQL row locks")
    todo_id = new_todo(db).id
    db.close()
    entered, release, second_started = Event(), Event(), Event()

    def generate(_title, _description):
        entered.set()
        assert release.wait(10)
        return ["Read", "Practice"]

    generator = Mock(side_effect=generate)
    monkeypatch.setattr(worker, "llm_generate_steps", generator)

    def process(second=False):
        with session_factory() as session:
            if second:
                second_started.set()
            worker.process_message(session, todo_id, "Title")

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(process)
        assert entered.wait(5)
        second = pool.submit(process, True)
        try:
            assert second_started.wait(5)
            with pytest.raises(TimeoutError):
                second.result(timeout=0.3)
        finally:
            release.set()
        first.result(timeout=10)
        second.result(timeout=10)
    generator.assert_called_once()
    assert db.query(TodoStep).count() == 2


def test_concurrent_step_completion_updates_task(db, session_factory):
    if db.bind.dialect.name != "postgresql":
        pytest.skip("Requires PostgreSQL row locks")
    from api.todos import update_step
    from schemas.todos import StepUpdate
    todo = new_todo(db)
    todo_id = todo.id
    db.add_all([TodoStep(todo_id=todo_id, description=str(index), completed=False) for index in range(4)])
    db.commit()
    ids = [step.id for step in db.query(TodoStep).filter_by(todo_id=todo_id)]
    db.close()

    def complete(step_id):
        with session_factory() as session:
            update_step(todo_id, step_id, StepUpdate(completed=True), session)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(complete, ids))
    assert db.get(TodoItem, todo_id).completed is True
    assert db.query(TodoStep).filter_by(todo_id=todo_id, completed=True).count() == 4
