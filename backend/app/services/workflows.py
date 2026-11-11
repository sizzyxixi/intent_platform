"""工作流工具：对接 Dify / Coze / 通用 HTTP 工作流的原生 API。平台只登记和同步入参，执行在 Bot。

- Dify：POST {基址}/workflows/run（基址形如 https://api.dify.ai/v1）；入参来自 GET {基址}/parameters 的 user_input_form；
  user 字段为常量或映射会话用户（$session.user）。
- Coze：POST https://api.coze.cn|com/v1/workflow/run（流式 /v1/workflow/stream_run）；入参与输出字段来自
  GET /v1/workflows/{id}?include_input_output=true；可选 workflow_version、is_async、bot_id、app_id、ext。
- 通用 HTTP：手工维护入参。
密钥只登记环境变量名。同步入参时可临时提供一次密钥（不保存），也可以粘贴接口返回的 JSON 离线同步。
"""
from __future__ import annotations

import json
import re

import httpx

from ..config import get_settings
from ..errors import ApiError, bad_request, unprocessable

PLATFORMS = {"dify": "Dify", "coze": "Coze", "generic": "通用 HTTP"}
REGIONS = {"coze.cn": "https://api.coze.cn", "coze.com": "https://api.coze.com"}
ENV_RE = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")
GENERIC_AUTH = ("无", "API Key", "Bearer Token")
FILE_TYPES = ("file", "file_list")


class WorkflowSyncError(ApiError):
    def __init__(self, message: str, details: dict | None = None):
        super().__init__(502, "WORKFLOW_SYNC_FAILED", message, details or {})


