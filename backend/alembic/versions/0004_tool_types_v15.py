"""v0.15：MCP 鉴权改为凭据列表；新增技能包表；工具类型增加 workflow；旧「技能/工作流」改为技能（需重新上传技能包）。

- mcp_servers：新增 credentials（[{location, name, env, scheme?}]）、example_data；auth / auth_header / auth_env 按原语义转成凭据后删除。
  原 secret_encrypted（单个密钥）保留，运行时视为第一条凭据的密钥。
- skill_packages：技能包（zip）及版本。
- tools：type=skill 的旧记录（skill_id + platform）保留为技能，入参清空、config 记 legacy，意图里对它的入参映射清空。

Revision ID: 0004
Revises: 0003
"""
import json

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def _creds(name: str, auth: str, header: str | None, env: str | None) -> list:
    tok = f"MCP_{name.upper()}_TOKEN"
    if auth == "Bearer Token":
        return [{"location": "header", "name": "Authorization", "env": tok, "scheme": "Bearer"}]
    if auth == "API Key":
        return [{"location": "header", "name": header or "X-API-Key", "env": tok}]
    if auth == "环境变量" and env:
        return [{"location": "env", "name": env, "env": env}]
    return []


def upgrade() -> None:
    op.add_column("mcp_servers", sa.Column("credentials", postgresql.JSONB(), nullable=False, server_default="[]"))
    op.add_column("mcp_servers", sa.Column("example_data", sa.Boolean(), nullable=False, server_default=sa.false()))
    conn = op.get_bind()
    for name, auth, header, env in conn.execute(sa.text("SELECT name, auth, auth_header, auth_env FROM mcp_servers")):
        conn.execute(sa.text("UPDATE mcp_servers SET credentials = CAST(:c AS jsonb) WHERE name = :n"),
                     {"c": json.dumps(_creds(name, auth or "无", header, env)), "n": name})
    op.drop_column("mcp_servers", "auth")
    op.drop_column("mcp_servers", "auth_header")
    op.drop_column("mcp_servers", "auth_env")

    op.create_table(
        "skill_packages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tool_name", sa.String(120), sa.ForeignKey("tools.name", ondelete="CASCADE"), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("skill_md", sa.Text(), nullable=False),
        sa.Column("files", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("source", sa.String(10), nullable=False, server_default="folder"),
        sa.Column("original_name", sa.String(255), nullable=True),
        sa.Column("size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("archive", sa.LargeBinary(), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_skill_packages_tool_name", "skill_packages", ["tool_name"])
    op.create_index("uq_skill_packages_tool_version", "skill_packages", ["tool_name", "version"], unique=True,
                    postgresql_where=sa.text("tool_name IS NOT NULL"))

    legacy = {}
    for name, cfg in conn.execute(sa.text("SELECT name, config FROM tools WHERE type = 'skill'")):
        cfg = cfg or {}
        legacy[name] = True
        new = {"version": 0, "package_id": None, "skill_name": "", "files": [], "source": "folder",
               "legacy": {"skill_id": cfg.get("skill_id", ""), "platform": cfg.get("platform", ""),
                          "output_description": cfg.get("output_description", "")}}
        conn.execute(sa.text("UPDATE tools SET params = '[]'::jsonb, config = CAST(:c AS jsonb) WHERE name = :n"),
                     {"c": json.dumps(new, ensure_ascii=False), "n": name})
    if legacy:
        for code, tools in conn.execute(sa.text("SELECT code, tools FROM intents")):
            changed = False
            for b in tools or []:
                if b.get("tool") in legacy and b.get("param_map"):
                    b["param_map"] = {}
                    changed = True
            if changed:
                conn.execute(sa.text("UPDATE intents SET tools = CAST(:t AS jsonb) WHERE code = :c"),
                             {"t": json.dumps(tools, ensure_ascii=False), "c": code})


def downgrade() -> None:
    op.drop_index("uq_skill_packages_tool_version", table_name="skill_packages")
    op.drop_index("ix_skill_packages_tool_name", table_name="skill_packages")
    op.drop_table("skill_packages")
    op.add_column("mcp_servers", sa.Column("auth", sa.String(20), nullable=False, server_default="无"))
    op.add_column("mcp_servers", sa.Column("auth_header", sa.String(64), nullable=True))
    op.add_column("mcp_servers", sa.Column("auth_env", sa.String(64), nullable=True))
    conn = op.get_bind()
    for name, creds in conn.execute(sa.text("SELECT name, credentials FROM mcp_servers")):
        c = (creds or [None])[0] if creds else None
        if not c:
            continue
        if c.get("location") == "env":
            conn.execute(sa.text("UPDATE mcp_servers SET auth='环境变量', auth_env=:e WHERE name=:n"), {"e": c["env"], "n": name})
        elif c.get("scheme") == "Bearer":
            conn.execute(sa.text("UPDATE mcp_servers SET auth='Bearer Token' WHERE name=:n"), {"n": name})
        elif c.get("location") == "header":
            conn.execute(sa.text("UPDATE mcp_servers SET auth='API Key', auth_header=:h WHERE name=:n"), {"h": c["name"], "n": name})
    op.drop_column("mcp_servers", "example_data")
    op.drop_column("mcp_servers", "credentials")
