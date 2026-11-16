"""MCP 服务与子工具：登记服务连接信息 → 同步（initialize + tools/list 或手动粘贴 tools/list JSON）→ 看差异 → 确认入库。

- 子工具保存在 tools 表（type=mcp，server=服务名，name={服务名}.{远端工具名}），意图只能绑定具体子工具。
- 再次同步：新增的直接加入；变更的更新说明和 inputSchema；远端消失的——没被意图用就删除，
  被意图用了就保留并标记 remote_missing（红色），已启用的意图校验不通过、自动转草稿，绝不静默删除。
"""
from __future__ import annotations

import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..config import get_settings
from ..errors import ApiError, bad_request, conflict, forbidden, not_found, unprocessable
from ..models import McpServer, Tool
from ..security import decrypt_secret, encrypt_secret
from . import intents as intent_svc
from . import spaces as space_svc
from .common import Actor, iso, utcnow
from .mcp_client import fetch_tools

TRANSPORTS = {"streamable_http": "Streamable HTTP", "sse": "SSE（旧版）", "stdio": "stdio（本地进程）"}
CRED_LOCATIONS = {"header": "请求头", "query": "URL 参数", "env": "环境变量"}
# URL 里出现这些参数视为密钥：保存时从 URL 移除，值转存为该凭据的加密密钥
SECRET_QUERY_KEYS = ("secret-key", "secret_key", "token", "api_key", "apikey", "api-key", "key", "access_token")
HEADER_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")
QUERY_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
SERVER_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,39}$")
REMOTE_NAME_RE = re.compile(r"^[A-Za-z0-9_\-.]{1,64}$")
ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")
MAX_TOOLS = 500


def tool_name(server: str, remote: str) -> str:
    return f"{server}.{remote}"


def creds(sv: McpServer) -> list[dict]:
    return [dict(c) for c in (sv.credentials or [])]


def token_env(sv: McpServer) -> str | None:
    """兼容旧字段 auth.token_env：第一条凭据的环境变量名。"""
    cs = creds(sv)
    return cs[0]["env"] if cs else None


def _secrets(sv: McpServer) -> dict[str, str]:
    """平台侧保存的密钥 {凭据名称: 值}。兼容旧格式（单个字符串 = 第一条凭据的密钥）。"""
    if not sv.secret_encrypted:
        return {}
    raw = decrypt_secret(sv.secret_encrypted) or ""
    try:
        j = json.loads(raw)
        if isinstance(j, dict):
            return {str(k): str(v) for k, v in j.items() if v}
    except ValueError:
        pass
    cs = creds(sv)
    return {cs[0]["name"]: raw} if cs and raw else {}


def strip_url_secrets(url: str, cred_list: list[dict]) -> tuple[str, dict[str, str]]:
    """去掉 URL 中的密钥参数，返回 (干净的 URL, {参数名: 值})。"""
    if not url:
        return url, {}
    parts = urlsplit(url)
    drop = {c["name"] for c in cred_list if c.get("location") == "query"} | set(SECRET_QUERY_KEYS)
    keep, taken = [], {}
    for k, v in parse_qsl(parts.query, keep_blank_values=True):
        if k in drop or k.lower() in SECRET_QUERY_KEYS:
            taken[k] = v
        else:
            keep.append((k, v))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(keep), parts.fragment)), taken


def server_tools(db: Session, name: str) -> list[Tool]:
    return list(db.scalars(select(Tool).where(Tool.server == name).order_by(Tool.position, Tool.name)))


def get(db: Session, name: str) -> McpServer:
    sv = db.get(McpServer, name)
    if not sv:
        raise not_found(f"MCP 服务 {name} 不存在", "MCP_SERVER_NOT_FOUND")
    return sv


def creds_out(sv: McpServer) -> list[dict]:
    out = []
    for c in creds(sv):
        o = {"location": c["location"], "name": c["name"], "env": c["env"]}
        if c.get("scheme"):
            o["scheme"] = c["scheme"]
        out.append(o)
    return out