def validate_config(cfg_in: dict, errs: list[dict]) -> dict:
    plat = cfg_in.get("platform") or ""
    if plat not in PLATFORMS:
        errs.append({"field": "config.platform", "message": "工作流平台为 dify / coze / generic"})
        return {}
    mode = cfg_in.get("response_mode") or "blocking"
    if mode not in ("blocking", "streaming"):
        errs.append({"field": "config.response_mode", "message": "响应模式为 blocking（同步）/ streaming（流式）"})
    to = cfg_in.get("timeout_sec")
    if to in ("", None):
        to = None
    else:
        try:
            to = float(to)
            if to <= 0 or to > 3600:
                raise ValueError
            to = int(to) if to == int(to) else to
        except (TypeError, ValueError):
            errs.append({"field": "config.timeout_sec", "message": "超时需为 0～3600 之间的秒数"})
            to = None
    env = str(cfg_in.get("auth_env") or "").strip()
    om = []
    for i, m in enumerate(cfg_in.get("output_mapping") or []):
        f, a = str((m or {}).get("from") or "").strip(), str((m or {}).get("as") or "").strip()
        if not f and not a:
            continue
        if not f or not a:
            errs.append({"field": f"config.output_mapping[{i}]", "message": "输出映射需同时填写工作流输出字段和映射名"})
        om.append({"from": f, "as": a})
    cfg = {"platform": plat, "response_mode": mode, "timeout_sec": to, "confirm_required": bool(cfg_in.get("confirm_required")),
           "auth_env": env or None, "output_mapping": om, "params_locked": plat != "generic" and cfg_in.get("params_locked", True) is not False,
           "output_description": str(cfg_in.get("output_description") or "").strip()}
    if plat in ("dify", "coze") and not ENV_RE.match(env):
        errs.append({"field": "config.auth_env", "message": ("应用 API Key" if plat == "dify" else "访问令牌") + "只填环境变量名（大写字母 / 数字 / 下划线，如 "
                     + ("DIFY_APP_KEY" if plat == "dify" else "COZE_PAT") + "）"})
    if plat == "generic" and env and not ENV_RE.match(env):
        errs.append({"field": "config.auth_env", "message": "凭据环境变量名为大写字母 / 数字 / 下划线"})
    if plat == "dify":
        base = str(cfg_in.get("base_url") or "").strip().rstrip("/")
        if not re.match(r"^https?://\S+$", base):
            errs.append({"field": "config.base_url", "message": "Dify API 基址必须是 http(s) 地址，如 https://api.dify.ai/v1"})
        um = (cfg_in.get("user") or {}).get("mode") or "constant"
        if um not in ("constant", "session"):
            errs.append({"field": "config.user.mode", "message": "user 字段为 constant（常量）/ session（会话用户）"})
        uv = str((cfg_in.get("user") or {}).get("value") or "").strip() or "intent-bot"
        cfg.update({"base_url": base, "user": {"mode": um, "value": uv} if um == "constant" else {"mode": "session"}})
    elif plat == "coze":
        reg = cfg_in.get("region") or "coze.cn"
        if reg not in REGIONS:
            errs.append({"field": "config.region", "message": "区域为 coze.cn / coze.com"})
        wid = str(cfg_in.get("workflow_id") or "").strip()
        if not re.match(r"^[A-Za-z0-9_\-]{1,64}$", wid):
            errs.append({"field": "config.workflow_id", "message": "请填写工作流 ID"})
        ext = cfg_in.get("ext")
        if isinstance(ext, str):
            ext = ext.strip()
            if ext:
                try:
                    ext = json.loads(ext)
                except ValueError:
                    errs.append({"field": "config.ext", "message": "ext 不是合法 JSON"})
                    ext = None
            else:
                ext = None
        if ext is not None and not isinstance(ext, dict):
            errs.append({"field": "config.ext", "message": "ext 需为 JSON 对象"})
            ext = None
        cfg.update({"region": reg, "workflow_id": wid, "workflow_version": str(cfg_in.get("workflow_version") or "").strip() or None,
                    "is_async": bool(cfg_in.get("is_async")), "bot_id": str(cfg_in.get("bot_id") or "").strip() or None,
                    "app_id": str(cfg_in.get("app_id") or "").strip() or None, "ext": ext})
    else:
        url = str(cfg_in.get("url") or "").strip()
        if not re.match(r"^https?://\S+$", url):
            errs.append({"field": "config.url", "message": "请求地址需以 http(s):// 开头"})
        m = cfg_in.get("method") or "POST"
        if m not in ("POST", "GET", "PUT"):
            errs.append({"field": "config.method", "message": "方法为 POST / GET / PUT"})
        a = cfg_in.get("auth") or "无"
        if a not in GENERIC_AUTH:
            errs.append({"field": "config.auth", "message": "鉴权为 无 / API Key / Bearer Token"})
        if a != "无" and not env:
            errs.append({"field": "config.auth_env", "message": "请填写凭据环境变量名"})
        cfg.update({"url": url, "method": m, "auth": a})
    return cfg


def missing_fields(cfg: dict) -> list[str]:
    """启用前校验用：工作流配置是否完整（兼容旧数据）。"""
    p = cfg.get("platform")
    if p not in PLATFORMS:
        return ["未选择工作流平台"]
    if p == "dify" and not cfg.get("base_url"):
        return ["未填写 Dify API 基址"]
    if p == "coze" and not cfg.get("workflow_id"):
        return ["未填写工作流 ID"]
    if p == "generic" and not cfg.get("url"):
        return ["未填写工作流请求地址"]
    return []


# ---------------- 入参同步 ----------------
DIFY_TYPES = {"text-input": "string", "paragraph": "string", "select": "string", "number": "number",
              "checkbox": "boolean", "file": "file", "file-list": "file_list", "json_object": "string"}


def parse_dify_parameters(j: dict) -> tuple[list[dict], list[dict]]:
    form = (j or {}).get("user_input_form")
    if not isinstance(form, list):
        raise bad_request("没有找到 user_input_form：请粘贴 GET /parameters 的返回结果", "BAD_WORKFLOW_JSON")
    out = []
    for item in form:
        if not isinstance(item, dict) or not item:
            continue
        kind, body = next(iter(item.items()))
        body = body or {}
        n = str(body.get("variable") or "").strip()
        if not n:
            continue
        desc = str(body.get("label") or "")
        if kind == "select" and body.get("options"):
            desc += ("（可选：" + "、".join(map(str, body["options"])) + "）")
        d = body.get("default")
        out.append({"name": n, "type": DIFY_TYPES.get(kind, "string"), "required": bool(body.get("required")),
                    "description": desc.strip(), "default": "" if d in (None, "") else str(d)})
    return out, []


