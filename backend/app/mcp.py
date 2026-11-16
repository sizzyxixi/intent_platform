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
from .services import spaces as space_svc, taxonomy as tax_svc, tools as tool_svc

router = APIRouter()
TOOLS = json.loads((Path(__file__).parent / "mcp_tools.json").read_text(encoding="utf-8"))
TOOL_MAP = {t["name"]: t for t in TOOLS}
SCOPE = {"list_intents": "意图:读", "get_intent": "意图:读", "upsert_intents": "意图:写",
         "search_knowledge": "知识:读", "import_knowledge": "知识:写", "list_tools": "工具:读",
         "register_tool": "工具:写", "update_tool": "工具:写", "disable_tool": "工具:写",
         "intent_bind_tool": "意图:写", "intent_unbind_tool": "意图:写",
         "taxonomy_upsert_node": "意图:写", "taxonomy_disable_node": "意图:写",
         "intent_enable": "意图:写", "intent_disable": "意图:写",
         "create_knowledge_base": "知识:写",
         "intent_bind_knowledge": ("意图:写", "知识:写"), "intent_unbind_knowledge": ("意图:写", "知识:写")}
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


def _need_idem(args: dict):
    if not args.get("idempotency_key"):
        raise ApiError(400, "IDEMPOTENCY_KEY_REQUIRED", "写入须提供 idempotency_key")


def _scope_list(name: str) -> list[str]:
    s = SCOPE[name]
    return list(s) if isinstance(s, (list, tuple)) else [s]


