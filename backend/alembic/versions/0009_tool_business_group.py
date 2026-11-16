"""v0.20.1：工具与 MCP 服务增加业务分组（从 CLI config.group 迁入）。

Revision ID: 0009
Revises: 0008
"""
from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tools", sa.Column("business_group", sa.String(length=200), nullable=True))
    op.add_column("mcp_servers", sa.Column("business_group", sa.String(length=200), nullable=True))
    op.create_index("ix_tools_business_group", "tools", ["business_group"])
    op.execute(
        """
        UPDATE tools
        SET business_group = NULLIF(BTRIM(config->>'group'), '')
        WHERE NULLIF(BTRIM(config->>'group'), '') IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_index("ix_tools_business_group", table_name="tools")
    op.drop_column("mcp_servers", "business_group")
    op.drop_column("tools", "business_group")