def _legacy_auth(sv: McpServer) -> dict:
    cs = creds(sv)
    if not cs:
        return {"type": "无"}
    c = cs[0]
    if c["location"] == "env":
        return {"type": "环境变量", "env": c["name"], "token_env": c["env"]}
    if c.get("scheme") == "Bearer":
        return {"type": "Bearer Token", "header": c["name"], "scheme": "Bearer", "token_env": c["env"]}
    if c["location"] == "header":
        return {"type": "API Key", "header": c["name"], "token_env": c["env"]}
    return {"type": "URL 参数", "query": c["name"], "token_env": c["env"]}


def conn_dict(sv: McpServer) -> dict:
    """连接信息（不含任何凭据值）：credentials 只给位置、名称和 Bot 侧环境变量名。"""
    d = {"name": sv.name, "display_name": sv.display_name, "transport": sv.transport}
    if sv.transport == "stdio":
        d.update({"command": sv.command, "args": list(sv.args or []), "env": dict(sv.env or {})})
    else:
        d["url"] = strip_url_secrets(sv.url or "", creds(sv))[0]
    d["credentials"] = creds_out(sv)
    d["auth"] = _legacy_auth(sv)  # 兼容 v0.6 的单一鉴权字段，新接入请读 credentials
    return d


def admin_dict(db: Session, sv: McpServer, with_tools: bool = True) -> dict:
    d = conn_dict(sv)
    sec = _secrets(sv)
    d["credentials"] = [{**c, "has_secret": c["name"] in sec} for c in d["credentials"]]
    d.update({"description": sv.description, "transport_label": TRANSPORTS.get(sv.transport, sv.transport),
              "has_secret": bool(sec), "example_data": bool(sv.example_data),
              "source": sv.source, "owner": sv.owner, "group": sv.business_group or None,
              "server_info": sv.server_info or {},
              "last_sync_at": iso(sv.last_sync_at), "last_sync_by": sv.last_sync_by,
              "last_sync_source": sv.last_sync_source})
    if with_tools:
        from . import tools as tool_svc
        from .knowledge import space_names
        sp = space_names(db)
        ts = server_tools(db, sv.name)
        d["tools"] = [tool_svc.admin_dict(t, intent_svc.users_of_tool(db, t.name), sp) for t in ts]
        d["tool_count"] = len(ts)
        d["enabled_count"] = sum(1 for t in ts if t.enabled and not t.remote_missing)
        d["missing_count"] = sum(1 for t in ts if t.remote_missing)
    return d


# ---------------- 服务登记 ----------------
def _validate(data: dict, creating: bool, sv: McpServer | None = None) -> dict:
    errs = []
    name = (data.get("name") or (sv.name if sv else "")).strip()
    if creating and not SERVER_NAME_RE.match(name):
        errs.append({"field": "name", "message": "服务名为 2～40 位小写英文 / 数字 / 下划线，字母开头"})
    for f, label in (("display_name", "显示名"), ("owner", "负责人")):
        if not str(data.get(f) or "").strip():
            errs.append({"field": f, "message": f"{label}必填"})
    tr = data.get("transport") or "streamable_http"
    if tr not in TRANSPORTS:
        errs.append({"field": "transport", "message": "传输方式为 streamable_http / sse / stdio"})
    url = str(data.get("url") or "").strip() or None
    command = str(data.get("command") or "").strip() or None
    args = data.get("args") or []
    if isinstance(args, str):
        import shlex
        try:
            args = shlex.split(args)
        except ValueError:
            errs.append({"field": "args", "message": "启动参数格式不正确"})
            args = []
    env = data.get("env") or {}
    if tr in ("streamable_http", "sse"):
        if not url or not re.match(r"^https?://\S+$", url):
            errs.append({"field": "url", "message": "服务地址必须是 http(s) 地址"})
        command, args, env = None, [], {}
    elif tr == "stdio":
        if not command:
            errs.append({"field": "command", "message": "启动命令必填（如 npx、uvx、python）"})
        if not isinstance(args, list) or not all(isinstance(x, str) for x in args):
            errs.append({"field": "args", "message": "启动参数应为字符串数组"})
        if not isinstance(env, dict) or not all(ENV_NAME_RE.match(str(k)) and isinstance(v, str) for k, v in env.items()):
            errs.append({"field": "env", "message": "环境变量名为大写字母 / 数字 / 下划线，值为字符串"})
        url = None
    cred_list, secrets_in = _parse_creds(data, tr, name, errs)
    warnings = []
    if url:
        clean, taken = strip_url_secrets(url, cred_list)
        if taken:
            for k, v in taken.items():
                c = next((x for x in cred_list if x["location"] == "query" and x["name"] == k), None)
                if not c:
                    c = {"location": "query", "name": k, "env": _env_name(name, k)}
                    cred_list.append(c)
                if v and not secrets_in.get(k):
                    secrets_in[k] = v
            warnings.append("服务地址里的密钥参数 " + "、".join(taken) + " 已从 URL 移除，改为「URL 参数」凭据（Bot 侧读环境变量"
                            + "、".join(next(x["env"] for x in cred_list if x["name"] == k) for k in taken) + "）")
            url = clean
    from .tools import parse_group
    group = parse_group(data.get("group"), existing=(sv.business_group if sv else None),
                        specified=("group" in data), errs=errs)
    if errs:
        raise unprocessable("MCP 服务校验不通过", details={"errors": errs})
    return {"name": name, "display_name": data["display_name"].strip(), "description": str(data.get("description") or "").strip(),
            "transport": tr, "url": url, "command": command, "args": args, "env": env, "credentials": cred_list,
            "owner": data["owner"].strip(), "business_group": group, "_secrets": secrets_in, "_warnings": warnings}


