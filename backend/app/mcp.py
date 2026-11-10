"""MCP（Streamable HTTP，JSON 响应）：POST /mcp，JSON-RPC 2.0。工具定义见 AI接入说明.md §9。"""
import base64
import binascii
import hashlib
import json
from datetime import datetime
from pathlib import Path

import jsonschema
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .deps import KeyCtx, api_key, cleanup_idempotency, resolve_space
from .errors import ApiError, forbidden
from .models import IdempotencyRecord
from .services import importer, intents as intent_svc, knowledge as kb_svc, slots as slot_svc
from .services import spaces as space_svc, tools as tool_svc

router = APIRouter()
TOOLS = json.loads((Path(__file__).parent / "mcp_tools.json").read_text(encoding="utf-8"))
TOOL_MAP = {t["name"]: t for t in TOOLS}
SCOPE = {"list_intents": "意图:读", "get_intent": "意图:读", "upsert_intents": "意图:写",
         "search_knowledge": "知识:读", "import_knowledge": "知识:写", "list_tools": "工具:读"}
PROTOCOL = "2025-06-18"


def _rpc(id_, result=None, error=None):
    b = {"jsonrpc": "2.0", "id": id_}
    if error is not None:
        b["error"] = error
    else:
        b["result"] = result
    return b


def _text(obj, is_error=False):
    return {"content": [{"type": "text", "text": json.dumps(obj, ensure_ascii=False, default=str)}],
            "structuredContent": obj if isinstance(obj, dict) else {"items": obj}, "isError": is_error}


def _idem(db: Session, ctx: KeyCtx, key: str, payload: dict, fn):
    h = hashlib.sha256(("mcp\n" + json.dumps(payload, sort_keys=True, ensure_ascii=False)).encode()).hexdigest()
    cleanup_idempotency(db)
    k = "mcp:" + key
    rec = db.scalar(select(IdempotencyRecord).where(IdempotencyRecord.api_key_id == ctx.key.id,
                                                    IdempotencyRecord.key == k[:64]))
    if rec:
        if rec.request_hash != h:
            raise ApiError(409, "IDEMPOTENCY_CONFLICT", "该 idempotency_key 已用于不同的请求，请换一个新的")
        return rec.status_code, rec.response_body
    try:
        status, body = 200, fn()
        db.commit()
    except ApiError as e:
        db.rollback()
        if e.status in (401, 429) or e.status >= 500:
            raise
        status, body = e.status, e.body()
    db.add(IdempotencyRecord(api_key_id=ctx.key.id, key=k[:64], request_hash=h, status_code=status, response_body=body))
    db.commit()
    return status, body


