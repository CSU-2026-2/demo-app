"""Исходная схема задач; принятие существующей схемы без удаления данных."""
from alembic import op
import sqlalchemy as sa

revision = "0001_tasks"
down_revision = None
branch_labels = depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if {"todos", "todo_steps"} & tables:
        expected = {"todos": {"id", "title", "description", "completed"},
                    "todo_steps": {"id", "todo_id", "description", "completed"}}
        for table, columns in expected.items():
            if table not in tables or {col["name"] for col in inspector.get_columns(table)} != columns:
                raise RuntimeError(f"Existing {table} schema does not match the legacy baseline")
        return
    op.create_table("todos", sa.Column("id", sa.Integer(), primary_key=True),
                    sa.Column("title", sa.String()), sa.Column("description", sa.String()),
                    sa.Column("completed", sa.Boolean()))
    op.create_index("ix_todos_id", "todos", ["id"])
    op.create_index("ix_todos_title", "todos", ["title"])
    op.create_table("todo_steps", sa.Column("id", sa.Integer(), primary_key=True),
                    sa.Column("todo_id", sa.Integer(), sa.ForeignKey("todos.id", ondelete="CASCADE")),
                    sa.Column("description", sa.String()), sa.Column("completed", sa.Boolean()))
    op.create_index("ix_todo_steps_id", "todo_steps", ["id"])
    op.create_index("ix_todo_steps_todo_id", "todo_steps", ["todo_id"])


def downgrade():
    op.drop_table("todo_steps")
    op.drop_table("todos")