def _coze_type(v: dict) -> str:
    t = (v or {}).get("type") or "string"
    if (v or {}).get("assist_type") not in (None, 0, "0", ""):
        return "file"
    if t in ("integer", "number", "float"):
        return "number"
    if t == "boolean":
        return "boolean"
    if t in ("array", "list"):
        it = (v or {}).get("items") or (v or {}).get("schema") or {}
        if isinstance(it, dict) and it.get("assist_type") not in (None, 0, "0", ""):
            return "file_list"
        return "string[]"
    if t in ("file", "image"):
        return "file"
    return "string"


def _coze_params(node) -> list[tuple[str, dict]]:
    if isinstance(node, dict):
        ps = node.get("parameters", node)
        if isinstance(ps, dict):
            return [(k, v if isinstance(v, dict) else {}) for k, v in ps.items()]
        if isinstance(ps, list):
            return [(str(x.get("name")), x) for x in ps if isinstance(x, dict) and x.get("name")]
    return []


def parse_coze_workflow(j: dict) -> tuple[list[dict], list[dict]]:
    data = (j or {}).get("data", j) or {}
    if isinstance(j, dict) and j.get("code") not in (None, 0):
        raise WorkflowSyncError(f"Coze 返回错误：{j.get('msg') or j.get('code')}")
    if not isinstance(data, dict) or "input" not in data:
        raise bad_request("没有找到 input：请粘贴 GET /v1/workflows/{id}?include_input_output=true 的返回结果", "BAD_WORKFLOW_JSON")
    params = []
    for k, v in _coze_params(data.get("input")):
        d = v.get("default_value", v.get("default"))
        params.append({"name": k, "type": _coze_type(v), "required": bool(v.get("required")),
                       "description": str(v.get("description") or ""), "default": "" if d in (None, "") else str(d)})
    outs = [{"from": f"data.{k}", "as": k} for k, _ in _coze_params(data.get("output"))]
    return params, outs


def sync_inputs(cfg_in: dict, secret: str | None = None, raw_json: str | None = None) -> dict:
    errs: list[dict] = []
    cfg = validate_config({**cfg_in, "auth_env": cfg_in.get("auth_env") or "X"}, errs)
    errs = [e for e in errs if e["field"] not in ("config.auth_env",)]
    if errs:
        raise unprocessable("工作流配置不完整，无法同步入参", details={"errors": errs})
    plat = cfg["platform"]
    if plat == "generic":
        raise bad_request("通用 HTTP 工作流请手工维护入参", "WORKFLOW_SYNC_UNSUPPORTED")
    if raw_json not in (None, ""):
        try:
            j = json.loads(raw_json)
        except ValueError as e:
            raise bad_request(f"不是合法 JSON：{e}", "BAD_WORKFLOW_JSON")
        source = "粘贴 JSON"
    else:
        if not secret:
            raise bad_request("在线同步需要临时提供一次密钥（只用于本次请求，不保存）；也可以粘贴接口返回的 JSON", "WORKFLOW_SECRET_REQUIRED")
        if plat == "dify":
            url, params = cfg["base_url"].rstrip("/") + "/parameters", {"user": (cfg.get("user") or {}).get("value", "intent-bot")}
        else:
            url, params = REGIONS[cfg["region"]] + f"/v1/workflows/{cfg['workflow_id']}", {"include_input_output": "true"}
            if cfg.get("workflow_version"):
                params["workflow_version"] = cfg["workflow_version"]
        try:
            with httpx.Client(timeout=get_settings().mcp_timeout_seconds) as c:
                r = c.get(url, params=params, headers={"Authorization": f"Bearer {secret}"})
        except httpx.HTTPError as e:
            raise WorkflowSyncError(f"连接 {PLATFORMS[plat]} 失败：{type(e).__name__} {e}")
        if r.status_code >= 400:
            raise WorkflowSyncError(f"{PLATFORMS[plat]} 返回 HTTP {r.status_code}：{r.text[:200]}")
        try:
            j = r.json()
        except ValueError:
            raise WorkflowSyncError(f"{PLATFORMS[plat]} 返回的不是 JSON")
        source = "在线同步"
    params, outs = parse_dify_parameters(j) if plat == "dify" else parse_coze_workflow(j)
    if plat == "dify" and not outs:
        outs = []
    return {"platform": plat, "source": source, "params": params, "output_mapping": outs}


