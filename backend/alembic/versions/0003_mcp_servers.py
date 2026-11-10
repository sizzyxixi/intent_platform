"""MCP 服务 + 子工具：mcp_servers 表；tools 增加 server / enabled / remote_missing；
存量 type=mcp 的工具按 MCP Server URL 归并成服务，子工具改名为 {服务名}.{远端工具名}，并改写意图里的工具绑定。

Revision ID: 0003
Revises: 0002
"""
import json
import re
from urllib.parse import urlparse

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def _slug(url: str, builtin: bool, used: set) -> str:
    if builtin:
        base = "intent_platform"
    else:
        p = urlparse(url or "")
        seg = [x for x in (p.path or "").split("/") if x]
        base = (seg[-1] if seg and seg[-1].lower() not in ("mcp", "sse") else (p.hostname or "mcp").split(".")[0])
        base = re.sub(r"[^a-z0-9_]", "_", base.lower()).strip("_") or "mcp"
        if not base[0].isalpha():
            base = "m_" + base
    name, i = base[:36], 2
    while name in used:
        name, i = f"{base[:33]}_{i}", i + 1
    used.add(name)
    return name


def upgrade() -> None:
    op.create_table(
        'mcp_servers',
        sa.Column('name', sa.String(length=40), nullable=False),
        sa.Column('display_name', sa.String(length=100), nullable=False),
        sa.Column('description', sa.Text(), nullable=False, server_default=''),
        sa.Column('transport', sa.String(length=20), nullable=False),
        sa.Column('url', sa.Text(), nullable=True),
        sa.Column('command', sa.Text(), nullable=True),
        sa.Column('args', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='[]'),
        sa.Column('env', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='{}'),
        sa.Column('auth', sa.String(length=20), nullable=False, server_default='无'),
        sa.Column('auth_header', sa.String(length=64), nullable=True),
        sa.Column('auth_env', sa.String(length=64), nullable=True),
        sa.Column('secret_encrypted', sa.Text(), nullable=True),
        sa.Column('source', sa.String(length=10), nullable=False, server_default='external'),
        sa.Column('owner', sa.String(length=100), nullable=False, server_default=''),
        sa.Column('position', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('server_info', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='{}'),
        sa.Column('last_sync_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_sync_by', sa.String(length=100), nullable=True),
        sa.Column('last_sync_source', sa.String(length=20), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('name'),
    )
    op.alter_column('tools', 'name', type_=sa.String(length=120))
    op.add_column('tools', sa.Column('server', sa.String(length=40), nullable=True))
    op.add_column('tools', sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column('tools', sa.Column('remote_missing', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_foreign_key('fk_tools_server', 'tools', 'mcp_servers', ['server'], ['name'], ondelete='CASCADE')
    op.create_index('ix_tools_server', 'tools', ['server'])

    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT name, config, source, owner, secret_encrypted, position FROM tools WHERE type = 'mcp' ORDER BY position, name"
    )).mappings().all()
    servers: dict[str, dict] = {}
    used: set = set()
    renames: dict[str, str] = {}
    for r in rows:
        cfg = dict(r["config"] or {})
        url = cfg.get("mcp_server") or ""
        key = (url, r["source"])
        if key not in servers:
            p = urlparse(url)
            servers[key] = {"name": _slug(url, r["source"] == "builtin", used), "url": url, "source": r["source"],
                            "owner": r["owner"], "position": r["position"], "secret": r["secret_encrypted"],
                            "display": ("意图管理平台（平台内置 MCP）" if r["source"] == "builtin"
                                        else f"{p.hostname or ''}{p.path or ''}" or url)}
        sv = servers[key]
        sv["secret"] = sv["secret"] or r["secret_encrypted"]
        remote = cfg.get("mcp_tool") or r["name"]
        new = f"{sv['name']}.{remote}"
        renames[r["name"]] = new
        cfg.pop("mcp_server", None)
        cfg.setdefault("confirm_required", False)
        cfg["mcp_tool"] = remote
        bind.execute(sa.text("UPDATE tools SET name = :new, config = CAST(:cfg AS jsonb), secret_encrypted = NULL "
                             "WHERE name = :old"), {"new": new, "cfg": json.dumps(cfg, ensure_ascii=False), "old": r["name"]})
        sv.setdefault("tools", []).append(new)
    for sv in servers.values():
        bind.execute(sa.text(
            "INSERT INTO mcp_servers (name, display_name, description, transport, url, auth, auth_header, secret_encrypted,"
            " source, owner, position) VALUES (:n, :d, '', 'streamable_http', :u, :a, :h, :s, :src, :o, :p)"),
            {"n": sv["name"], "d": sv["display"][:100], "u": sv["url"],
             "a": "Bearer Token" if (sv["secret"] or sv["source"] == "builtin") else "无",
             "h": None, "s": sv["secret"], "src": sv["source"], "o": sv["owner"] or "", "p": sv["position"]})
        bind.execute(sa.text("UPDATE tools SET server = :n WHERE name = ANY(:names)"), {"n": sv["name"], "names": sv["tools"]})
    # 改写意图里的工具绑定
    if renames:
        for code, tools in bind.execute(sa.text("SELECT code, tools FROM intents")).all():
            changed = False
            for b in tools or []:
                if b.get("tool") in renames:
                    b["tool"] = renames[b["tool"]]
                    changed = True
            if changed:
                bind.execute(sa.text("UPDATE intents SET tools = CAST(:t AS jsonb) WHERE code = :c"),
                             {"t": json.dumps(tools, ensure_ascii=False), "c": code})


def downgrade() -> None:
    bind = op.get_bind()
    renames = {}
    for name, cfg, server, url in bind.execute(sa.text(
            "SELECT t.name, t.config, t.server, s.url FROM tools t JOIN mcp_servers s ON s.name = t.server")).all():
        cfg = dict(cfg or {})
        cfg["mcp_server"] = url
        old = name.split(".", 1)[1].lower().replace("-", "_").replace(".", "_")[:64]
        renames[name] = old
        bind.execute(sa.text("UPDATE tools SET name = :o, config = CAST(:c AS jsonb) WHERE name = :n"),
                     {"o": old, "c": json.dumps(cfg, ensure_ascii=False), "n": name})
    for code, tools in bind.execute(sa.text("SELECT code, tools FROM intents")).all():
        if any(b.get("tool") in renames for b in tools or []):
            for b in tools:
                b["tool"] = renames.get(b["tool"], b["tool"])
            bind.execute(sa.text("UPDATE intents SET tools = CAST(:t AS jsonb) WHERE code = :c"),
                         {"t": json.dumps(tools, ensure_ascii=False), "c": code})
    op.drop_index('ix_tools_server', table_name='tools')
    op.drop_constraint('fk_tools_server', 'tools', type_='foreignkey')
    op.drop_column('tools', 'remote_missing')
    op.drop_column('tools', 'enabled')
    op.drop_column('tools', 'server')
    op.alter_column('tools', 'name', type_=sa.String(length=64))
    op.drop_table('mcp_servers')
