"""种子数据：把 demo（index.html 的 seedState）全部导入数据库。

用法：
  python -m app.seed               # 空库时导入；已有数据则跳过
  python -m app.seed --reset       # 清空所有业务表后重新导入（会丢数据，仅用于演示/测试环境）
  python -m app.seed --admin-only  # 只确保初始管理员存在
演示用 3 个 API Key 会生成新的随机 Token，只在本次输出里打印一次。
"""
import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .config import get_settings
from .db import SessionLocal
from .models import (ApiKey, Chunk, Document, ImportRecord, Intent, IntentLog, KnowledgeBase, Setting, SlotDef, Space,
                     Tool, User, Category, McpServer, SkillPackage)
from .security import hash_password, new_api_token, token_hash, token_hint
from .services.common import TZ_CN, parse_local, utcnow
from .services.parsers import content_hash
from .services.search import backend
from .services import skills as skill_svc

SEED_FILE = Path(__file__).resolve().parents[1] / "seed" / "demo_state.json"
PREFIX = {"cs": "B", "mc": "M"}
FINAL = {"转人工": "handoff_human", "交给通用助手": "general_assistant", "告知无法处理": "decline"}
TOOL_TYPE = {"API 接口": "http", "MCP 工具": "mcp", "CLI 命令": "cli", "技能": "skill", "工作流": "workflow",
             "技能/工作流": "skill"}
KB_TYPE = {"表格": "表格知识库", "图文": "图文知识库-富文本"}
USAGE = {"参考": "reference", "校验": "validate"}
TABLES = ["intent_logs", "intents", "slots", "categories", "chunks", "documents", "knowledge_bases",
          "skill_packages", "tools", "mcp_servers", "import_records", "api_keys", "audit_logs", "idempotency_records", "rate_counters", "settings",
          "spaces", "users"]


def ensure_admin(db: Session) -> bool:
    s = get_settings()
    if db.scalar(select(User).where(User.username == s.admin_username)):
        return False
    db.add(User(username=s.admin_username, display_name=s.admin_display_name, role="admin",
                password_hash=hash_password(s.admin_password), is_active=True))
    db.commit()
    return True


def _t(s, default=None):
    return parse_local(s) or default or utcnow()


