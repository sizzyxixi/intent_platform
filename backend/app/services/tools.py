"""工具中心：外部工具（http / mcp / cli / skill / workflow）登记与平台内置工具。平台只登记，执行在 Bot。"""
import json
import re
import shlex

from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import selectinload
from sqlalchemy.orm import Session

from .. import audit
from ..errors import conflict, forbidden, not_found, unprocessable
from ..models import Tool
from ..security import encrypt_secret
from . import intents as intent_svc
from . import skills as skill_svc
from . import workflows as wf_svc
from . import spaces as space_svc
from .common import TOOL_NAME_RE, Actor, iso, utcnow

TYPE_LABEL = {"http": "API 接口", "mcp": "MCP 工具", "cli": "CLI 命令", "skill": "技能", "workflow": "工作流"}
HTTP_AUTH = ("无", "API Key", "Bearer Token", "OAuth2")
CLI_AUTH = ("无", "CLI 内置登录（OAuth）", "环境变量 Token")
# CLI 命令的来源 / 分组信息（按官方命令目录批量接入时写入，控制台编辑时原样保留）：
# pack=解决方案包编码 pack_name=包名 group=业务分组 source_ref=来源命令 ID risk_level=来源标注的风险等级
CLI_META = ("pack", "pack_name", "group", "source_ref", "risk_level")
CLI_KEYS = ("bin", "command_template", "auth", "auth_env", "login_command", "install", "verified_version",
            "output_format", "confirm_required", "output_description") + CLI_META
ENV_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
PARAM_TYPES = ("string", "number", "boolean", "file", "file_list", "string[]")
PARAM_TYPE_LABEL = {"file": "文件", "file_list": "文件列表"}
PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
GROUP_MAX = 200


def parse_group(value, *, existing: str | None = None, specified: bool = True, errs: list | None = None) -> str | None:
    """规范化业务分组。specified=False 时保留 existing；空字符串表示清除。"""
    if not specified:
        return existing
    s = str(value or "").strip()
    if len(s) > GROUP_MAX:
        if errs is not None:
            errs.append({"field": "group", "message": f"业务分组最多 {GROUP_MAX} 字"})
            return existing
        raise unprocessable(f"业务分组最多 {GROUP_MAX} 字")
    return s or None


def tool_group(t: Tool) -> str | None:
    """工具的业务分组：优先列字段，兼容尚未迁移的 CLI config.group。"""
    return (t.business_group or "").strip() or ((t.config or {}).get("group") or "").strip() or None


def param_schema(p: dict) -> dict:
    t = p.get("type") or "string"
    if t == "file":
        sch = {"type": "string", "format": "uri", "x-type": "file"}
    elif t == "file_list":
        sch = {"type": "array", "items": {"type": "string", "format": "uri"}, "x-type": "file_list"}
    elif t == "string[]":
        sch = {"type": "array", "items": {"type": "string"}}
    else:
        sch = {"type": t}
    if p.get("description"):
        sch["description"] = p["description"]
    if p.get("default") not in (None, ""):
        sch["default"] = p["default"]
    return sch


def input_schema(t: Tool) -> dict:
    if t.type == "mcp" and t.config.get("input_schema"):
        return t.config["input_schema"]
    props = {p["name"]: param_schema(p) for p in t.params}
    return {"type": "object", "properties": props, "required": [p["name"] for p in t.params if p.get("required")]}


def _param_out(p: dict) -> dict:
    o = {"name": p["name"], "type": p["type"], "required": bool(p.get("required"))}
    if p.get("description"):
        o["description"] = p["description"]
    if p.get("default") not in (None, ""):
        o["default"] = p["default"]
    return o


def skill_download_path(name: str) -> str:
    return f"/api/v1/tools/{name}/skill-package"


