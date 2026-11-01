from sqlalchemy import Boolean, Column, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from common.database import Base


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(32), unique=True, nullable=False, index=True)
    password_hash = Column(String(256), nullable=False)


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    id = Column(String(32), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    expires_at = Column(Integer, nullable=False, index=True)


class TodoItem(Base):
    __tablename__ = "todos"
    id = Column(Integer, primary_key=True, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String, index=True)
    description = Column(String)
    completed = Column(Boolean, default=False)

    steps = relationship(
        "TodoStep",
        back_populates="todo",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class TodoStep(Base):
    __tablename__ = "todo_steps"
    id = Column(Integer, primary_key=True, index=True)
    todo_id = Column(Integer, ForeignKey("todos.id", ondelete="CASCADE"), index=True)
    description = Column(String)
    completed = Column(Boolean, default=False)

    todo = relationship("TodoItem", back_populates="steps")
