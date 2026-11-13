"""v0.19.3：意图增加 handling；空间增加 fallback（由原 final_action 迁移）。

Revision ID: 0008
Revises: 0007
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "intents",
        sa.Column("handling", JSONB(), nullable=False,
                  server_default=sa.text("'{\"mode\": \"tool_flow\"}'::jsonb")),
    )
    op.add_column("spaces", sa.Column("fallback", JSONB(), nullable=True))
    conn = op.get_bind()
    from app.services.handling import from_legacy
    rows = conn.execute(sa.text("SELECT id, final_action, final_message FROM spaces")).mappings().all()
    for r in rows:
        fb = from_legacy(r["final_action"], r["final_message"])
        conn.execute(sa.text("UPDATE spaces SET fallback = CAST(:fb AS jsonb) WHERE id = :id"),
                     {"fb": __import__("json").dumps(fb, ensure_ascii=False), "id": r["id"]})


def downgrade() -> None:
    op.drop_column("spaces", "fallback")
    op.drop_column("intents", "handling")