def api_dict(t: Tool) -> dict:
    c = t.config or {}
    d = {"name": t.name, "display_name": t.display_name, "type": t.type, "description": t.description,
         "params": [_param_out(p) for p in t.params], "source": t.source, "enabled": t.enabled,
         "group": tool_group(t)}
    if t.type != "skill":
        d["input_schema"] = input_schema(t)
    if t.type == "http":
        d.update({"method": c.get("method", "GET"), "url": c.get("url", ""), "auth": c.get("auth", "无")})
    elif t.type == "mcp":
        from .mcp_servers import conn_dict
        d.update({"server": t.server, "mcp_server": conn_dict(t.server_ref) if t.server_ref else {},
                  "mcp_tool": c.get("mcp_tool", ""), "confirm_required": bool(c.get("confirm_required")),
                  "enabled": t.enabled, "remote_missing": t.remote_missing})
    elif t.type == "cli":
        d["cli"] = {k: c.get(k) for k in CLI_KEYS if k not in CLI_META and k != "auth_env"}
        d["cli"].update({k: c[k] for k in ("auth_env",) + CLI_META if c.get(k)})
        d["confirm_required"] = bool(c.get("confirm_required"))
    elif t.type == "skill":
        d["skill"] = {"name": c.get("skill_name") or t.name, "version": int(c.get("version") or 0),
                      "description": t.description, "files": list(c.get("files") or []),
                      "download_path": skill_download_path(t.name) if c.get("package_id") else None}
    elif t.type == "workflow":
        d["workflow"] = wf_svc.export(c)
        d["confirm_required"] = bool(c.get("confirm_required"))
    if t.source == "builtin":
        d.update({"maintenance": t.maintenance, "space_scoped": t.space_scoped, "endpoints": list(t.endpoints or []),
                  "mcp_tools": list(t.mcp_tools or [])})
    return d


def admin_dict(t: Tool, users: list | None = None, spaces: dict | None = None) -> dict:
    d = api_dict(t)
    d["type_label"] = TYPE_LABEL[t.type]
    d["owner"] = t.owner
    d["updated_at"] = iso(t.updated_at)
    d["maintenance"] = t.maintenance
    d["has_secret"] = bool(t.secret_encrypted)
    c = t.config or {}
    if t.type in ("http", "mcp"):
        d["output_example"] = c.get("output_example", "")
    if t.type in ("skill", "workflow"):
        d["config"] = dict(c)
    if users is not None:
        d["used_by"] = [{"code": u.code, "name": u.name, "space": u.space_id,
                         "space_name": (spaces or {}).get(u.space_id, u.space_id), "status": u.status} for u in users]
    return d


LIST_BATCH = 0  # 0 = 取配置 tool_list_batch（测试可直接改这个值）


def list_tools(db: Session, source: str | None = None) -> list[Tool]:
    """全部工具（只读，返回的对象已脱离会话）。按工具名分批读取，远程数据库上避免单次传输过大被断开；断开时重试该批。"""
    from ..config import get_settings
    batch = LIST_BATCH or get_settings().tool_list_batch
    out: list[Tool] = []
    last = ""
    while True:
        stmt = select(Tool).where(Tool.name > last)
        if source:
            stmt = stmt.where(Tool.source == source)
        for attempt in range(6):  # 远程数据库连接中途断开时，回滚换新连接重试这一批
            try:
                part = list(db.scalars(stmt.order_by(Tool.name).limit(batch).options(selectinload(Tool.server_ref))))
                for t in part:  # 只读结果脱离会话，后续某批出错回滚时不会被作废重载
                    db.expunge(t)
                break
            except OperationalError:
                db.rollback()
                if attempt == 5:
                    raise
        out += part
        if len(part) < batch:
            break
        last = part[-1].name
    out.sort(key=lambda t: (t.position, t.name))
    return out


def _escape_like(q: str) -> str:
    return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def apply_filters(stmt, *, source: str | None = None, typ: str | None = None, q: str | None = None,
                  pack: str | None = None, group: str | None = None, server: str | None = None,
                  status: str | None = None, maintenance: bool | None = None,
                  exclude_type: str | None = None, names: list[str] | None = None):
    """工具列表的服务端筛选（分页与按名取用共用）。"""
    if names:
        stmt = stmt.where(Tool.name.in_(names))
    if source:
        stmt = stmt.where(Tool.source == source)
    if typ:
        stmt = stmt.where(Tool.type == typ)
    if exclude_type:
        types = [x.strip() for x in exclude_type.split(",") if x.strip()]
        if types:
            stmt = stmt.where(Tool.type.notin_(types))
    if pack:
        stmt = stmt.where(Tool.config["pack"].astext == pack)
    if group:
        stmt = stmt.where(or_(Tool.business_group == group, Tool.config["group"].astext == group))
    if server:
        stmt = stmt.where(Tool.server == server)
    if maintenance is not None:
        stmt = stmt.where(Tool.maintenance == maintenance)
    if status in ("启用", "enabled"):
        stmt = stmt.where(Tool.enabled.is_(True), Tool.remote_missing.is_(False))
    elif status in ("停用", "disabled"):
        stmt = stmt.where(or_(Tool.enabled.is_(False), Tool.remote_missing.is_(True)))
    if q and q.strip():
        pat = "%" + _escape_like(q.strip()) + "%"
        stmt = stmt.where(or_(
            Tool.name.ilike(pat, escape="\\"),
            Tool.display_name.ilike(pat, escape="\\"),
            Tool.description.ilike(pat, escape="\\"),
            Tool.config["command_template"].astext.ilike(pat, escape="\\"),
            Tool.config["group"].astext.ilike(pat, escape="\\"),
            Tool.business_group.ilike(pat, escape="\\"),
        ))
    return stmt


