import logging

from db.models import TodoItem
from common.kafka_client import send_todo_created_event
from schemas.todos import TodoCreate, TodoItem as TodoResponse
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def request_plan(todo: TodoItem) -> None:
    """Отправить запрос генерации напрямую в Kafka; ошибку обрабатывает вызывающий код."""
    send_todo_created_event(todo_id=todo.id, title=todo.title)


def try_request_plan(todo: TodoItem) -> None:
    if todo.steps:
        return
    try:
        request_plan(todo)
    except Exception:
        logger.exception("Could not request plan for task %s; request can be retried", todo.id)


def get_todos(db: Session, owner_id: int):
    return db.query(TodoItem).filter(TodoItem.owner_id == owner_id).all()


def get_todo_by_id(db: Session, todo_id: int, owner_id: int):
    return db.query(TodoItem).filter(TodoItem.id == todo_id, TodoItem.owner_id == owner_id).first()


def create_todo(db: Session, todo: TodoCreate, owner_id: int):
    try:
        db_todo = TodoItem(**todo.model_dump(), owner_id=owner_id)
        db.add(db_todo)
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(db_todo)
    try_request_plan(db_todo)
    return db_todo


def update_todo(db: Session, db_todo: TodoItem, todo: TodoCreate):
    try:
        for key, value in todo.model_dump().items():
            setattr(db_todo, key, value)
        db.commit()
    except Exception:
        db.rollback()
        raise
    try_request_plan(db_todo)
    return db_todo


def delete_todo(db: Session, db_todo: TodoItem):
    # После DELETE объект отсоединён от сессии: сериализуем шаги заранее.
    response = TodoResponse.model_validate(db_todo)
    try:
        db.delete(db_todo)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return response
