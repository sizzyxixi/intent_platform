"""工具中心「测试调用」：真实执行一次工具（仅管理员，记审计日志）。

- cli：按命令模板生成参数数组，在本服务器上直接执行（不经 shell），有超时和输出上限；
  凭据只从服务器环境变量读取（如 XCLAW_UMC_TOKEN）或使用 CLI 自己的登录态，平台不保存、不回显。
- http：真实发送请求；平台内置工具在进程内调用本平台自己的开放接口（临时 Key，用完即删）。
- mcp：对登记的服务做 initialize + tools/call（Streamable HTTP / 旧版 SSE）；平台内置 MCP 在进程内调用。
- workflow：按 Dify / Coze / 通用 HTTP 真实调用，凭据取 auth_env 指定的环境变量。
- skill：不执行，返回技能包内容（SKILL.md + 文件列表）。
写操作（confirm_required / 维护类内置工具）需要请求里 confirm=true。
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import time

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .. import audit
from ..config import get_settings
from ..errors import ApiError, forbidden, unprocessable
from ..models import ApiKey, Tool
from ..security import decrypt_secret, new_api_token, token_hash, token_hint
from . import tools as tool_svc
from . import workflows as wf_svc
from .common import Actor, utcnow

AUTH_HINT_CODES = ("AUTH", "TOKEN", "NO_TOKEN", "LOGIN", "UNAUTHORIZED")


def needs_confirm(t: Tool) -> bool:
    c = t.config or {}
    return bool(c.get("confirm_required")) or (t.source == "builtin" and t.maintenance)


def _cap(b: bytes | str | None) -> tuple[str, bool]:
    lim = get_settings().tool_test_max_output_kb * 1024
    if b is None:
        return "", False
    if isinstance(b, str):
        b = b.encode("utf-8", "replace")
    cut = len(b) > lim
    return b[:lim].decode("utf-8", "replace"), cut


def _json_or_none(s: str):
    try:
        return json.loads(s) if s and s.strip()[:1] in "{[" else None
    except ValueError:
        return None


def _search_path() -> str:
    extra = [os.path.expanduser(p) for p in get_settings().tool_test_extra_path.split(":") if p]
    return os.pathsep.join([os.environ.get("PATH", ""), *extra])


# ---------------- CLI ----------------
def run_cli(t: Tool, args: dict) -> dict:
    c = t.config or {}
    s = get_settings()
    if not s.tool_test_cli_enabled:
        raise forbidden("本服务器未开启 CLI 测试执行（IP_TOOL_TEST_CLI_ENABLED）", "CLI_EXEC_DISABLED")
    argv = tool_svc.render_cli(c.get("command_template", ""), {k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else str(v)) for k, v in args.items()})
    if not argv:
        raise unprocessable("命令模板为空", "CLI_TEMPLATE_EMPTY")
    path = _search_path()
    exe = shutil.which(argv[0], path=path)
    base = {"request": {"command": "$ " + shlex.join(argv)}}
    if not exe:
        return {**base, "ok": False, "error_code": "CLI_NOT_INSTALLED",
                "error": f"本服务器上找不到可执行程序 {argv[0]}",
                "hint": f"先在运行平台后端的机器上安装：{c.get('install') or '见工具的安装方式'}；装好后重启后端（start.sh 已把 ~/.xclaw/bin 加入 PATH）"}
    env = {**os.environ, "PATH": path}
    t0 = time.monotonic()
    try:
        p = subprocess.run([exe, *argv[1:]], capture_output=True, timeout=s.tool_test_timeout_seconds, env=env,
                           stdin=subprocess.DEVNULL, check=False)
    except subprocess.TimeoutExpired as e:
        out, _ = _cap(e.stdout)
        err, _ = _cap(e.stderr)
        return {**base, "ok": False, "error_code": "CLI_TIMEOUT", "duration_ms": int((time.monotonic() - t0) * 1000),
                "error": f"执行超过 {s.tool_test_timeout_seconds:g} 秒，已终止", "stdout": out, "stderr": err}
    ms = int((time.monotonic() - t0) * 1000)
    out, cut1 = _cap(p.stdout)
    err, cut2 = _cap(p.stderr)
    res = {**base, "ok": p.returncode == 0, "exit_code": p.returncode, "duration_ms": ms, "stdout": out, "stderr": err,
           "truncated": cut1 or cut2}
    j = _json_or_none(out)
    if j is not None:
        res["stdout_json"] = j
        if isinstance(j, dict) and j.get("ok") is False:
            res["ok"] = False
            e = j.get("error") or {}
            code = str(e.get("code") or "")
            res["error_code"] = code or "CLI_ERROR"
            res["error"] = e.get("messageZh") or e.get("message") or "命令返回失败"
            if any(k in code.upper() for k in AUTH_HINT_CODES):
                res["hint"] = (f"CLI 未登录或登录已过期：在运行平台后端的机器上执行 {c.get('login_command') or '登录命令'}"
                               + (f"，或在后端环境里设置 {c['auth_env']}" if c.get("auth_env") else "") + "，然后重试")
    if not res["ok"] and "error" not in res:
        res["error"] = (err or out).strip()[-500:] or f"退出码 {p.returncode}"
    return res


# ---------------- HTTP ----------------
def _http_send(method: str, url: str, headers: dict, query: dict | None = None, body=None, timeout: float = 30) -> dict:
    t0 = time.monotonic()
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False) as cl:
            r = cl.request(method, url, params=query or None, json=body, headers=headers)
    except httpx.HTTPError as e:
        return {"ok": False, "error_code": "HTTP_ERROR", "error": f"{type(e).__name__}: {e}",
                "duration_ms": int((time.monotonic() - t0) * 1000)}
    text, cut = _cap(r.content)
    out = {"ok": r.status_code < 400, "status": r.status_code, "duration_ms": int((time.monotonic() - t0) * 1000),
           "response": text, "truncated": cut}
    j = _json_or_none(text)
    if j is not None:
        out["response_json"] = j
    if not out["ok"]:
        out["error"] = f"HTTP {r.status_code}"
    return out


def run_http(t: Tool, args: dict) -> dict:
    c = t.config or {}
    m, url = c.get("method", "GET"), c.get("url", "")
    for pv in tool_svc.PLACEHOLDER.findall(url):
        url = url.replace("{" + pv + "}", str(args.pop(pv, "")))
    h = {}
    sec = decrypt_secret(t.secret_encrypted) if t.secret_encrypted else None
    a = c.get("auth") or "无"
    if a != "无" and not sec:
        hint = "该接口需要鉴权，但平台上没有保存密钥：编辑工具填写密钥后再测"
    else:
        hint = None
    if sec and a in ("Bearer Token", "OAuth2"):
        h["Authorization"] = f"Bearer {sec}"
    elif sec and a == "API Key":
        h["X-API-Key"] = sec
    res = _http_send(m, url, h, args if m == "GET" else None, None if m == "GET" else args,
                     get_settings().tool_test_timeout_seconds)
    res["request"] = {"method": m, "url": url, "auth": a}
    if hint and not res["ok"]:
        res["hint"] = hint
    return res


# ---------------- 平台内置（进程内调用自己的开放接口） ----------------
async def _internal(method: str, path: str, space: str | None, query=None, body=None, rpc=None) -> dict:
    from ..db import SessionLocal
    from ..main import app
    tok = new_api_token()
    kid = "tmp_" + token_hash(tok)[:12]
    from ..deps import SCOPES
    from ..models import Space
    with SessionLocal() as db:
        spaces = [space] if space else [x.id for x in db.scalars(select(Space))]
        db.add(ApiKey(id=kid, name="工具测试调用（临时）", owner="系统", token_hash=token_hash(tok), token_hint=token_hint(kid),
                      spaces=spaces, status="有效", scopes=list(SCOPES)))
        db.commit()
    t0 = time.monotonic()
    try:
        h = {"Authorization": f"Bearer {tok}"}
        if space:
            h["X-Space-Id"] = space
        if method != "GET":
            h["Idempotency-Key"] = "test-" + kid
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://internal") as cl:
            r = await cl.request(method, path, params=query or None, json=rpc if rpc is not None else body, headers=h)
    finally:
        with SessionLocal() as db:
            db.execute(delete(ApiKey).where(ApiKey.id == kid))
            db.commit()
    text, cut = _cap(r.content)
    out = {"ok": r.status_code < 400, "status": r.status_code, "duration_ms": int((time.monotonic() - t0) * 1000),
           "response": text, "truncated": cut}
    j = _json_or_none(text)
    if j is not None:
        out["response_json"] = j
        if rpc is not None and isinstance(j, dict):
            res = j.get("result") or {}
            if j.get("error") or res.get("isError"):
                out["ok"] = False
                out["error"] = (j.get("error") or {}).get("message") or "工具返回错误"
    if not out["ok"] and "error" not in out:
        e = j if isinstance(j, dict) else {}
        out["error"] = e.get("message") or f"HTTP {r.status_code}"
    return out


async def run_builtin(t: Tool, args: dict, space: str | None) -> dict:
    c = t.config or {}
    if t.type == "mcp":
        rpc = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": c.get("mcp_tool"), "arguments": args}}
        res = await _internal("POST", "/mcp", space, rpc=rpc)
        res["request"] = {"server": "平台内置 MCP（进程内）", "method": "tools/call", "name": c.get("mcp_tool")}
        return res
    url = c.get("url", "")
    path = url[url.index("/api/v1"):] if "/api/v1" in url else url
    for pv in tool_svc.PLACEHOLDER.findall(path):
        path = path.replace("{" + pv + "}", str(args.pop(pv, "")))
    m = c.get("method", "GET")
    res = await _internal(m, path, space if t.space_scoped else None, query=args if m == "GET" else None,
                          body=None if m == "GET" else args)
    res["request"] = {"method": m, "path": path, "via": "进程内调用本平台开放接口（临时 Key，用完即删）"}
    return res


# ---------------- MCP ----------------
def run_mcp(t: Tool, args: dict) -> dict:
    from . import mcp_client
    from .mcp_servers import connection, creds
    sv = t.server_ref
    if sv is None:
        raise unprocessable("MCP 工具没有所属服务", "MCP_NO_SERVER")
    url, headers, _env = connection(sv)
    missing = []
    for cr in creds(sv):  # 平台没保存密钥的凭据，取服务器环境变量
        if cr["location"] == "header" and cr["name"] not in headers:
            v = os.environ.get(cr.get("env") or "")
            if v:
                headers[cr["name"]] = f"Bearer {v}" if cr.get("scheme") == "Bearer" else v
            else:
                missing.append(cr.get("env") or cr["name"])
    if sv.transport != "streamable_http":
        raise unprocessable(f"测试调用目前只支持 Streamable HTTP 的 MCP 服务（当前 {sv.transport}）", "MCP_TRANSPORT")
    t0 = time.monotonic()
    try:
        result = mcp_client.call_tool(url or "", headers, c_name := (t.config or {}).get("mcp_tool"), args,
                                      get_settings().tool_test_timeout_seconds)
        ok = not result.get("isError")
        text, cut = _cap(json.dumps(result, ensure_ascii=False))
        res = {"ok": ok, "duration_ms": int((time.monotonic() - t0) * 1000), "response": text, "response_json": result,
               "truncated": cut}
        if not ok:
            res["error"] = "工具返回 isError"
    except ApiError as e:
        res = {"ok": False, "error_code": e.code, "error": e.message, "duration_ms": int((time.monotonic() - t0) * 1000)}
    res["request"] = {"server": sv.name, "url": (url or "").split("?")[0], "method": "tools/call", "name": c_name}
    if missing and not res["ok"]:
        res["hint"] = "以下凭据既没在平台保存，也没在后端环境变量里设置：" + "、".join(missing)
    return res


# ---------------- 工作流 ----------------
def run_workflow(t: Tool, args: dict) -> dict:
    c = t.config or {}
    req = wf_svc.request_preview(c, args)
    env_name = c.get("auth_env")
    headers = dict(req.get("headers") or {})
    val = os.environ.get(env_name or "") if env_name else None
    for k, v in list(headers.items()):
        if "$" in str(v):
            if not val:
                return {"ok": False, "error_code": "WF_CREDENTIAL_MISSING", "request": {"method": req["method"], "url": req["url"]},
                        "error": f"后端环境变量 {env_name or '（未配置）'} 没有设置，无法调用",
                        "hint": f"在运行平台后端的环境里设置 {env_name}（工作流平台的 Key / Token）后重启"}
            headers[k] = str(v).split("$")[0] + val
    body = req.get("json")
    if isinstance(body, dict) and body.get("user") == "$session.user":
        body["user"] = "intent-platform-test"
    res = _http_send(req["method"], req["url"], headers, None, body, float(c.get("timeout_sec") or get_settings().tool_test_timeout_seconds))
    res["request"] = {"method": req["method"], "url": req["url"], "credential_env": env_name}
    return res


# ---------------- 技能 ----------------
def show_skill(db: Session, t: Tool) -> dict:
    from . import skills as skill_svc
    pkg = skill_svc.current(db, t)
    if not pkg:
        return {"ok": False, "error_code": "SKILL_NO_PACKAGE", "error": "还没有上传技能包"}
    return {"ok": True, "executed": False, "note": "技能不执行：Bot 把 SKILL.md 交给 AI，按意图上下文使用。这里展示技能包内容。",
            "skill": {"name": pkg.name, "version": pkg.version, "files": pkg.files, "skill_md": pkg.skill_md}}


async def execute(db: Session, t: Tool, values: dict, space: str | None, confirm: bool, actor: Actor) -> dict:
    if not get_settings().tool_test_enabled:
        raise forbidden("本服务器未开启测试调用（IP_TOOL_TEST_ENABLED）", "TOOL_TEST_DISABLED")
    if t.type == "skill":
        return {"type": t.type, **show_skill(db, t)}
    args = tool_svc.coerce_args(t, values)
    if needs_confirm(t) and not confirm:
        raise unprocessable("这是写操作，执行前需要勾选确认", "CONFIRM_REQUIRED")
    if t.source == "builtin":
        res = await run_builtin(t, args, space)
    elif t.type == "cli":
        from starlette.concurrency import run_in_threadpool
        res = await run_in_threadpool(run_cli, t, args)
    elif t.type == "http":
        from starlette.concurrency import run_in_threadpool
        res = await run_in_threadpool(run_http, t, args)
    elif t.type == "mcp":
        res = run_mcp(t, args)
    else:
        res = run_workflow(t, args)
    res = {"type": t.type, "executed": True, "at": utcnow().isoformat(), **res}
    audit.record(db, actor, "tool.test", "tool", t.name,
                 f"测试调用 {t.display_name}：{'成功' if res.get('ok') else '失败'}" + (f"（{res.get('error_code')}）" if res.get("error_code") else ""),
                 space, {"ok": bool(res.get("ok")), "duration_ms": res.get("duration_ms"), "params": sorted(args)})
    db.commit()
    return res
