"""工具中心：外部工具（http / mcp / cli / skill / workflow）登记与平台内置工具。平台只登记，执行在 Bot。"""
import json
import re
import shlex

from sqlalchemy import func, select
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
         "params": [_param_out(p) for p in t.params], "source": t.source}
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


def list_tools(db: Session, source: str | None = None) -> list[Tool]:
    stmt = select(Tool)
    if source:
        stmt = stmt.where(Tool.source == source)
    return list(db.scalars(stmt.order_by(Tool.position, Tool.name)))


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
        cfg.update({k: str(cfg_in[k]).strip()[:200] for k in CLI_META if str(cfg_in.get(k) or "").strip()})
    elif t == "skill":
        params = []
        cfg = {"package_id": cfg_in.get("package_id")}
    elif t == "workflow":
        cfg = wf_svc.validate_config(cfg_in, errs)
    if errs:
        raise unprocessable("工具校验不通过", details={"errors": errs})
    return {"name": name, "type": t, "display_name": data["display_name"].strip(),
            "description": str(data.get("description") or "").strip(), "owner": data["owner"].strip(), "params": params,
            "config": cfg}


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
        raise unprocessable("MCP 子工具的说明与入参来自同步，这里只能启停、设置写操作确认（PATCH）", "MCP_TOOL_SYNCED")
    if data.get("name") and data["name"] != name:
        raise unprocessable("工具名注册后不可修改")
    v = validate_payload({**data, "name": name}, False, t)
    before = admin_dict(t)
    old_req = {p["name"] for p in t.params if p.get("required")}
    for k in ("display_name", "owner"):
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


def preview(t: Tool, values: dict, space_id: str | None = None) -> dict:
    """测试调用：校验必填并生成将要发出的请求（平台不执行，执行在 Bot）。"""
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