def list_page(db: Session, page: int = 1, page_size: int = 20, **filters) -> tuple[int, list[Tool]]:
    """分页列出工具。filters 见 apply_filters。"""
    stmt = apply_filters(select(Tool), **filters)
    total = db.scalar(select(func.count()).select_from(stmt.with_only_columns(Tool.name).order_by(None).subquery())) or 0
    rows = list(db.scalars(stmt.order_by(Tool.position, Tool.name)
                           .offset((page - 1) * page_size).limit(page_size)
                           .options(selectinload(Tool.server_ref))))
    return int(total), rows


def list_facets(db: Session) -> dict:
    """工具目录筛选项计数：非 MCP 工具按类型/来源，MCP 以服务为单位计入，外加 CLI 命令包与分组。"""
    from ..models import McpServer
    by_type: dict[str, int] = {}
    by_source: dict[str, int] = {"external": 0, "builtin": 0}
    tool_n = 0
    for typ, src, n in db.execute(
            select(Tool.type, Tool.source, func.count()).where(Tool.type != "mcp").group_by(Tool.type, Tool.source)):
        by_type[typ] = by_type.get(typ, 0) + n
        by_source[src] = by_source.get(src, 0) + n
        tool_n += n
    mcp_n = 0
    for src, n in db.execute(select(McpServer.source, func.count()).group_by(McpServer.source)):
        by_source[src] = by_source.get(src, 0) + n
        mcp_n += n
    packs, groups = [], []
    pack_col, name_col = Tool.config["pack"].astext, Tool.config["pack_name"].astext
    grp_col = func.nullif(func.coalesce(Tool.business_group, Tool.config["group"].astext), "")
    for pack, pack_name, n in db.execute(
            select(pack_col, name_col, func.count())
            .where(Tool.type == "cli", pack_col.isnot(None), pack_col != "")
            .group_by(pack_col, name_col).order_by(pack_col)):
        packs.append({"pack": pack, "pack_name": pack_name or pack, "count": n})
    # 全类型业务分组；packs 记录该分组在哪些 CLI 命令包中出现，便于按命令包缩小筛选
    by_g: dict[str, dict] = {}
    for g, pack, n in db.execute(
            select(grp_col, pack_col, func.count())
            .where(grp_col.isnot(None))
            .group_by(grp_col, pack_col).order_by(grp_col, pack_col)):
        rec = by_g.setdefault(g, {"group": g, "count": 0, "packs": []})
        rec["count"] += n
        if pack:
            rec["packs"].append(pack)
    groups = list(by_g.values())
    builtin_bindable = db.scalar(select(func.count()).select_from(Tool).where(
        Tool.source == "builtin", Tool.maintenance.is_(False), Tool.type != "mcp")) or 0
    return {"total": tool_n + mcp_n, "tool_count": tool_n, "mcp_servers": mcp_n,
            "by_type": {**by_type, "mcp": mcp_n}, "by_source": by_source, "packs": packs, "groups": groups,
            "builtin_bindable": int(builtin_bindable)}


def fingerprint(db: Session) -> tuple:
    """工具表 + MCP 服务表的变化指纹（行数 + 最大事务号），用于列表缓存失效。"""
    return tuple(db.execute(text(
        "SELECT (SELECT count(*) FROM tools), (SELECT coalesce(max(xmin::text::bigint), 0) FROM tools),"
        " (SELECT count(*) FROM mcp_servers), (SELECT coalesce(max(xmin::text::bigint), 0) FROM mcp_servers)")).one())


