"""v0.16：按官方命令目录批量接入 CLI 工具（西之月 xclaw-cli）。

- tools.display_name 加长到 255（官方命令目录里的命令名最长 255 字）。
- CLI 工具 config 新增可选字段 auth_env / pack / pack_name / group / source_ref / risk_level（JSONB，无需改表）。

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("tools", "display_name", type_=sa.String(255), existing_type=sa.String(100), existing_nullable=False)


def downgrade() -> None:
    op.execute("UPDATE tools SET display_name = left(display_name, 100) WHERE length(display_name) > 100")
    op.alter_column("tools", "display_name", type_=sa.String(100), existing_type=sa.String(255), existing_nullable=False)