def seed(db: Session, data: dict, with_keys: bool = True) -> dict:
    st = data["state"]
    base = get_settings().public_base_url.rstrip("/")
    trees = {"cs": data["TREE"], "mc": data["MC_TREE"]}
    leaf: dict[tuple[str, str, str], int] = {}
    # 空间 + 设置
    for pos, sp in enumerate(st["spaces"]):
        rc = st["recog"][sp["id"]]
        fa = FINAL.get(rc.get("final_action"), "general_assistant")
        db.add(Space(id=sp["id"], name=sp["name"], description=sp.get("desc", ""), bot=sp["bot"],
                     code_prefix=PREFIX.get(sp["id"], sp["id"][:1].upper()), version=st["ver"][sp["id"]],
                     threshold=rc["threshold"], max_candidates=3, max_clarify=rc["max_clarify"],
                     max_slot_ask=rc["max_slot_ask"], tool_retry=rc["tool_retry"], final_action=fa,
                     clarify_prompt=rc["clarify_prompt"],
                     final_message=rc.get("final_msg") if fa == "decline" else None, position=pos,
                     levels=(st.get("levels") or {}).get(sp["id"]) or ["大类", "问题桶", "意图"]))
        db.flush()
        for i, l1 in enumerate(trees.get(sp["id"]) or data["GEN_TREE"]):
            a = Category(space_id=sp["id"], parent_id=None, depth=1, code=l1["c"], name=l1["n"], position=i)
            db.add(a)
            db.flush()
            for j, l2 in enumerate(l1["k"]):
                b = Category(space_id=sp["id"], parent_id=a.id, depth=2, code=None, name=l2["n"], position=j)
                db.add(b)
                db.flush()
                leaf[(sp["id"], l1["c"], l2["n"])] = b.id
        for i, s in enumerate(st["slots"].get(sp["id"], [])):
            db.add(SlotDef(space_id=sp["id"], name=s["n"], display_name=s.get("disp") or s["n"], type=s["type"],
                           options=[x.strip() for x in (s.get("opts") or "").split(",") if x.strip()],
                           default_ask=s.get("ask") or "", position=i))
    db.flush()
    # 知识库
    chunk_pks = []
    for pos, kb in enumerate(st["kbs"]):
        k = KnowledgeBase(id=kb["id"], name=kb["name"], type=KB_TYPE[kb["type"]],
                          headers=kb.get("headers") if kb["type"] == "表格" else None, tags=kb.get("tags") or {},
                          position=pos, created_at=_t(kb.get("created")), updated_at=_t(kb.get("updated")))
        db.add(k)
        for d in kb["docs"]:
            doc = Document(kb_id=kb["id"], doc_id=d["id"], name=d["name"], status=d.get("status") or "构建成功",
                           uploaded_at=_t(d.get("uploaded")), updated_at=_t(d.get("updated")),
                           content_hash=content_hash([c["content"] for c in d["chunks"]]))
            for c in d["chunks"]:
                doc.chunks.append(Chunk(kb_id=kb["id"], chunk_id=c["id"], position=c["order"], content=c["content"],
                                        cells=c.get("cells"), tags=c.get("tags") or {},
                                        updated_at=_t(c.get("updated"))))
            k.documents.append(doc)
        db.flush()
        chunk_pks += [c.id for d in k.documents for c in d.chunks]
    backend().reindex(db, chunk_pks)
    # 工具：先建 MCP 服务（demo 的 mcpServers，含凭据列表），再建工具
    servers: dict[str, McpServer] = {}
    for pos, v in enumerate(st.get("mcpServers") or []):
        creds = []
        for c in v.get("creds") or []:
            o = {"location": c.get("loc") or "header", "name": c["name"], "env": c.get("env") or ""}
            if c.get("scheme"):
                o["scheme"] = c["scheme"]
            creds.append(o)
        env = {}
        for line in str(v.get("env") or "").splitlines():
            if "=" in line:
                k, _, val = line.partition("=")
                env[k.strip()] = val.strip()
        sv = McpServer(name=v["name"], display_name=v.get("disp") or v["name"], description=v.get("desc") or "",
                       transport=v.get("transport") or "streamable_http",
                       url=(v.get("url") or "").replace("https://intent.example.com", base) or None,
                       command=v.get("command") or None, args=[x for x in str(v.get("args") or "").split() if x], env=env,
                       credentials=creds, example_data=bool(v.get("exampleSync")),
                       source="builtin" if v.get("builtin") else "external", owner=v.get("owner") or "", position=pos,
                       server_info={}, last_sync_at=parse_local(v.get("lastSyncAt")), last_sync_by=v.get("lastSyncBy") or None,
                       last_sync_source=("示例数据" if v.get("exampleSync") else (v.get("lastSyncSrc") or None)))
        db.add(sv)
        servers[sv.name] = sv
    db.flush()
    skill_pkgs = []
    for pos, t in enumerate(st["tools"]):
        typ = TOOL_TYPE[t["type"]]
        params = []
        for p in t.get("params") or []:
            o = {"name": p["n"], "type": p.get("type") or "string", "required": bool(p.get("req")), "description": p.get("desc") or ""}
            if p.get("def"):
                o["default"] = str(p["def"])
            params.append(o)
        url = (t.get("url") or "").replace("https://intent.example.com", base)
        server = None
        if typ == "http":
            cfg = {"url": url, "method": t.get("method") or "GET", "auth": t.get("auth") or "无",
                   "output_example": t.get("out") or ""}
        elif typ == "mcp":
            server = t.get("srv")
            remote = t.get("toolName") or t["name"].split(".", 1)[-1]
            cfg = {"mcp_tool": remote, "title": "", "input_schema": json.loads(t["schema"]) if t.get("schema") else None,
                   "annotations": {}, "output_example": t.get("out") or "", "confirm_required": bool(t.get("confirm"))}
        elif typ == "cli":
            cfg = {"bin": t.get("bin") or "", "command_template": t.get("cmd") or "", "auth": t.get("cliAuth") or "无",
                   "login_command": t.get("login") or "", "install": t.get("install") or "",
                   "verified_version": t.get("cliVer") or "",
                   "output_format": "json" if (t.get("outFmt") or "JSON").upper() == "JSON" else "text",
                   "confirm_required": bool(t.get("confirm")), "output_description": t.get("out") or ""}
        elif typ == "workflow":
            plat = t.get("wfPlatform") or "generic"
            cfg = {"platform": plat, "response_mode": "streaming" if t.get("wfMode") == "stream" else "blocking",
                   "timeout_sec": float(t["wfTimeout"]) if t.get("wfTimeout") else None,
                   "confirm_required": bool(t.get("confirm")), "auth_env": t.get("wfAuthEnv") or None,
                   "output_mapping": [m for m in t.get("wfOutMap") or [] if m.get("from") and m.get("as")],
                   "params_locked": plat != "generic", "output_description": t.get("out") or ""}
            if plat == "dify":
                cfg.update({"base_url": t.get("wfBase") or "https://api.dify.ai/v1",
                            "user": {"mode": "session"} if t.get("wfUserMode") == "session" else {"mode": "constant", "value": t.get("wfUserConst") or "intent-bot"}})
            elif plat == "coze":
                cfg.update({"region": t.get("wfRegion") or "coze.cn", "workflow_id": t.get("wfId") or "",
                            "workflow_version": t.get("wfVersion") or None, "is_async": bool(t.get("wfAsync")),
                            "bot_id": t.get("wfBotId") or None, "app_id": t.get("wfAppId") or None, "ext": None})
            else:
                cfg.update({"url": t.get("wfUrl") or "", "method": t.get("wfMethod") or "POST", "auth": t.get("wfAuth") or "无"})
        else:
            params = []
            md = t.get("skillMd") or f"---\nname: {t['disp']}\ndescription: {t['desc']}\n---\n"
            md = "---\nname: " + t["disp"] + "\ndescription: " + json.dumps(t["desc"], ensure_ascii=False) + "\n---\n" + md.split("\n---\n", 1)[-1]
            files = {"SKILL.md": md}
            for f in t.get("skillFiles") or []:
                if f["path"] != "SKILL.md":
                    files[f["path"]] = f"# {f['path']}\n\n演示数据：{t['disp']} 的技能文件占位内容。\n"
            pk = skill_svc.build_from_texts(files)
            skill_pkgs.append((t["name"], pk))
            cfg = {}
        db.add(Tool(name=t["name"], type=typ, server=server, display_name=t["disp"], description=t["desc"], params=params,
                    source="builtin" if t.get("builtin") else "external", maintenance=bool(t.get("maint")),
                    space_scoped=t.get("scope") == "space", endpoints=t.get("rest") or [], mcp_tools=t.get("mcp") or [],
                    enabled=t.get("enabled") is not False, remote_missing=bool(t.get("missing")),
                    config=cfg, owner=t.get("owner") or "", position=pos, updated_at=_t(t.get("updated"))))
    db.flush()
    for name, pk in skill_pkgs:
        pkg = SkillPackage(tool_name=name, version=1, created_by="种子数据", **pk)
        db.add(pkg)
        db.flush()
        tl = db.get(Tool, name)
        tl.config = skill_svc.config_of(pkg)
        tl.description = pkg.description
    renamed: dict[str, str] = {}
    skills = {n for n, _ in skill_pkgs}
    # 意图
    for pos, it in enumerate(st["intents"].values()):
        logs = [(_t(x["at"]), x["by"], x["msg"]) for x in it.get("log") or []]
        upd = max((a for a, _, _ in logs), default=utcnow())
        i = Intent(code=it["code"], space_id=it["space"], name=it["name"],
                   category_id=leaf[(it["space"], it["l1"], it["l2"])],
                   description=it.get("def") or "", examples=list(it.get("ex") or []),
                   slots=[{"name": s["n"], "required": s.get("req") == "必填", "ask": s.get("ask") or ""}
                          for s in it.get("slots") or []],
                   kb_refs=[{"kb_id": r["kb"], "doc_id": r["doc"], "chunk_id": r["chunk"],
                             "usage": USAGE.get(r.get("usage") or "参考", "reference")} for r in it.get("kbRefs") or []],
                   tools=[{"tool": renamed.get(b["name"], b["name"]), "param_map": {} if b["name"] in skills else
                           {k: {"source": v.get("src") or "槽位", "value": str(v.get("v") or "")} for k, v in (b.get("map") or {}).items()}}
                          for b in it.get("tools") or []],
                   status=it["status"], owner=it.get("owner") or "", position=pos,
                   created_at=min((a for a, _, _ in logs), default=upd), updated_at=upd)
        for a, by, msg in logs:
            i.logs.append(IntentLog(at=a, by=by, message=msg))
        db.add(i)
    db.flush()
    # 导入记录
    for r in st["imports"]:
        db.add(ImportRecord(id=r["id"], kind=r["kind"], space_id=r.get("space"), file=r["file"], source=r["src"],
                            at=_t(r["at"]), by=r["by"], result=r["res"], status="构建成功", details={}))
    mx = max(int(r["id"].split("-")[1]) for r in st["imports"])
    db.execute(text("SELECT setval('import_seq', :v, true)"), {"v": mx})
    # API Key（新随机 Token）
    tokens = {}
    for k in (st["keys"] if with_keys else []):
        tok = new_api_token()
        tokens[k["id"]] = tok
        db.add(ApiKey(id=k["id"], name=k["name"], owner=k["owner"], token_hash=token_hash(tok),
                      token_hint=token_hint(k["id"]), spaces=k["spaces"], scopes=k["scopes"], status=k["status"],
                      created_at=_t(k.get("at")), last_used_at=parse_local(k.get("last"))))
    db.merge(Setting(key="kb_search", value={"top_k": 5, "threshold": 0.2}))
    db.commit()
    return tokens