def get(db: Session, name: str) -> Tool:
    t = db.get(Tool, name)
    if not t:
        raise not_found(f"工具 {name} 不存在", "TOOL_NOT_FOUND")
    return t


def _params(raw) -> tuple[list[dict], list[dict]]:
    errs, out, seen = [], [], set()
    for i, p in enumerate(raw or []):
        n = str(p.get("name") or "").strip()
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", n):
            errs.append({"field": f"params[{i}].name", "message": "参数名为英文/数字/下划线"})
            continue
        if n in seen:
            errs.append({"field": f"params[{i}].name", "message": f"参数名 {n} 重复"})
        seen.add(n)
        typ = p.get("type") or "string"
        if typ not in PARAM_TYPES:
            errs.append({"field": f"params[{i}].type", "message": "参数类型为 string / number / boolean / file / file_list / string[]"})
        o = {"name": n, "type": typ, "required": bool(p.get("required")),
             "description": str(p.get("description") or "").strip()}
        dv = p.get("default")
        if dv not in (None, ""):
            o["default"] = str(dv)
        out.append(o)
    return out, errs


def schema_params(schema: dict) -> list[dict]:
    req = set(schema.get("required") or [])
    out = []
    for n, v in (schema.get("properties") or {}).items():
        typ = (v or {}).get("type", "string")
        typ = "number" if typ in ("number", "integer") else ("boolean" if typ == "boolean" else "string")
        out.append({"name": n, "type": typ, "required": n in req, "description": (v or {}).get("description", "")})
    return out


def validate_payload(data: dict, creating: bool, existing: Tool | None = None) -> dict:
    errs: list[dict] = []
    t = data.get("type") or (existing.type if existing else "")
    if t not in TYPE_LABEL:
        errs.append({"field": "type", "message": "工具类型应为 http / mcp / cli / skill / workflow"})
    if existing and data.get("type") and data["type"] != existing.type:
        errs.append({"field": "type", "message": "工具类型注册后不可修改"})
    name = (data.get("name") or (existing.name if existing else "")).strip()
    if creating and (not TOOL_NAME_RE.match(name) or len(name) > 120):
        errs.append({"field": "name", "message": "工具名为小写英文/数字/下划线，以字母开头"})
    for f, label in (("display_name", "显示名"), ("description", "描述"), ("owner", "负责人")):
        if f == "description" and t == "skill":
            continue  # 技能描述来自 SKILL.md，只读
        if not str(data.get(f) or "").strip():
            errs.append({"field": f, "message": f"{label}必填"})
    cfg_in = data.get("config") or {}
    cfg: dict = {}
    params, perr = _params(data.get("params"))
    errs += perr
    group_specified = "group" in data or "group" in cfg_in
    group_raw = data["group"] if "group" in data else cfg_in.get("group")
    group = parse_group(group_raw, existing=(existing.business_group if existing else None),
                        specified=group_specified, errs=errs)
    if t == "http":
        url = str(cfg_in.get("url") or "").strip()
        if not re.match(r"^https?://\S+$", url):
            errs.append({"field": "config.url", "message": "接口地址必须是 http(s) 地址"})
        m = cfg_in.get("method") or "GET"
        if m not in ("GET", "POST", "PUT", "DELETE"):
            errs.append({"field": "config.method", "message": "方法为 GET / POST / PUT / DELETE"})
        a = cfg_in.get("auth") or "无"
        if a not in HTTP_AUTH:
            errs.append({"field": "config.auth", "message": "鉴权方式不支持"})
        oe = str(cfg_in.get("output_example") or "").strip()
        if oe:
            try:
                json.loads(oe)
            except ValueError:
                errs.append({"field": "config.output_example", "message": "出参示例必须是合法 JSON"})
        cfg = {"url": url, "method": m, "auth": a, "output_example": oe}
    elif t == "mcp":
        errs.append({"field": "type", "message": "MCP 请以服务登记（POST /mcp-servers）后同步子工具，不能单独注册 MCP 工具"})
    elif t == "cli":
        b = str(cfg_in.get("bin") or "").strip()
        tpl = str(cfg_in.get("command_template") or "").strip()
        if not b:
            errs.append({"field": "config.bin", "message": "可执行程序必填"})
        if not tpl:
            errs.append({"field": "config.command_template", "message": "命令模板必填"})
        elif b and not (tpl == b or tpl.startswith(b + " ")):
            errs.append({"field": "config.command_template", "message": f"命令模板须以可执行程序 {b} 开头"})
        refs = set(PLACEHOLDER.findall(tpl))
        defined = {p["name"] for p in params}
        for r in sorted(refs - defined):
            errs.append({"field": "config.command_template", "message": f"模板引用的参数 {{{r}}} 未在入参中定义"})
        for p in sorted(defined - refs):
            errs.append({"field": "params", "message": f"入参 {p} 没有出现在命令模板中"})
        a = cfg_in.get("auth") or "无"
        if a not in CLI_AUTH:
            errs.append({"field": "config.auth", "message": "鉴权方式不支持"})
        ae = str(cfg_in.get("auth_env") or "").strip()
        if ae and not ENV_RE.match(ae):
            errs.append({"field": "config.auth_env", "message": "凭据环境变量名为大写英文/数字/下划线"})
        of = cfg_in.get("output_format") or "json"
        if of not in ("json", "text"):
            errs.append({"field": "config.output_format", "message": "输出格式为 json / text"})
        cfg = {"bin": b, "command_template": tpl, "auth": a,
               "login_command": str(cfg_in.get("login_command") or "").strip(),
               "install": str(cfg_in.get("install") or "").strip(),
               "verified_version": str(cfg_in.get("verified_version") or "").strip(),
               "output_format": of, "confirm_required": bool(cfg_in.get("confirm_required")),
               "output_description": str(cfg_in.get("output_description") or "").strip(), "auth_env": ae}
        cfg.update({k: str(cfg_in[k]).strip()[:200] for k in CLI_META if k != "group" and str(cfg_in.get(k) or "").strip()})
        if group:
            cfg["group"] = group
        else:
            cfg.pop("group", None)
    elif t == "skill":
        params = []
        cfg = {"package_id": cfg_in.get("package_id")}
    elif t == "workflow":
        cfg = wf_svc.validate_config(cfg_in, errs)
    if errs:
        raise unprocessable("工具校验不通过", details={"errors": errs})
    return {"name": name, "type": t, "display_name": data["display_name"].strip(),
            "description": str(data.get("description") or "").strip(), "owner": data["owner"].strip(), "params": params,
            "config": cfg, "business_group": group}