def call_tool(request: Request, db: Session, ctx: KeyCtx, name: str, args: dict, background: list):
    if SCOPE[name] not in (ctx.key.scopes or []):
        raise forbidden(f"该 Key 没有「{SCOPE[name]}」权限", "SCOPE_FORBIDDEN", required=SCOPE[name])
    if name == "list_intents":
        sp = resolve_space(request, db, ctx, args.get("space"))
        view = args.get("view") or "intents"
        if view == "taxonomy":
            return space_svc.taxonomy_dict(db, sp.id)
        if view == "slots":
            ub = slot_svc.used_by_map(db, sp.id)
            return {"slots": [slot_svc.to_dict(s, ub.get(s.name, [])) for s in slot_svc.list_slots(db, sp.id)]}
        if view == "spaces":
            from .models import Space
            return {"spaces": [space_svc.space_dict(s) for s in db.scalars(select(Space).where(Space.id.in_(ctx.key.spaces)))]}
        if view == "recognition":
            return {**space_svc.recognition(sp), "version": sp.version}
        ua = args.get("updated_after")
        ua = datetime.fromisoformat(ua.replace("Z", "+00:00")) if ua else None
        total, rows = intent_svc.list_page(db, sp.id, 1, args.get("page_size") or 50, l1=args.get("l1"),
                                           l2=args.get("l2"), status=args.get("status"), q=args.get("q"),
                                           updated_after=ua)
        return {"total": total, "items": [intent_svc.api_summary(i) for i in rows]}
    if name == "get_intent":
        sp = resolve_space(request, db, ctx, args.get("space"))
        return intent_svc.api_detail(intent_svc.get(db, args["code"], sp.id))
    if name == "upsert_intents":
        sp = resolve_space(request, db, ctx, args.get("space"))
        payload = {"mode": "merge", "intents": args["intents"]}
        st, body = _idem(db, ctx, args["idempotency_key"], {"t": name, "space": sp.id, **payload},
                         lambda: importer.batch_upsert(db, sp, payload, ctx.actor, file="MCP upsert_intents"))
        if st >= 400:
            raise ApiError(st, body.get("code", "ERROR"), body.get("message", ""), body.get("details"))
        return body
    if name == "search_knowledge":
        hits = kb_svc.search(db, args.get("kb_id"), args["query"], args, args.get("top_k"), None)
        return {"items": hits}
    if name == "import_knowledge":
        files = []
        for f in args["files"]:
            try:
                files.append((f["name"], base64.b64decode(f["content_base64"], validate=True)))
            except (binascii.Error, ValueError):
                raise ApiError(400, "BAD_REQUEST", f"文件 {f['name']} 的 content_base64 不是合法 Base64")
        ov = bool(args.get("overwrite"))
        digest = [[n, hashlib.sha256(b).hexdigest()] for n, b in files]
        jobs = []

        def run():
            kb = kb_svc.get_kb(db, args["kb_id"])
            task, job = kb_svc.import_documents(db, kb, files, ov, ctx.actor, source="AI 工具（API）")
            db.flush()
            jobs.append(job)
            return task
        st, body = _idem(db, ctx, args["idempotency_key"], {"t": name, "kb": args["kb_id"], "ov": ov, "f": digest}, run)
        for j in jobs:
            if j["files"]:
                background.append(j)
        if st >= 400:
            raise ApiError(st, body.get("code", "ERROR"), body.get("message", ""), body.get("details"))
        return body
    if name == "list_tools":
        ts = tool_svc.list_tools(db, args.get("source"))
        q = (args.get("q") or "").lower()
        out = [tool_svc.api_dict(t) for t in ts if (not args.get("type") or t.type == args["type"]) and
               (not q or q in t.name.lower() or q in t.display_name.lower() or q in (t.description or "").lower())]
        return {"items": out}
    raise ApiError(404, "TOOL_NOT_FOUND", f"未知工具 {name}")


@router.get("/mcp")
def mcp_get():
    return Response(status_code=405, headers={"Allow": "POST"})


@router.post("/mcp")
async def mcp(request: Request, ctx: KeyCtx = Depends(api_key(None)), db: Session = Depends(get_db)):
    try:
        msg = json.loads(await request.body() or b"null")
    except ValueError:
        return JSONResponse(_rpc(None, error={"code": -32700, "message": "Parse error"}), status_code=400)
    if isinstance(msg, list):
        return JSONResponse(_rpc(None, error={"code": -32600, "message": "不支持批量请求"}), status_code=400)
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
        return JSONResponse(_rpc(msg.get("id") if isinstance(msg, dict) else None,
                                 error={"code": -32600, "message": "Invalid Request"}), status_code=400)
    mid, method, params = msg.get("id"), msg["method"], msg.get("params") or {}
    if "id" not in msg:  # 通知
        return Response(status_code=202)
    if method == "initialize":
        return JSONResponse(_rpc(mid, {"protocolVersion": params.get("protocolVersion") or PROTOCOL,
                                       "capabilities": {"tools": {"listChanged": False}},
                                       "serverInfo": {"name": "intent-platform", "version": "0.5.0"}}))
    if method == "ping":
        return JSONResponse(_rpc(mid, {}))
    if method == "tools/list":
        return JSONResponse(_rpc(mid, {"tools": TOOLS}))
    if method != "tools/call":
        return JSONResponse(_rpc(mid, error={"code": -32601, "message": f"Method not found: {method}"}))
    name, args = params.get("name"), params.get("arguments") or {}
    if name not in TOOL_MAP:
        return JSONResponse(_rpc(mid, error={"code": -32602, "message": f"未知工具 {name}"}))
    try:
        jsonschema.validate(args, TOOL_MAP[name]["inputSchema"])
    except jsonschema.ValidationError as e:
        return JSONResponse(_rpc(mid, _text({"code": "BAD_REQUEST", "message": f"参数不合法：{e.message}",
                                             "details": {"path": list(e.absolute_path)}}, True)))
    background: list = []
    try:
        result = call_tool(request, db, ctx, name, args, background)
        db.commit()
        out = _rpc(mid, _text(result))
    except ApiError as e:
        db.rollback()
        out = _rpc(mid, _text(e.body(getattr(request.state, "request_id", None)), True))
    resp = JSONResponse(out)
    if background:
        from starlette.background import BackgroundTasks
        bt = BackgroundTasks()
        for j in background:
            bt.add_task(kb_svc.run_build, j)
        resp.background = bt
    return resp