def _env_name(server: str, cred: str) -> str:
    return re.sub(r"[^A-Z0-9_]", "_", f"MCP_{server}_{cred}".upper())[:64]


def _parse_creds(data: dict, tr: str, server: str, errs: list) -> tuple[list[dict], dict[str, str]]:
    """credentials: [{location, name, env, scheme?, secret?}]；兼容 v0.6 的 auth / auth_header / auth_env / secret。"""
    raw = data.get("credentials")
    secrets: dict[str, str] = {}
    if raw is None and data.get("auth") not in (None, "", "无"):
        a, tok = data.get("auth"), _env_name(server or "x", "token")
        if a == "Bearer Token":
            raw = [{"location": "header", "name": "Authorization", "env": tok, "scheme": "Bearer"}]
        elif a == "API Key":
            raw = [{"location": "header", "name": data.get("auth_header") or "X-API-Key", "env": tok}]
        elif a == "环境变量":
            raw = [{"location": "env", "name": data.get("auth_env") or "", "env": data.get("auth_env") or ""}]
        else:
            errs.append({"field": "auth", "message": "鉴权方式为 无 / Bearer Token / API Key / 环境变量（建议改用 credentials）"})
            raw = []
        if data.get("secret") and raw:
            raw[0]["secret"] = data["secret"]
    out, seen = [], set()
    if not isinstance(raw or [], list):
        errs.append({"field": "credentials", "message": "credentials 应为数组"})
        return [], {}
    for i, c in enumerate(raw or []):
        c = c or {}
        loc = c.get("location") or c.get("loc") or "header"
        n = str(c.get("name") or "").strip()
        e = str(c.get("env") or "").strip()
        f = f"credentials[{i}]"
        if loc not in CRED_LOCATIONS:
            errs.append({"field": f + ".location", "message": "位置为 header（请求头）/ query（URL 参数）/ env（环境变量，仅 stdio）"})
            continue
        if loc == "env" and tr != "stdio":
            errs.append({"field": f + ".location", "message": "「环境变量」位置只用于 stdio 服务"})
        if loc in ("header", "query") and tr == "stdio":
            errs.append({"field": f + ".location", "message": "stdio 服务的凭据请用「环境变量」位置"})
        rx = HEADER_RE if loc == "header" else QUERY_RE if loc == "query" else ENV_NAME_RE
        if not rx.match(n):
            errs.append({"field": f + ".name", "message": f"第 {i + 1} 条凭据请填写" + ("请求头名（英文 / 数字 / 横线）" if loc == "header" else "参数名" if loc == "query" else "环境变量名（大写）")})
        if not ENV_NAME_RE.match(e):
            errs.append({"field": f + ".env", "message": f"第 {i + 1} 条凭据请填写 Bot 侧环境变量名（大写字母 / 数字 / 下划线）"})
        if (loc, n.lower() if loc == "header" else n) in seen:
            errs.append({"field": f + ".name", "message": f"凭据 {n} 重复"})
        seen.add((loc, n.lower() if loc == "header" else n))
        scheme = "Bearer" if (c.get("scheme") == "Bearer" or (loc == "header" and n.lower() == "authorization" and c.get("scheme") != "")) else None
        o = {"location": loc, "name": n, "env": e}
        if scheme and loc == "header":
            o["scheme"] = scheme
        out.append(o)
        if c.get("secret"):
            secrets[n] = str(c["secret"])
    return out, secrets