def _apply_skill(db: Session, t: Tool, package_id, creating: bool) -> None:
    """技能：有新包就挂上成为新版本，描述取 SKILL.md；没传新包则保持当前版本。"""
    cur = int((t.config or {}).get("version") or 0)
    if package_id in (None, "") and not creating and (t.config or {}).get("package_id"):
        return
    pkg = skill_svc.attach(db, t.name, package_id, cur)
    t.config = skill_svc.config_of(pkg)
    t.description = pkg.description
    t.params = []


def create(db: Session, data: dict, actor: Actor) -> Tool:
    v = validate_payload(data, True)
    if db.get(Tool, v["name"]):
        raise conflict(f"工具名 {v['name']} 已存在", "TOOL_EXISTS")
    pos = (db.scalar(select(func.max(Tool.position)).where(Tool.source == "external")) or 0) + 1
    t = Tool(source="external", maintenance=False, space_scoped=False, endpoints=[], mcp_tools=[], position=pos, **v)
    if data.get("secret") and t.type == "http":
        t.secret_encrypted = encrypt_secret(str(data["secret"]))
    db.add(t)
    db.flush()
    if t.type == "skill":
        _apply_skill(db, t, v["config"].get("package_id"), True)
    audit.record(db, actor, "tool.create", "tool", t.name, f"注册工具 {t.display_name}（{t.name}）", None,
                 {"type": t.type})
    return t


