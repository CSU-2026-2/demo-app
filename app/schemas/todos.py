from typing import List

from pydantic import BaseModel, ConfigDict, Field


class TodoStep(BaseModel):
    id: int
    todo_id: int
    description: str
    completed: bool

    model_config = ConfigDict(from_attributes=True)


class TodoCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    completed: bool = False
    model_config = ConfigDict(str_strip_whitespace=True)


class StepUpdate(BaseModel):
    completed: bool


class TodoItem(TodoCreate):
    id: int
    steps: List[TodoStep] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)