def _store_secrets(sv: McpServer, new: dict[str, str], clear: bool = False) -> None:
    cur = {} if clear else _secrets(sv)
    names = {c["name"] for c in creds(sv)}
    cur = {k: v for k, v in {**cur, **new}.items() if k in names and v}
    sv.secret_encrypted = encrypt_secret(json.dumps(cur, ensure_ascii=False)) if cur else None


def create(db: Session, data: dict, actor: Actor) -> McpServer:
    v = _validate(data, True)
    if db.get(McpServer, v["name"]):
        raise conflict(f"服务名 {v['name']} 已存在", "MCP_SERVER_EXISTS")
    pos = (db.scalar(select(func.max(McpServer.position))) or 0) + 1
    secrets, warnings = v.pop("_secrets"), v.pop("_warnings")
    sv = McpServer(source="external", position=pos, server_info={}, example_data=False, **v)
    _store_secrets(sv, secrets)
    sv.warnings = warnings  # type: ignore[attr-defined]
    db.add(sv)
    db.flush()
    audit.record(db, actor, "mcp.create", "mcp_server", sv.name, f"注册 MCP 服务 {sv.display_name}（{sv.name}）", None,
                 {"transport": sv.transport})
    return sv


def update(db: Session, name: str, data: dict, actor: Actor) -> McpServer:
    sv = get(db, name)
    if sv.source == "builtin":
        raise forbidden("平台内置 MCP 服务不可编辑", "BUILTIN_READONLY")
    if data.get("name") and data["name"] != name:
        raise unprocessable("服务名注册后不可修改")
    v = _validate({**data, "name": name}, False, sv)
    secrets, warnings = v.pop("_secrets"), v.pop("_warnings")
    before = conn_dict(sv)
    for k, val in v.items():
        if k != "name":
            setattr(sv, k, val)
    _store_secrets(sv, secrets, clear=bool(data.get("clear_secret")))
    sv.warnings = warnings  # type: ignore[attr-defined]
    sv.updated_at = utcnow()
    if "group" in data:
        for child in server_tools(db, name):
            if child.business_group != sv.business_group:
                child.business_group = sv.business_group
                child.updated_at = utcnow()
    after = conn_dict(sv)
    if before != after or "group" in data:
        users = {u.code: u for t in server_tools(db, name) for u in intent_svc.users_of_tool(db, t.name)}
        space_svc.bump_versions(db, {u.space_id for u in users.values() if u.status == "启用"})
    audit.record(db, actor, "mcp.update", "mcp_server", name, f"编辑 MCP 服务 {sv.display_name}", None,
                 {"before": before, "after": after})
    return sv


def delete(db: Session, name: str, actor: Actor) -> None:
    sv = get(db, name)
    if sv.source == "builtin":
        raise forbidden("平台内置 MCP 服务不可删除", "BUILTIN_READONLY")
    used = sorted({u.code for t in server_tools(db, name) for u in intent_svc.users_of_tool(db, t.name)})
    if used:
        raise conflict(f"服务 {name} 下的工具正被 {len(used)} 个意图使用，请先在意图中移除", "TOOL_IN_USE", used_by=used)
    db.delete(sv)
    audit.record(db, actor, "mcp.delete", "mcp_server", name, f"删除 MCP 服务 {sv.display_name}", None)


# ---------------- 工具清单解析 ----------------
def parse_tools_json(text: str) -> list[dict]:
    """手动导入：接受 tools/list 的完整 JSON-RPC 响应、{"result": {"tools": [...]}}、{"tools": [...]} 或直接数组。"""
    try:
        j = json.loads(text)
    except ValueError as e:
        raise bad_request(f"不是合法 JSON：{e}", "BAD_TOOLS_JSON")
    if isinstance(j, dict) and isinstance(j.get("result"), dict):
        j = j["result"]
    if isinstance(j, dict):
        j = j.get("tools")
    if not isinstance(j, list):
        raise bad_request("没有找到 tools 数组：请粘贴 tools/list 的返回结果", "BAD_TOOLS_JSON")
    return j