def update(db: Session, name: str, data: dict, actor: Actor) -> Tool:
    t = get(db, name)
    if t.source == "builtin":
        raise forbidden("平台内置工具由平台提供，不可编辑", "BUILTIN_READONLY")
    if t.type == "mcp":
        # 子工具的说明与入参来自同步，不能整单编辑；允许单独改业务分组
        if "group" in data and set(data.keys()) <= {"name", "group", "dry_run"}:
            from . import mcp_servers as mcp_svc
            return mcp_svc.patch_tool(db, t, data, actor)
        raise unprocessable("MCP 子工具的说明与入参来自同步，这里只能修改业务分组；启停与写操作确认请用 PATCH", "MCP_TOOL_SYNCED")
    if data.get("name") and data["name"] != name:
        raise unprocessable("工具名注册后不可修改")
    v = validate_payload({**data, "name": name}, False, t)
    before = admin_dict(t)
    old_req = {p["name"] for p in t.params if p.get("required")}
    for k in ("display_name", "owner", "business_group"):
        setattr(t, k, v[k])
    if t.type == "skill":
        _apply_skill(db, t, v["config"].get("package_id"), False)
    else:
        for k in ("description", "params", "config"):
            setattr(t, k, v[k])
    if data.get("secret") and t.type == "http":
        t.secret_encrypted = encrypt_secret(str(data["secret"]))
    if data.get("clear_secret"):
        t.secret_encrypted = None
    t.updated_at = utcnow()
    after = admin_dict(t)
    if before == after and not data.get("secret"):
        return t
    users = intent_svc.users_of_tool(db, name)
    new_req = [p["name"] for p in t.params if p.get("required") and p["name"] not in old_req]
    for u in users:
        msg = f"工具 {name} 已修改"
        if new_req:
            missing = [p for p in new_req if p not in next((b.get("param_map") or {} for b in u.tools if b["tool"] == name), {})]
            if missing:
                msg += f"，新增必填入参 {'、'.join(missing)}，请补充映射"
        intent_svc.add_log(u, actor.name, msg)
    space_svc.bump_versions(db, {u.space_id for u in users if u.status == "启用"})
    audit.record(db, actor, "tool.update", "tool", name, f"编辑工具 {t.display_name}", None,
                 {"new_required": new_req, "used_by": [u.code for u in users]})
    return t


def delete(db: Session, name: str, actor: Actor) -> None:
    t = get(db, name)
    if t.source == "builtin":
        raise forbidden("平台内置工具不可删除", "BUILTIN_READONLY")
    if t.type == "mcp":
        raise unprocessable("MCP 子工具随服务同步维护，不需要的请停用；删除整个服务请在服务上操作", "MCP_TOOL_SYNCED")
    users = intent_svc.users_of_tool(db, name)
    if users:
        raise conflict(f"工具 {name} 正被 {len(users)} 个意图使用，请先在意图中移除", "TOOL_IN_USE",
                       used_by=[u.code for u in users])
    db.delete(t)
    audit.record(db, actor, "tool.delete", "tool", name, f"删除工具 {t.display_name}", None)


def upsert(db: Session, data: dict, actor: Actor) -> tuple[Tool, str]:
    """按工具名（稳定编码）创建或更新。MCP 子工具不能走这条路径。"""
    name = str(data.get("name") or "").strip()
    existing = db.get(Tool, name) if name else None
    if existing:
        return update(db, name, data, actor), "updated"
    return create(db, data, actor), "created"


def set_enabled(db: Session, name: str, enabled: bool, actor: Actor) -> Tool:
    """停用或重新启用外部工具（开放接口只提供停用；内置工具不可改）。"""
    t = get(db, name)
    if t.source == "builtin":
        raise forbidden("平台内置工具不可启停", "BUILTIN_READONLY")
    if t.enabled == bool(enabled):
        return t
    t.enabled = bool(enabled)
    t.updated_at = utcnow()
    db.flush()
    users = intent_svc.users_of_tool(db, name)
    verb = "启用" if t.enabled else "停用"
    for u in users:
        intent_svc.add_log(u, actor.name, f"工具 {name} 已{verb}")
    space_svc.bump_versions(db, {u.space_id for u in users if u.status == "启用"})
    downgraded = []
    if not t.enabled:
        downgraded = intent_svc.recheck_enabled(db, users, actor.name, f"工具 {name} 已停用")
    audit.record(db, actor, "tool.enable" if t.enabled else "tool.disable", "tool", name,
                 f"{verb}工具 {t.display_name}", None,
                 {"enabled": t.enabled, "used_by": [u.code for u in users], "downgraded": downgraded})
    return t