def call_tool(request: Request, db: Session, ctx: KeyCtx, name: str, args: dict, background: list):
    have = ctx.key.scopes or []
    missing = [s for s in _scope_list(name) if s not in have]
    if missing:
        raise forbidden(f"该 Key 没有「{missing[0]}」权限", "SCOPE_FORBIDDEN", required=missing[0],
                        required_all=_scope_list(name))
    if name == "list_intents":
        sp = resolve_space(request, db, ctx, args.get("space"))
        view = args.get("view") or "intents"
        if view == "taxonomy":
            return tax_svc.public_taxonomy(db, sp, max_depth=args.get("max_depth"),
                                           parent=args.get("parent"))
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
        keep = intent_svc.preload_categories(db, sp.id)
        cats = intent_svc.path_filter(db, sp, args.get("category"), args.get("l1"), args.get("l2"))
        total, rows = intent_svc.list_page(db, sp.id, 1, args.get("page_size") or 50, category_ids=cats,
                                           status=args.get("status"), q=args.get("q"), updated_after=ua)
        out = {"total": total, "items": [intent_svc.api_summary(i) for i in rows]}
        del keep
        return out
    if name == "get_intent":
        sp = resolve_space(request, db, ctx, args.get("space"))
        return intent_svc.api_detail(intent_svc.get(db, args["code"], sp.id))
    if name == "upsert_intents":
        sp = resolve_space(request, db, ctx, args.get("space"))
        payload = {"mode": "merge", "intents": args["intents"],
                   "auto_create_parents": bool(args.get("auto_create_parents"))}
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
    if name in ("register_tool", "update_tool"):
        if not args.get("dry_run"):
            _need_idem(args)
        from .models import Tool
        payload = {k: v for k, v in args.items() if k not in ("idempotency_key", "dry_run", "space")}
        if name == "update_tool":
            payload["name"] = args["name"]
            existing = tool_svc.get(db, args["name"])
            if args.get("dry_run"):
                tool_svc.validate_payload(payload, False, existing)
                return {"ok": True, "dry_run": True, "action": "updated", "name": args["name"]}
            st, body = _idem(db, ctx, args["idempotency_key"], {"t": name, **{k: v for k, v in payload.items() if k != "secret"}},
                             lambda: (tool_svc.api_dict(tool_svc.update(db, args["name"], payload, ctx.actor)) | {"action": "updated"}))
        else:
            existing = db.get(Tool, str(payload.get("name") or "").strip() or None) if payload.get("name") else None
            if args.get("dry_run"):
                tool_svc.validate_payload(payload, existing is None, existing)
                return {"ok": True, "dry_run": True, "action": "updated" if existing else "created",
                        "name": payload.get("name")}
            def run_reg():
                t, action = tool_svc.upsert(db, payload, ctx.actor)
                return {**tool_svc.api_dict(t), "action": action}
            st, body = _idem(db, ctx, args["idempotency_key"], {"t": name, **{k: v for k, v in payload.items() if k != "secret"}}, run_reg)
        if st >= 400:
            raise ApiError(st, body.get("code", "ERROR"), body.get("message", ""), body.get("details"))
        return body
    if name == "disable_tool":
        _need_idem(args)
        st, body = _idem(db, ctx, args["idempotency_key"], {"t": name, "name": args["name"]},
                         lambda: ({**tool_svc.api_dict(tool_svc.set_enabled(db, args["name"], False, ctx.actor)),
                                   "action": "disabled"}))
        if st >= 400:
            raise ApiError(st, body.get("code", "ERROR"), body.get("message", ""), body.get("details"))
        return body
    if name == "intent_bind_tool":
        sp = resolve_space(request, db, ctx, args.get("space"))
        it = intent_svc.get(db, args["code"], sp.id)
        bind = {"tool": args["tool"], "param_map": args.get("param_map") or {}}
        if args.get("dry_run"):
            return {"ok": True, "dry_run": True, "code": it.code, "bind": intent_svc.preview_bind(db, it, bind)}
        _need_idem(args)
        st, body = _idem(db, ctx, args["idempotency_key"], {"t": name, "space": sp.id, "code": it.code, **bind},
                         lambda: (lambda u: {**intent_svc.api_detail(u[0]), "warnings": u[1]})(
                             intent_svc.bind_tool(db, it, bind, ctx.actor)))
        if st >= 400:
            raise ApiError(st, body.get("code", "ERROR"), body.get("message", ""), body.get("details"))
        return body
    if name == "intent_unbind_tool":
        _need_idem(args)
        sp = resolve_space(request, db, ctx, args.get("space"))
        it = intent_svc.get(db, args["code"], sp.id)
        st, body = _idem(db, ctx, args["idempotency_key"],
                         {"t": name, "space": sp.id, "code": it.code, "tool": args["tool"]},
                         lambda: intent_svc.api_detail(intent_svc.unbind_tool(db, it, args["tool"], ctx.actor)))
        if st >= 400:
            raise ApiError(st, body.get("code", "ERROR"), body.get("message", ""), body.get("details"))
        return body
    if name == "taxonomy_upsert_node":
        sp = resolve_space(request, db, ctx, args.get("space"))
        payload = {k: v for k, v in args.items() if k not in ("idempotency_key", "dry_run", "space")}
        if args.get("dry_run"):
            existing, v = tax_svc.validate_node(db, sp, payload)
            return {"ok": True, "dry_run": True, "action": "updated" if existing else "created", "code": v["code"]}
        _need_idem(args)
        st, body = _idem(db, ctx, args["idempotency_key"], {"t": name, "space": sp.id, **payload},
                         lambda: (lambda r: {**tax_svc.node_dict(r[0], tax_svc.Tax(db, sp)), "action": r[1]})(
                             tax_svc.upsert_node(db, sp, payload, ctx.actor)))
        if st >= 400:
            raise ApiError(st, body.get("code", "ERROR"), body.get("message", ""), body.get("details"))
        return body
    if name == "taxonomy_disable_node":
        _need_idem(args)
        sp = resolve_space(request, db, ctx, args.get("space"))
        ref = args.get("id") if args.get("id") not in (None, "") else args.get("code")
        st, body = _idem(db, ctx, args["idempotency_key"], {"t": name, "space": sp.id, "ref": ref},
                         lambda: ({**tax_svc.node_dict(tax_svc.set_node_enabled(db, sp, ref, False, ctx.actor),
                                                       tax_svc.Tax(db, sp)), "action": "disabled"}))
        if st >= 400:
            raise ApiError(st, body.get("code", "ERROR"), body.get("message", ""), body.get("details"))
        return body
    if name == "intent_enable":
        _need_idem(args)
        sp = resolve_space(request, db, ctx, args.get("space"))
        st, body = _idem(db, ctx, args["idempotency_key"], {"t": name, "space": sp.id, "code": args["code"]},
                         lambda: ({**intent_svc.api_detail(intent_svc.set_status(
                             db, intent_svc.get(db, args["code"], sp.id), "启用", ctx.actor)), "action": "enabled"}))
        if st >= 400:
            raise ApiError(st, body.get("code", "ERROR"), body.get("message", ""), body.get("details"))
        return body
    if name == "intent_disable":
        _need_idem(args)
        sp = resolve_space(request, db, ctx, args.get("space"))
        st, body = _idem(db, ctx, args["idempotency_key"], {"t": name, "space": sp.id, "code": args["code"]},
                         lambda: ({**intent_svc.api_detail(intent_svc.set_status(
                             db, intent_svc.get(db, args["code"], sp.id), "停用", ctx.actor)), "action": "disabled"}))
        if st >= 400:
            raise ApiError(st, body.get("code", "ERROR"), body.get("message", ""), body.get("details"))
        return body
    if name == "create_knowledge_base":
        _need_idem(args)
        payload = {"name": args["name"], "type": args.get("type") or "表格知识库",
                   "description": args.get("description") or ""}
        st, body = _idem(db, ctx, args["idempotency_key"], {"t": name, **payload},
                         lambda: ({**kb_svc.kb_dict(kb_svc.create_kb(
                             db, payload["name"], payload["type"], ctx.actor,
                             description=payload["description"]), admin=True),
                                   "action": "created"}))
        if st >= 400:
            raise ApiError(st, body.get("code", "ERROR"), body.get("message", ""), body.get("details"))
        return body
    if name == "intent_bind_knowledge":
        sp = resolve_space(request, db, ctx, args.get("space"))
        it = intent_svc.get(db, args["code"], sp.id)
        ref = {"kb_id": args["kb_id"], "doc_id": args["doc_id"], "chunk_id": args["chunk_id"],
               "usage": args.get("usage") or "reference"}
        if args.get("dry_run"):
            return {"ok": True, "dry_run": True, "code": it.code, "ref": intent_svc.preview_kb_ref(db, it, ref)}
        _need_idem(args)
        st, body = _idem(db, ctx, args["idempotency_key"], {"t": name, "space": sp.id, "code": it.code, **ref},
                         lambda: (lambda u: {**intent_svc.api_detail(u[0]), "warnings": u[1]})(
                             intent_svc.bind_knowledge(db, it, ref, ctx.actor)))
        if st >= 400:
            raise ApiError(st, body.get("code", "ERROR"), body.get("message", ""), body.get("details"))
        return body
    if name == "intent_unbind_knowledge":
        _need_idem(args)
        sp = resolve_space(request, db, ctx, args.get("space"))
        it = intent_svc.get(db, args["code"], sp.id)
        ref = {"kb_id": args["kb_id"], "doc_id": args["doc_id"], "chunk_id": args["chunk_id"]}
        st, body = _idem(db, ctx, args["idempotency_key"], {"t": name, "space": sp.id, "code": it.code, **ref},
                         lambda: intent_svc.api_detail(intent_svc.unbind_knowledge(db, it, ref, ctx.actor)))
        if st >= 400:
            raise ApiError(st, body.get("code", "ERROR"), body.get("message", ""), body.get("details"))
        return body
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
                                       "serverInfo": {"name": "intent-platform", "version": "0.20.1"}}))
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