def normalize_tools(raw: list) -> list[dict]:
    errs, out, seen = [], [], set()
    if len(raw) > MAX_TOOLS:
        raise bad_request(f"单个服务最多 {MAX_TOOLS} 个工具", "BAD_TOOLS_JSON")
    for i, t in enumerate(raw):
        if not isinstance(t, dict):
            errs.append(f"第 {i + 1} 项不是对象")
            continue
        n = str(t.get("name") or "").strip()
        if not REMOTE_NAME_RE.match(n):
            errs.append(f"第 {i + 1} 项工具名「{n}」不合法（1～64 位英文 / 数字 / _ - .）")
            continue
        if n in seen:
            errs.append(f"工具名 {n} 重复")
            continue
        seen.add(n)
        sch = t.get("inputSchema") or t.get("input_schema") or {"type": "object", "properties": {}}
        if not isinstance(sch, dict) or sch.get("type", "object") != "object" or not isinstance(sch.get("properties", {}), dict):
            errs.append(f"{n} 的 inputSchema 不是 type=object 的 JSON Schema")
            continue
        sch = {"type": "object", **sch}
        ann = t.get("annotations") if isinstance(t.get("annotations"), dict) else {}
        title = str(t.get("title") or ann.get("title") or "").strip()
        out.append({"name": n, "title": title[:100], "description": str(t.get("description") or "").strip(),
                    "input_schema": sch, "annotations": ann})
    if errs:
        raise unprocessable("工具清单校验不通过", "BAD_TOOLS_JSON", problems=errs)
    return out


def connection(sv: McpServer) -> tuple[str | None, dict, dict]:
    """在线同步用：(带 URL 参数凭据的地址, 请求头, stdio 环境变量)。只使用平台上保存的密钥；没保存的凭据不发送。"""
    sec = _secrets(sv)
    headers, query, env = {}, [], dict(sv.env or {})
    for c in creds(sv):
        v = sec.get(c["name"])
        if not v:
            continue
        if c["location"] == "header":
            headers[c["name"]] = f"Bearer {v}" if c.get("scheme") == "Bearer" else v
        elif c["location"] == "query":
            query.append((c["name"], v))
        else:
            env[c["name"]] = v
    url = sv.url
    if url and query:
        p = urlsplit(url)
        url = urlunsplit((p.scheme, p.netloc, p.path, urlencode(parse_qsl(p.query) + query), p.fragment))
    return url, headers, env


def fetch_remote(sv: McpServer, actor: Actor) -> tuple[dict, list[dict]]:
    if sv.source == "builtin":
        raise forbidden("平台内置 MCP 服务的工具由平台维护，不需要同步", "BUILTIN_READONLY")
    if sv.transport == "stdio":
        if not get_settings().mcp_stdio_enabled:
            raise bad_request("stdio 服务的在线同步未开启（会在平台服务器上执行命令）。请在能运行该命令的机器上执行 "
                              "tools/list，把返回的 JSON 用「手动导入」粘贴进来；或由运维设置 IP_MCP_STDIO_ENABLED=true。",
                              "STDIO_SYNC_DISABLED")
        if actor.role != "admin":
            raise forbidden("stdio 服务的在线同步只有管理员可以执行", "ROLE_FORBIDDEN")
        _, _, env = connection(sv)
        init, tools = fetch_tools("stdio", command=sv.command, args=list(sv.args or []), env=env,
                                  timeout=get_settings().mcp_timeout_seconds)
    else:
        url, headers, _ = connection(sv)
        init, tools = fetch_tools(sv.transport, url=url, headers=headers, timeout=get_settings().mcp_timeout_seconds)
    info = {"protocol_version": init.get("protocolVersion"), "server_info": init.get("serverInfo") or {}}
    return info, tools


# ---------------- 差异 ----------------
def _param_changes(old: dict, new: dict) -> list[str]:
    op, np_ = old.get("properties") or {}, new.get("properties") or {}
    orq, nrq = set(old.get("required") or []), set(new.get("required") or [])
    out = [f"新增入参 {k}" + ("（必填）" if k in nrq else "") for k in np_ if k not in op]
    out += [f"删除入参 {k}" for k in op if k not in np_]
    out += [f"入参 {k} 改为必填" for k in np_ if k in op and k in nrq and k not in orq]
    out += [f"入参 {k} 改为选填" for k in np_ if k in op and k in orq and k not in nrq]
    out += [f"入参 {k} 类型 {op[k].get('type')} → {np_[k].get('type')}" for k in np_
            if k in op and (op[k] or {}).get("type") != (np_[k] or {}).get("type")]
    if not out and old != new:
        out.append("inputSchema 有调整")
    return out