# ---------------- 请求预览（Bot 执行规则的参考实现） ----------------
def request_preview(cfg: dict, args: dict) -> dict:
    plat = cfg.get("platform")
    to = cfg.get("timeout_sec")
    if plat == "dify":
        user = "$session.user" if (cfg.get("user") or {}).get("mode") == "session" else (cfg.get("user") or {}).get("value", "intent-bot")
        req = {"method": "POST", "url": (cfg.get("base_url") or "").rstrip("/") + "/workflows/run",
               "headers": {"Authorization": f"Bearer ${cfg.get('auth_env') or 'DIFY_APP_KEY'}", "Content-Type": "application/json"},
               "json": {"inputs": args, "response_mode": cfg.get("response_mode") or "blocking", "user": user}}
    elif plat == "coze":
        path = "/v1/workflow/stream_run" if cfg.get("response_mode") == "streaming" else "/v1/workflow/run"
        payload: dict = {"workflow_id": cfg.get("workflow_id"), "parameters": args}
        for k in ("workflow_version", "bot_id", "app_id", "ext"):
            if cfg.get(k):
                payload[k] = cfg[k]
        if cfg.get("is_async"):
            payload["is_async"] = True
        req = {"method": "POST", "url": REGIONS.get(cfg.get("region") or "coze.cn", REGIONS["coze.cn"]) + path,
               "headers": {"Authorization": f"Bearer ${cfg.get('auth_env') or 'COZE_PAT'}", "Content-Type": "application/json"},
               "json": payload}
    else:
        h = {"Content-Type": "application/json"}
        if cfg.get("auth") == "Bearer Token":
            h["Authorization"] = f"Bearer ${cfg.get('auth_env') or 'WF_TOKEN'}"
        elif cfg.get("auth") == "API Key":
            h["X-API-Key"] = f"${cfg.get('auth_env') or 'WF_TOKEN'}"
        req = {"method": cfg.get("method") or "POST", "url": cfg.get("url"), "headers": h, "json": {"inputs": args}}
    if to:
        req["timeout_sec"] = to
    req["confirm_required"] = bool(cfg.get("confirm_required"))
    return req


def export(cfg: dict) -> dict:
    """运行时配置 / 开放接口里的 workflow 对象（不含任何密钥值）。"""
    o = {"platform": cfg.get("platform"), "response_mode": cfg.get("response_mode") or "blocking",
         "auth_env": cfg.get("auth_env"), "output_mapping": cfg.get("output_mapping") or [],
         "confirm_required": bool(cfg.get("confirm_required"))}
    if cfg.get("timeout_sec"):
        o["timeout_sec"] = cfg["timeout_sec"]
    p = cfg.get("platform")
    if p == "dify":
        o.update({"base_url": cfg.get("base_url"), "user": cfg.get("user") or {"mode": "constant", "value": "intent-bot"}})
    elif p == "coze":
        o.update({"region": cfg.get("region") or "coze.cn", "workflow_id": cfg.get("workflow_id")})
        for k in ("workflow_version", "bot_id", "app_id", "ext"):
            if cfg.get(k):
                o[k] = cfg[k]
        if cfg.get("is_async"):
            o["is_async"] = True
    else:
        o.update({"url": cfg.get("url"), "method": cfg.get("method") or "POST", "auth": cfg.get("auth") or "无"})
    if cfg.get("output_description"):
        o["output_description"] = cfg["output_description"]
    return o
