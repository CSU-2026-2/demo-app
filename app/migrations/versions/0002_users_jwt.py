"""Пользователи, сессии JWT и перенос старых задач первому пользователю."""
import os
from alembic import op
import sqlalchemy as sa
from common.passwords import hash_password

revision = "0002_users_jwt"
down_revision = "0001_tasks"
branch_labels = depends_on = None


def upgrade():
    config = op.get_context().config
    username = config.attributes.get("bootstrap_username", os.getenv("AUTH_USERNAME", ""))
    password = config.attributes.get("bootstrap_password", os.getenv("AUTH_PASSWORD", ""))
    if not username or not password:
        raise RuntimeError("Set AUTH_USERNAME and AUTH_PASSWORD for the initial user")
    users = op.create_table("users", sa.Column("id", sa.Integer(), primary_key=True),
                            sa.Column("username", sa.String(32), nullable=False),
                            sa.Column("password_hash", sa.String(256), nullable=False))
    op.create_index("ix_users_username", "users", ["username"], unique=True)
    user_id = op.get_bind().execute(users.insert().values(
        username=username.lower(), password_hash=hash_password(password)
    ).returning(users.c.id)).scalar_one()
    op.create_table("auth_sessions", sa.Column("id", sa.String(32), primary_key=True),
                    sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
                    sa.Column("expires_at", sa.Integer(), nullable=False))
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])
    op.add_column("todos", sa.Column("owner_id", sa.Integer(), nullable=True))
    op.execute(sa.text("UPDATE todos SET owner_id=:owner").bindparams(owner=user_id))
    with op.batch_alter_table("todos") as batch:
        batch.alter_column("owner_id", existing_type=sa.Integer(), nullable=False, server_default=str(user_id))
        batch.create_foreign_key("fk_todos_owner_id_users", "users", ["owner_id"], ["id"])
        batch.create_index("ix_todos_owner_id", ["owner_id"])


def downgrade():
    with op.batch_alter_table("todos") as batch:
        batch.drop_index("ix_todos_owner_id")
        batch.drop_constraint("fk_todos_owner_id_users", type_="foreignkey")
        batch.drop_column("owner_id")
    op.drop_table("auth_sessions")
    op.drop_table("users")