def diff(db: Session, sv: McpServer, tools: list[dict]) -> dict:
    cur = {t.config.get("mcp_tool"): t for t in server_tools(db, sv.name)}
    new = {t["name"]: t for t in tools}
    added, changed, removed, unchanged = [], [], [], []
    for n, t in new.items():
        old = cur.get(n)
        if not old:
            added.append({"name": n, "tool": tool_name(sv.name, n), "title": t["title"], "description": t["description"],
                          "params": len((t["input_schema"].get("properties") or {}))})
            continue
        ch = []
        if old.remote_missing:
            ch.append("远端重新出现，恢复可用")
        if (old.description or "") != t["description"]:
            ch.append("说明有变化")
        ch += _param_changes(old.config.get("input_schema") or {}, t["input_schema"])
        users = intent_svc.users_of_tool(db, old.name)
        if ch:
            changed.append({"name": n, "tool": old.name, "changes": ch,
                            "used_by": [{"code": u.code, "name": u.name, "status": u.status} for u in users]})
        else:
            unchanged.append(n)
    for n, old in cur.items():
        if n in new:
            continue
        users = intent_svc.users_of_tool(db, old.name)
        removed.append({"name": n, "tool": old.name, "already_missing": old.remote_missing,
                        "action": "保留并标红" if users else "删除",
                        "used_by": [{"code": u.code, "name": u.name, "status": u.status} for u in users]})
    return {"added": added, "changed": changed, "removed": removed, "unchanged": unchanged,
            "summary": {"added": len(added), "changed": len(changed), "removed": len(removed),
                        "unchanged": len(unchanged)}}


def preview(db: Session, sv: McpServer, body: dict, actor: Actor) -> dict:
    if sv.source == "builtin":
        raise forbidden("平台内置 MCP 服务的工具由平台维护，不需要同步", "BUILTIN_READONLY")
    if body.get("tools_json") not in (None, ""):
        source, info = "手动导入", {}
        raw = parse_tools_json(str(body["tools_json"]))
    else:
        source = "在线同步"
        info, raw = fetch_remote(sv, actor)
    tools = normalize_tools(raw)
    return {"server": sv.name, "source": source, "server_info": info, "tools": tools, "diff": diff(db, sv, tools)}


