from typing import List

from crud import todos as crud_todos
from db.database import get_db
from fastapi import APIRouter, Depends, HTTPException
from schemas.todos import TodoCreate, TodoItem, TodoStep, StepUpdate
from common.models import TodoItem as TaskModel, TodoStep as StepModel
from sqlalchemy.orm import Session

router = APIRouter()


@router.get("/todos", response_model=List[TodoItem])
def read_todos(db: Session = Depends(get_db)):
    return crud_todos.get_todos(db)


@router.get("/todos/{todo_id}", response_model=TodoItem)
def read_todo(todo_id: int, db: Session = Depends(get_db)):
    todo = crud_todos.get_todo_by_id(db, todo_id)

    if todo is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return todo


@router.post("/todos", response_model=TodoItem)
def create_todo(todo: TodoCreate, db: Session = Depends(get_db)):
    return crud_todos.create_todo(db, todo)


@router.put("/todos/{todo_id}", response_model=TodoItem)
def update_todo(todo_id: int, todo: TodoCreate, db: Session = Depends(get_db)):
    db_todo = crud_todos.get_todo_by_id(db, todo_id)
    if db_todo is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return crud_todos.update_todo(db, db_todo, todo)


@router.delete("/todos/{todo_id}", response_model=TodoItem)
def delete_todo(todo_id: int, db: Session = Depends(get_db)):
    db_todo = crud_todos.get_todo_by_id(db, todo_id)
    if db_todo is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return crud_todos.delete_todo(db, db_todo)


@router.post("/todos/{todo_id}/generate", status_code=202)
def generate_plan(todo_id: int, db: Session = Depends(get_db)):
    todo = crud_todos.get_todo_by_id(db, todo_id)
    if todo is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if todo.steps:
        return {"status": "ready"}
    try:
        crud_todos.request_plan(todo)
    except Exception:
        raise HTTPException(status_code=503, detail="Не удалось отправить запрос. Попробуйте позже.")
    return {"status": "requested"}


@router.patch("/todos/{todo_id}/steps/{step_id}", response_model=TodoStep)
def update_step(todo_id: int, step_id: int, body: StepUpdate, db: Session = Depends(get_db)):
    # Сериализуем изменения шагов одной задачи, чтобы параллельные запросы
    # корректно вычисляли общий статус выполнения.
    todo = db.query(TaskModel).filter(TaskModel.id == todo_id).with_for_update().first()
    if todo is None:
        raise HTTPException(status_code=404, detail="Task not found")
    step = db.query(StepModel).filter(StepModel.id == step_id, StepModel.todo_id == todo_id).first()
    if step is None:
        raise HTTPException(status_code=404, detail="Step not found")
    try:
        step.completed = body.completed
        db.flush()
        todo.completed = not db.query(StepModel).filter(
            StepModel.todo_id == todo_id, StepModel.completed.is_(False)
        ).first()
        db.commit()
        db.refresh(step)
    except Exception:
        db.rollback()
        raise
    return step