# ---------------- CLI 命令渲染（Bot 执行规则的参考实现） ----------------
def render_cli(template: str, values: dict) -> list[str]:
    """把 {参数} 替换为值；选填参数为空时连同前面的 --选项 一起省略；返回参数数组（不拼 shell）。"""
    toks = shlex.split(template)
    out: list[str] = []
    i = 0

    def filled(tok: str) -> str | None:
        names = PLACEHOLDER.findall(tok)
        if any(values.get(n) in (None, "") for n in names):
            return None
        return PLACEHOLDER.sub(lambda m: str(values[m.group(1)]), tok)

    while i < len(toks):
        tok = toks[i]
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        if tok.startswith("-") and not PLACEHOLDER.search(tok) and nxt is not None and PLACEHOLDER.search(nxt) \
                and not nxt.startswith("-"):
            v = filled(nxt)
            if v is not None:
                out += [tok, v]
            i += 2
            continue
        if PLACEHOLDER.search(tok):
            v = filled(tok)
            if v is not None:
                out.append(v)
        else:
            out.append(tok)
        i += 1
    return out


def coerce_args(t: Tool, values: dict) -> dict:
    """测试调用的入参：补默认值、校验必填、按类型转换。"""
    values = {**{p["name"]: p["default"] for p in t.params if p.get("default") not in (None, "")},
              **{k: v for k, v in (values or {}).items() if v not in (None, "")}}
    missing = [p["name"] for p in t.params if p.get("required") and values.get(p["name"]) in (None, "")]
    if missing:
        raise unprocessable("必填入参未填写：" + "、".join(missing), "PARAM_REQUIRED", missing=missing)
    args = {}
    for p in t.params:
        v = values.get(p["name"])
        if v in (None, ""):
            continue
        if p["type"] == "number":
            try:
                v = float(v) if "." in str(v) else int(v)
            except ValueError:
                raise unprocessable(f"入参 {p['name']} 应为数字", "PARAM_TYPE")
        elif p["type"] == "boolean":
            v = str(v).lower() in ("1", "true", "yes", "是")
        elif p["type"] in ("file_list", "string[]") and isinstance(v, str):
            v = [x.strip() for x in re.split(r"[,，\n]", v) if x.strip()]
        args[p["name"]] = v
    return args


def preview(t: Tool, values: dict, space_id: str | None = None) -> dict:
    """生成将要发出的请求（不执行）。"""
    args = coerce_args(t, values)
    c = t.config or {}
    out: dict = {"type": t.type, "executed": False,
                 "note": "平台只登记工具，测试调用只生成请求预览，不会真正执行；实际执行在 Bot / 智能体侧。"}
    if t.type == "http":
        m = c.get("method", "GET")
        url = c.get("url", "")
        path_vars = PLACEHOLDER.findall(url)
        for pv in path_vars:
            url = url.replace("{" + pv + "}", str(args.pop(pv, "{" + pv + "}")))
        req = {"method": m, "url": url, "auth": c.get("auth")}
        if m == "GET":
            req["query"] = args
        else:
            req["json"] = args
        if t.source == "builtin" and t.space_scoped:
            req["headers"] = {"X-Space-Id": space_id or ""}
        out["request"] = req
        out["response_example"] = c.get("output_example") or None
    elif t.type == "mcp":
        from .mcp_servers import conn_dict
        out["request"] = {"server": conn_dict(t.server_ref) if t.server_ref else None, "jsonrpc": "2.0",
                          "method": "tools/call", "params": {"name": c.get("mcp_tool"), "arguments": args},
                          "confirm_required": bool(c.get("confirm_required"))}
        out["response_example"] = c.get("output_example") or None
    elif t.type == "cli":
        argv = render_cli(c.get("command_template", ""), {k: str(v) for k, v in args.items()})
        out["request"] = {"argv": argv, "command": "$ " + shlex.join(argv), "auth": c.get("auth"),
                          "login_command": c.get("login_command"), "install": c.get("install"),
                          "confirm_required": c.get("confirm_required"), "output_format": c.get("output_format")}
        out["response_example"] = c.get("output_description") or None
    elif t.type == "workflow":
        out["request"] = wf_svc.request_preview(c, args)
        out["response_example"] = c.get("output_description") or None
    else:
        out["request"] = {"skill": c.get("skill_name") or t.name, "version": c.get("version"),
                          "files": [f["path"] for f in c.get("files") or []],
                          "note": "技能没有入参映射：本意图抽取的槽位作为上下文交给 AI，输出格式由意图的回复要求约定。"}
        out["response_example"] = None
    return out
