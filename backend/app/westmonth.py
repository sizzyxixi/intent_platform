"""西之月开放平台 xclaw-cli（https://open.westmonth.com/cli）工具接入。

工具来源全部是官方数据，不手写：
- CLI 命令：`xclaw-cli catalog index --json`（官方机器可读命令目录），每条命令登记为一个 CLI 命令工具；
- 技能：`xclaw-cli skills list` 给出的 5 个官方 Skills 压缩包（xclaw-cli-assistant / mall / operate / platform / supply）。
快照存放在 backend/seed/westmonth/（catalog.json + skills/*.zip），可用 refresh-fixture 从官方重新生成。

用法：
  python -m app.westmonth refresh-fixture --catalog index.json --skills-dir zips/   # 用新导出的官方目录刷新快照
  python -m app.westmonth purge-examples --backup backup.json --yes   # 删除示例外部工具 / MCP 服务（先备份），保留平台内置
  python -m app.westmonth import                                     # 按快照登记 / 更新西之月工具（幂等）
  python -m app.westmonth status
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import sys
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .models import Intent, McpServer, SkillPackage, Tool
from .services import intents as intent_svc
from .services import skills as skill_svc
from .services import spaces as space_svc
from .services import tools as tool_svc
from .services.common import utcnow

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "seed" / "westmonth"
OWNER = "西之月开放平台"
BIN = "xclaw-cli"
INSTALL = "curl -fsSL https://open.westmonth.com/install | bash（Windows：irm https://open.westmonth.com/install.ps1 | iex）"
LOGIN = "xclaw-cli auth login"
# 目录里未标记需确认、但会改动订单金额的命令：平台侧强制「写操作需确认」（1688 客服映射表 R1–R3：成交类工具需人确认）
CONFIRM_OVERRIDE = {"platform.alibaba.trade-order-modifyorderprice.call"}
AUTH_ENV = "XCLAW_UMC_TOKEN"  # 官方文档：业务命令凭证优先链 XCLAW_UMC_TOKEN → .xclaw.env.json → 本地 credentials.json
PACK_NAMES = {"goods": "商品"}
SKILL_ORDER = ["xclaw-cli-assistant", "xclaw-mall", "xclaw-operate", "xclaw-platform", "xclaw-supply"]
ARG_KEEP = ("name", "sourceName", "type", "in", "description", "summary")


def _arg(a: dict) -> dict:
    o = {k: a[k] for k in ARG_KEEP if a.get(k) not in (None, "")}
    if o.get("summary") == o.get("description"):
        o.pop("summary", None)
    if o.get("sourceName") == o["name"].replace("-", "_"):
        o.pop("sourceName", None)
    return o


# ---------------- 快照 ----------------
def trim_catalog(index: dict, cli_version: str) -> dict:
    d = index.get("data", index)
    cmds = []
    for c in d["commands"]:
        cmds.append({
            "operationId": c["operationId"], "path": c["generatedCommandPath"],
            "displayName": c.get("displayName") or "", "description": c.get("description") or "",
            "packCode": c.get("packCode") or "", "packName": c.get("packName") or "",
            "capabilityCode": c.get("capabilityCode") or "", "confirmRequired": bool(c.get("confirmRequired")),
            "riskLevel": c.get("riskLevel") or "",
            "requiredArgs": [_arg(a) for a in c.get("requiredArgs") or []],
            "optionalArgs": [_arg(a) for a in c.get("optionalArgs") or []],
        })
    cmds.sort(key=lambda c: c["operationId"])
    return {"source": "xclaw-cli catalog index --json", "cli_version": cli_version,
            "release_version": d.get("releaseVersion"), "release_id": d.get("releaseId"),
            "generated_at": d.get("generatedAt"), "env": d.get("env"), "commands": cmds}


def load_fixture(fixture_dir: Path = FIXTURE_DIR) -> tuple[dict, dict[str, bytes]]:
    cat = json.loads((fixture_dir / "catalog.json").read_text(encoding="utf-8"))
    skills = {p.stem: p.read_bytes() for p in sorted((fixture_dir / "skills").glob("*.zip"))}
    return cat, skills


# ---------------- 转换 ----------------
def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def tool_names(cmds: list[dict]) -> dict[str, str]:
    """operationId → 工具名。'.' 和 '-' 都转成 '_'；转换后撞名的，统一追加 operationId 的短哈希保证唯一且稳定。"""
    base = {c["operationId"]: "xclaw_" + _slug(c["operationId"]) for c in cmds}
    cnt: dict[str, int] = {}
    for v in base.values():
        cnt[v] = cnt.get(v, 0) + 1
    return {op: (v if cnt[v] == 1 else f"{v}_{hashlib.sha1(op.encode()).hexdigest()[:6]}") for op, v in base.items()}


def _ptype(t: str) -> tuple[str, str]:
    t = (t or "string").lower()
    if t in ("integer", "number"):
        return "number", ""
    if t == "boolean":
        return "boolean", ""
    if t == "string":
        return "string", ""
    return "string", f"（{t}，按 JSON 传入）"  # object / array / array[object]：CLI 选项值为 JSON


def cli_payload(c: dict, name: str, cli_version: str) -> dict:
    params, flags = [], []
    for req, args in ((True, c.get("requiredArgs") or []), (False, c.get("optionalArgs") or [])):
        for a in args:
            flag = a["name"]
            pn = flag.replace("-", "_")
            typ, note = _ptype(a.get("type"))
            desc = (a.get("description") or a.get("summary") or "").strip() + note
            params.append({"name": pn, "type": typ, "required": req, "description": desc})
            flags.append(f"--{flag} {{{pn}}}")
    tpl = " ".join([BIN, c["path"], *flags, "--json"])
    pack = c.get("packCode") or c["path"].split()[0]
    disp = (c.get("displayName") or c.get("description") or c["path"]).strip()
    desc = (c.get("description") or disp).strip()
    return {"name": name, "type": "cli", "display_name": disp[:255], "description": desc, "owner": OWNER,
            "params": params,
            "config": {"bin": BIN, "command_template": tpl, "auth": "CLI 内置登录（OAuth）", "login_command": LOGIN,
                       "install": INSTALL, "verified_version": cli_version, "output_format": "json",
                       "confirm_required": bool(c.get("confirmRequired")) or c["operationId"] in CONFIRM_OVERRIDE,
                       "output_description": f"JSON（--json）；参数与返回以 xclaw-cli schema {c['path']} --json 为准",
                       "auth_env": AUTH_ENV, "pack": pack, "pack_name": c.get("packName") or PACK_NAMES.get(pack, pack),
                       "group": c.get("capabilityCode") or "", "source_ref": c["operationId"],
                       "risk_level": c.get("riskLevel") or ""}}


def _content(archive: bytes) -> str:
    """技能包内容指纹（只看文件路径和内容，不看 zip 时间戳）。"""
    import io
    import zipfile
    h = hashlib.sha256()
    with zipfile.ZipFile(io.BytesIO(archive or b"")) as z:
        for n in sorted(z.namelist()):
            h.update(n.encode() + b"\0" + z.read(n) + b"\0")
    return h.hexdigest()


def skill_tool_name(skill: str) -> str:
    return _slug(skill)


# ---------------- 删除示例数据 ----------------
def _tool_row(t: Tool) -> dict:
    return {"name": t.name, "type": t.type, "display_name": t.display_name, "description": t.description,
            "params": t.params, "source": t.source, "server": t.server, "config": t.config, "owner": t.owner,
            "enabled": t.enabled, "position": t.position}


def purge_examples(db: Session, by: str = "西之月接入") -> dict:
    """删除所有外部工具、外部 MCP 服务（含子工具）、技能包，并移除意图对它们的关联；平台内置工具与内置 MCP 服务保留。
    返回备份 + 结果（备份里包含被删的行和被改的意图关联，便于回滚）。"""
    ext_tools = list(db.scalars(select(Tool).where(Tool.source != "builtin").order_by(Tool.position, Tool.name)))
    ext_servers = list(db.scalars(select(McpServer).where(McpServer.source != "builtin")))
    gone = {t.name for t in ext_tools}
    for sv in ext_servers:  # 外部服务下的子工具也会随服务删除
        gone |= {t.name for t in db.scalars(select(Tool).where(Tool.server == sv.name))}
    pkgs = list(db.scalars(select(SkillPackage).where((SkillPackage.tool_name.in_(gone)) | (SkillPackage.tool_name.is_(None)))))
    backup = {"at": utcnow().isoformat(), "tools": [_tool_row(t) for t in db.scalars(select(Tool).where(Tool.name.in_(gone)))],
              "mcp_servers": [{c.key: getattr(sv, c.key) for c in McpServer.__table__.columns
                               if c.key not in ("secret_encrypted",)} for sv in ext_servers],
              "skill_packages": [{"id": p.id, "tool_name": p.tool_name, "version": p.version, "name": p.name,
                                  "sha256": p.sha256, "archive_b64": base64.b64encode(p.archive or b"").decode()} for p in pkgs],
              "intent_bindings": []}
    touched: list[Intent] = []
    for it in db.scalars(select(Intent).order_by(Intent.space_id, Intent.position)):
        drop = [b for b in it.tools or [] if b.get("tool") in gone]
        if not drop:
            continue
        backup["intent_bindings"].append({"space": it.space_id, "code": it.code, "status": it.status, "tools": it.tools})
        it.tools = [b for b in it.tools if b.get("tool") not in gone]
        intent_svc.add_log(it, by, "移除示例工具关联：" + "、".join(b["tool"] for b in drop))
        it.updated_at = utcnow()
        touched.append(it)
    db.flush()
    for p in pkgs:
        db.delete(p)
    for t in ext_tools:
        db.delete(t)
    for sv in ext_servers:
        db.delete(sv)
    db.flush()
    downgraded = intent_svc.recheck_enabled(db, touched, by, "示例工具已删除")
    space_svc.bump_versions(db, {it.space_id for it in touched})
    return {"backup": backup, "deleted_tools": sorted(gone), "deleted_servers": [s.name for s in ext_servers],
            "deleted_skill_packages": len(pkgs), "intents_changed": [it.code for it in touched], "downgraded": downgraded}


# ---------------- 导入 ----------------
def import_westmonth(db: Session, catalog: dict, skills: dict[str, bytes], by: str = "西之月接入",
                     prune: bool = True, commit_every: int | None = None) -> dict:
    """幂等：按快照新建 / 更新西之月工具；快照里已不存在、且未被意图使用的旧西之月工具会删除（prune）。
    commit_every：每写入这么多条就提交一次（远程数据库时避免单个超大事务；中断后重跑会从断点继续）。"""
    if commit_every:
        db.expire_on_commit = False  # 分批提交后不逐行重新加载
    ver = catalog.get("cli_version") or ""
    names = tool_names(catalog["commands"])
    want: dict[str, dict] = {}
    for c in catalog["commands"]:
        p = cli_payload(c, names[c["operationId"]], ver)
        v = tool_svc.validate_payload(p, True)  # 走与控制台相同的校验
        want[v["name"]] = v
    existing = {t.name: t for t in db.scalars(select(Tool).where(Tool.owner == OWNER))}
    taken = set(db.scalars(select(Tool.name)))  # 一次取全部工具名，避免逐个查询
    pos = db.scalar(select(func.max(Tool.position)).where(Tool.source == "external")) or 0
    created = updated = 0
    # 技能
    skill_names = []
    for i, sk in enumerate(sorted(skills, key=lambda s: (SKILL_ORDER.index(s) if s in SKILL_ORDER else 99, s))):
        info = skill_svc.parse_upload([(sk + ".zip", skills[sk])])
        name = skill_tool_name(info["name"])
        skill_names.append(name)
        t = existing.get(name) or (db.get(Tool, name) if name in taken else None)
        if t is not None and t.type != "skill":
            raise SystemExit(f"工具名 {name} 已被非技能工具占用")
        cur = skill_svc.current(db, t) if t else None
        if t is None:
            pos += 1
            t = Tool(name=name, type="skill", display_name=info["name"], description=info["description"], params=[],
                     source="external", maintenance=False, space_scoped=False, endpoints=[], mcp_tools=[],
                     config={}, owner=OWNER, position=pos)
            db.add(t)
            db.flush()
            created += 1
        elif cur and cur.skill_md == info["skill_md"] and cur.files == info["files"] and _content(cur.archive) == _content(info["archive"]):
            continue
        else:
            updated += 1
        pkg = SkillPackage(tool_name=None, version=0, created_by=by, **info)
        db.add(pkg)
        db.flush()
        pkg = skill_svc.attach(db, name, pkg.id, int((t.config or {}).get("version") or 0))
        t.config = skill_svc.config_of(pkg)
        t.description = pkg.description
        t.updated_at = utcnow()
    if commit_every:
        db.commit()
    # CLI 命令
    pending = 0
    for name, v in want.items():
        t = existing.get(name)
        if t is None:
            if name in taken:
                raise SystemExit(f"工具名 {name} 已存在且不属于 {OWNER}")
            pos += 1
            v = {**v, "business_group": str((v.get("config") or {}).get("group") or "").strip() or None}
            db.add(Tool(source="external", maintenance=False, space_scoped=False, endpoints=[], mcp_tools=[],
                        position=pos, **v))
            created += 1
            pending += 1
        elif any(getattr(t, k) != v[k] for k in ("display_name", "description", "params", "config")) or (
                (t.business_group or "") != str((v.get("config") or {}).get("group") or "")):
            for k in ("display_name", "description", "params", "config"):
                setattr(t, k, v[k])
            t.business_group = str((v.get("config") or {}).get("group") or "").strip() or None
            t.updated_at = utcnow()
            updated += 1
            pending += 1
        if pending and pending % (commit_every or 1000) == 0:
            db.flush()
            if commit_every:
                db.commit()
    db.flush()
    removed, kept = [], []
    if prune:
        keep = set(want) | set(skill_names)
        usage = intent_svc.tool_usage(db)
        for name, t in existing.items():
            if name in keep:
                continue
            if usage.get(name):
                kept.append(name)
            else:
                db.delete(t)
                removed.append(name)
    db.flush()
    return {"created": created, "updated": updated, "removed": removed, "kept_in_use": kept,
            "cli_commands": len(want), "skills": skill_names, "cli_version": ver,
            "release_version": catalog.get("release_version")}


def status(db: Session) -> dict:
    rows = db.execute(select(Tool.source, Tool.type, Tool.owner, func.count()).group_by(Tool.source, Tool.type, Tool.owner)).all()
    return {"tools": [{"source": s, "type": t, "owner": o, "count": n} for s, t, o, n in rows],
            "mcp_servers": [{"name": n, "source": s} for n, s in db.execute(select(McpServer.name, McpServer.source))],
            "skill_packages": db.scalar(select(func.count()).select_from(SkillPackage))}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="西之月 xclaw-cli 工具接入")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("refresh-fixture")
    r.add_argument("--catalog", required=True, help="xclaw-cli catalog index --json 的输出文件")
    r.add_argument("--skills-dir", required=True, help="官方 Skills zip 所在目录（文件名 = skill 名.zip）")
    r.add_argument("--cli-version", required=True)
    p = sub.add_parser("purge-examples")
    p.add_argument("--backup", required=True)
    p.add_argument("--yes", action="store_true")
    i = sub.add_parser("import")
    i.add_argument("--no-prune", action="store_true")
    i.add_argument("--batch", type=int, default=300, help="每多少条提交一次（默认 300）")
    sub.add_parser("status")
    a = ap.parse_args(argv)
    if a.cmd == "refresh-fixture":
        cat = trim_catalog(json.loads(Path(a.catalog).read_text(encoding="utf-8")), a.cli_version)
        FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
        (FIXTURE_DIR / "catalog.json").write_text(json.dumps(cat, ensure_ascii=False, separators=(",", ":")) + "\n",
                                                  encoding="utf-8")
        sd = FIXTURE_DIR / "skills"
        sd.mkdir(exist_ok=True)
        for z in Path(a.skills_dir).glob("*.zip"):
            (sd / z.name).write_bytes(z.read_bytes())
        print(f"快照已更新：{len(cat['commands'])} 条命令，Skills {len(list(sd.glob('*.zip')))} 个")
        return 0
    from .db import SessionLocal
    with SessionLocal() as db:
        if a.cmd == "status":
            print(json.dumps(status(db), ensure_ascii=False, indent=1))
            return 0
        if a.cmd == "purge-examples":
            if not a.yes:
                print("会删除所有外部工具 / 外部 MCP 服务 / 技能包，并移除意图里的关联；确认请加 --yes", file=sys.stderr)
                return 2
            res = purge_examples(db)
            Path(a.backup).write_text(json.dumps(res["backup"], ensure_ascii=False, default=str, indent=1), encoding="utf-8")
            db.commit()
            res.pop("backup")
            print(json.dumps(res, ensure_ascii=False, indent=1))
            return 0
        cat, skills = load_fixture()
        res = import_westmonth(db, cat, skills, prune=not a.no_prune, commit_every=a.batch)
        db.commit()
        print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