def counts(db: Session) -> dict:
    out = {}
    for sid in ("cs", "mc"):
        out[sid] = {
            "大类": db.scalar(select(func.count()).select_from(Category).where(Category.space_id == sid, Category.depth == 1)),
            "问题桶": db.scalar(select(func.count()).select_from(Category).where(Category.space_id == sid, Category.depth == 2)),
            "意图": db.scalar(select(func.count()).select_from(Intent).where(Intent.space_id == sid)),
            "slots": db.scalar(select(func.count()).select_from(SlotDef).where(SlotDef.space_id == sid)),
        }
    out["kbs"] = db.scalar(select(func.count()).select_from(KnowledgeBase))
    out["tools"] = db.scalar(select(func.count()).select_from(Tool))
    out["builtin_tools"] = db.scalar(select(func.count()).select_from(Tool).where(Tool.source == "builtin"))
    out["mcp_servers"] = db.scalar(select(func.count()).select_from(McpServer))
    return out


def reset(db: Session) -> None:
    db.execute(text("TRUNCATE " + ", ".join(TABLES) + " RESTART IDENTITY CASCADE"))
    db.commit()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="导入 demo 种子数据")
    ap.add_argument("--reset", action="store_true", help="清空所有业务表后重新导入")
    ap.add_argument("--admin-only", action="store_true")
    ap.add_argument("--file", default=str(SEED_FILE))
    ap.add_argument("--demo-keys", choices=["auto", "yes", "no"], default="auto",
                    help="是否创建 demo 的 3 个 API Key；auto=非生产环境创建，生产环境不创建")
    ap.add_argument("--tokens-out", help="把演示 Key 的 Token 写入该文件（JSON），便于脚本使用")
    a = ap.parse_args(argv)
    with SessionLocal() as db:
        if a.reset:
            if get_settings().env == "production":
                print("生产环境禁止 --reset", file=sys.stderr)
                return 2
            reset(db)
        if not a.admin_only:
            if db.scalar(select(func.count()).select_from(Space)):
                print("已有数据，跳过种子导入（如需重建用 --reset）")
            else:
                wk = a.demo_keys == "yes" or (a.demo_keys == "auto" and get_settings().env != "production")
                tokens = seed(db, json.loads(Path(a.file).read_text(encoding="utf-8")), with_keys=wk)
                print("种子数据已导入：", json.dumps(counts(db), ensure_ascii=False))
                if tokens:
                    print("演示 API Key（只显示这一次，请妥善保存）：")
                    for kid, tok in tokens.items():
                        print(f"  {kid}  {tok}")
                else:
                    print("未创建演示 API Key（生产环境请在控制台「工具中心 › 接入与密钥」新建）")
                if a.tokens_out:
                    Path(a.tokens_out).write_text(json.dumps(tokens, indent=1))
        if ensure_admin(db):
            s = get_settings()
            print(f"已创建初始管理员：{s.admin_username}（密码取自 IP_ADMIN_PASSWORD）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

_ = TZ_CN
