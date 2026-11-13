"""v0.19.2：知识库增加 description；把原适用范围标签拼进描述（描述为空时）。

Revision ID: 0007
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("knowledge_bases", sa.Column("description", sa.Text(), server_default="", nullable=False))
    conn = op.get_bind()
    from app.services.common import scope_tags_to_description
    rows = conn.execute(sa.text("SELECT id, tags, description FROM knowledge_bases")).mappings().all()
    for r in rows:
        if (r["description"] or "").strip():
            continue
        text = scope_tags_to_description(r["tags"] or {})
        if not text:
            continue
        conn.execute(sa.text("UPDATE knowledge_bases SET description = :d WHERE id = :id"),
                     {"d": text, "id": r["id"]})


def downgrade() -> None:
    op.drop_column("knowledge_bases", "description")