def apply(db: Session, sv: McpServer, body: dict, actor: Actor) -> dict:
    if sv.source == "builtin":
        raise forbidden("平台内置 MCP 服务的工具由平台维护，不需要同步", "BUILTIN_READONLY")
    if not isinstance(body.get("tools"), list):
        raise bad_request("tools 应为预览返回的工具数组")
    from . import tools as tool_svc
    tools = normalize_tools(body["tools"])
    d = diff(db, sv, tools)
    cur = {t.config.get("mcp_tool"): t for t in server_tools(db, sv.name)}
    pos = (db.scalar(select(func.max(Tool.position))) or 0) + 1
    affected: dict[str, object] = {}
    now = utcnow()
    for t in tools:
        old = cur.get(t["name"])
        params = tool_svc.schema_params(t["input_schema"])
        ann = t["annotations"]
        if not old:
            db.add(Tool(name=tool_name(sv.name, t["name"]), type="mcp", server=sv.name,
                        display_name=(t["title"] or t["name"])[:100], description=t["description"] or t["title"] or t["name"],
                        params=params, source="external", maintenance=False, space_scoped=False, endpoints=[],
                        mcp_tools=[], enabled=True, remote_missing=False, owner=sv.owner,
                        business_group=sv.business_group, position=pos, updated_at=now,
                        config={"mcp_tool": t["name"], "title": t["title"], "input_schema": t["input_schema"],
                                "annotations": ann, "output_example": "",
                                "confirm_required": ann.get("readOnlyHint") is False or ann.get("destructiveHint") is True}))
            pos += 1
            continue
        if not next((c for c in d["changed"] if c["name"] == t["name"]), None):
            continue
        old.description = t["description"] or old.description
        old.params = params
        old.config = {**old.config, "title": t["title"], "input_schema": t["input_schema"], "annotations": ann}
        old.remote_missing = False
        old.updated_at = now
        for u in intent_svc.users_of_tool(db, old.name):
            affected[u.code] = u
            intent_svc.add_log(u, actor.name, f"MCP 工具 {old.name} 同步后有变化："
                               + "；".join(next(c for c in d["changed"] if c["name"] == t["name"])["changes"]))
    deleted, marked = [], []
    for r in d["removed"]:
        old = cur[r["name"]]
        if r["used_by"]:
            if not old.remote_missing:
                old.remote_missing = True
                old.updated_at = now
                marked.append(old.name)
                for u in intent_svc.users_of_tool(db, old.name):
                    affected[u.code] = u
                    intent_svc.add_log(u, actor.name, f"MCP 工具 {old.name} 在最近一次同步中已不存在，请更换工具")
        else:
            db.delete(old)
            deleted.append(old.name)
    db.flush()
    users = list(affected.values())
    space_svc.bump_versions(db, {u.space_id for u in users if u.status == "启用"})
    downgraded = intent_svc.recheck_enabled(db, users, actor.name, f"MCP 服务 {sv.name} 同步")
    src = body.get("source") if body.get("source") in ("在线同步", "手动导入") else "手动导入"
    sv.last_sync_at, sv.last_sync_by, sv.last_sync_source = now, actor.name, src
    sv.example_data = False
    if isinstance(body.get("server_info"), dict) and body["server_info"]:
        sv.server_info = body["server_info"]
    audit.record(db, actor, "mcp.sync", "mcp_server", sv.name,
                 f"同步 MCP 服务 {sv.name}（{src}）：新增 {d['summary']['added']} · 变更 {d['summary']['changed']} · "
                 f"消失 {d['summary']['removed']}", None,
                 {"summary": d["summary"], "deleted": deleted, "marked_missing": marked, "downgraded": downgraded})
    return {"summary": d["summary"], "deleted": deleted, "marked_missing": marked, "downgraded": downgraded}


def patch_tool(db: Session, t: Tool, data: dict, actor: Actor) -> Tool:
    """MCP 子工具：启用 / 停用、写操作需确认、显示名。"""
    if t.type != "mcp" or not t.server:
        raise unprocessable("只有 MCP 子工具支持单独启停，其他工具请用编辑")
    if t.source == "builtin":
        raise forbidden("平台内置工具不可修改", "BUILTIN_READONLY")
    changed = {}
    if "enabled" in data and bool(data["enabled"]) != t.enabled:
        changed["enabled"] = [t.enabled, bool(data["enabled"])]
        t.enabled = bool(data["enabled"])
    if "confirm_required" in data and bool(data["confirm_required"]) != bool(t.config.get("confirm_required")):
        changed["confirm_required"] = [bool(t.config.get("confirm_required")), bool(data["confirm_required"])]
        t.config = {**t.config, "confirm_required": bool(data["confirm_required"])}
    if "display_name" in data:
        dn = str(data.get("display_name") or "").strip()
        if not dn:
            raise unprocessable("显示名不能为空")
        if dn != t.display_name:
            changed["display_name"] = [t.display_name, dn[:100]]
            t.display_name = dn[:100]
    if "group" in data:
        from .tools import parse_group
        g = parse_group(data.get("group"))
        if g != (t.business_group or None):
            changed["group"] = [t.business_group, g]
            t.business_group = g
    if not changed:
        return t
    t.updated_at = utcnow()
    db.flush()
    users = intent_svc.users_of_tool(db, t.name)
    for u in users:
        intent_svc.add_log(u, actor.name, f"MCP 工具 {t.name} 设置调整：" + "、".join(changed))
    space_svc.bump_versions(db, {u.space_id for u in users if u.status == "启用"})
    downgraded = intent_svc.recheck_enabled(db, users, actor.name, f"MCP 工具 {t.name} 已停用") if "enabled" in changed and not t.enabled else []
    audit.record(db, actor, "mcp.tool", "tool", t.name, f"修改 MCP 工具 {t.name}", None,
                 {"changed": changed, "downgraded": downgraded})
    return t


_ = ApiError
