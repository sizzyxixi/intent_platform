"""开放接口 /api/v1（严格对应 docs/openapi.yaml 0.19.0）。"""
import hashlib
import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...config import get_settings
from ...db import get_db
from ...deps import KeyCtx, api_key, cleanup_idempotency, resolve_space
from ...errors import ApiError, bad_request, forbidden, unprocessable
from ...models import IdempotencyRecord, ImportRecord, Space, Tool
from ...services import importer, intents as intent_svc, knowledge as kb_svc, runtime, slots as slot_svc
from ...services import mcp_servers as mcp_svc, spaces as space_svc, taxonomy as tax_svc, tools as tool_svc
from ...services.common import STATUSES, utcnow
from ...services.validation import RefIndex

router = APIRouter(prefix="/api/v1")
R_INT, W_INT, R_KB, W_KB, R_TOOL, W_TOOL = "意图:读", "意图:写", "知识:读", "知识:写", "工具:读", "工具:写"


async def _json(request: Request) -> Any:
    raw = await request.body()
    if not raw:
        raise bad_request("请求体不能为空")
    try:
        return json.loads(raw)
    except ValueError:
        raise bad_request("请求体不是合法 JSON")


def _hash(request: Request, payload: Any) -> str:
    s = request.method + " " + request.url.path + "?" + str(request.url.query) + "\n" + \
        json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(s.encode()).hexdigest()


def idempotent(request: Request, db: Session, ctx: KeyCtx, payload: Any, fn) -> Response:
    key = request.headers.get("idempotency-key")
    if not key:
        raise bad_request("写请求必须带 Idempotency-Key 请求头", "IDEMPOTENCY_KEY_REQUIRED")
    if len(key) > 64:
        raise bad_request("Idempotency-Key 最长 64 个字符", "IDEMPOTENCY_KEY_INVALID")
    h = _hash(request, payload)
    cleanup_idempotency(db)
    rec = db.scalar(select(IdempotencyRecord).where(IdempotencyRecord.api_key_id == ctx.key.id,
                                                    IdempotencyRecord.key == key))
    if rec:
        if rec.request_hash != h:
            raise ApiError(409, "IDEMPOTENCY_CONFLICT", "该 Idempotency-Key 已用于不同的请求体，请换一个新的 Key")
        if rec.status_code == 204:
            return Response(status_code=204, headers={"Idempotent-Replayed": "true"})
        return JSONResponse(rec.response_body, status_code=rec.status_code, headers={"Idempotent-Replayed": "true"})
    try:
        status, body = fn()
        db.commit()
    except ApiError as e:
        db.rollback()
        if e.status in (401, 429) or e.status >= 500:
            raise
        status, body = e.status, e.body(getattr(request.state, "request_id", None))
    db.add(IdempotencyRecord(api_key_id=ctx.key.id, key=key, request_hash=h, status_code=status, response_body=body))
    db.commit()
    if status == 204:
        return Response(status_code=204)
    return JSONResponse(body, status_code=status)


# ---------------- 空间 / 分类 / 设置 ----------------
@router.get("/spaces")
def list_spaces(ctx: KeyCtx = Depends(api_key(R_INT)), db: Session = Depends(get_db)):
    sps = db.scalars(select(Space).where(Space.id.in_(ctx.key.spaces or [])).order_by(Space.position))
    return [space_svc.space_dict(s) for s in sps]


@router.get("/taxonomy")
def get_taxonomy(request: Request, ctx: KeyCtx = Depends(api_key(R_INT)), db: Session = Depends(get_db)):
    sp = resolve_space(request, db, ctx)
    return tax_svc.public_taxonomy(db, sp)



@router.post("/taxonomy/nodes")
async def upsert_taxonomy_node(request: Request, ctx: KeyCtx = Depends(api_key(W_INT)),
                               db: Session = Depends(get_db)):
    """按编码创建或更新分类节点。dry_run=true 只校验不写入。需要「意图:写」，且 Key 必须绑定该空间。"""
    payload = await _json(request)
    if not isinstance(payload, dict):
        raise bad_request("请求体应为对象")
    sp = resolve_space(request, db, ctx)
    if payload.get("dry_run"):
        existing, v = tax_svc.validate_node(db, sp, payload)
        return {"ok": True, "dry_run": True, "action": "updated" if existing else "created", "code": v["code"]}

    def run():
        c, action = tax_svc.upsert_node(db, sp, payload, ctx.actor)
        db.flush()
        status = 200 if action == "updated" else 201
        return status, {**tax_svc.node_dict(c, tax_svc.Tax(db, sp)), "action": action}
    return idempotent(request, db, ctx, {"space": sp.id, **payload}, run)


@router.put("/taxonomy/nodes/{code}")
async def update_taxonomy_node(code: str, request: Request, ctx: KeyCtx = Depends(api_key(W_INT)),
                               db: Session = Depends(get_db)):
    payload = await _json(request)
    if not isinstance(payload, dict):
        raise bad_request("请求体应为对象")
    payload = {**payload, "code": code}
    sp = resolve_space(request, db, ctx)
    if payload.get("dry_run"):
        existing, v = tax_svc.validate_node(db, sp, payload)
        if not existing:
            raise unprocessable(f"分类「{code}」不存在，先调用 POST /api/v1/taxonomy/nodes 创建", "CATEGORY_NOT_FOUND")
        return {"ok": True, "dry_run": True, "action": "updated", "code": v["code"]}

    def run():
        existing, _ = tax_svc.validate_node(db, sp, payload)
        if not existing:
            raise unprocessable(f"分类「{code}」不存在，先调用 POST /api/v1/taxonomy/nodes 创建", "CATEGORY_NOT_FOUND")
        c, action = tax_svc.upsert_node(db, sp, payload, ctx.actor)
        db.flush()
        return 200, {**tax_svc.node_dict(c, tax_svc.Tax(db, sp)), "action": action}
    return idempotent(request, db, ctx, {"space": sp.id, **payload}, run)


@router.post("/taxonomy/nodes/{code}:disable")
async def disable_taxonomy_node(code: str, request: Request, ctx: KeyCtx = Depends(api_key(W_INT)),
                                db: Session = Depends(get_db)):
    raw = await request.body()
    payload = json.loads(raw) if raw else {}
    if payload and not isinstance(payload, dict):
        raise bad_request("请求体应为对象")
    sp = resolve_space(request, db, ctx)

    def run():
        c = tax_svc.set_node_enabled(db, sp, code, False, ctx.actor)
        db.flush()
        return 200, {**tax_svc.node_dict(c, tax_svc.Tax(db, sp)), "action": "disabled"}
    return idempotent(request, db, ctx, {"space": sp.id, "code": code, "op": "disable", **(payload or {})}, run)


@router.get("/recognition")
def get_recognition(request: Request, ctx: KeyCtx = Depends(api_key(R_INT)), db: Session = Depends(get_db)):
    sp = resolve_space(request, db, ctx)
    r = space_svc.recognition(sp)
    r["version"] = sp.version
    return r


@router.put("/recognition")
async def put_recognition(request: Request, ctx: KeyCtx = Depends(api_key(W_INT)), db: Session = Depends(get_db)):
    payload = await _json(request)
    if not isinstance(payload, dict):
        raise bad_request("请求体应为对象")
    sp = resolve_space(request, db, ctx)

    def run():
        space_svc.update_recognition(db, sp, payload, ctx.actor)
        db.flush()
        r = space_svc.recognition(sp)
        r["version"] = sp.version
        return 200, r
    return idempotent(request, db, ctx, payload, run)


# ---------------- 槽位 ----------------
def _as400(e: ApiError) -> ApiError:
    """openapi 中槽位写接口的内容校验错误为 400。"""
    if e.status == 422:
        return ApiError(400, "BAD_REQUEST" if e.code == "VALIDATION_FAILED" else e.code, e.message, e.details)
    return e


@router.get("/slots")
def list_slots(request: Request, q: str | None = None, ctx: KeyCtx = Depends(api_key(R_INT)),
               db: Session = Depends(get_db)):
    sp = resolve_space(request, db, ctx)
    ub = slot_svc.used_by_map(db, sp.id)
    return [slot_svc.to_dict(s, ub.get(s.name, [])) for s in slot_svc.list_slots(db, sp.id, q)]


@router.post("/slots", status_code=201)
async def create_slot(request: Request, ctx: KeyCtx = Depends(api_key(W_INT)), db: Session = Depends(get_db)):
    payload = await _json(request)
    sp = resolve_space(request, db, ctx)

    def run():
        if not isinstance(payload, dict):
            raise bad_request("请求体应为对象")
        try:
            s = slot_svc.create(db, sp.id, payload, ctx.actor)
        except ApiError as e:
            raise _as400(e)
        db.flush()
        return 201, slot_svc.to_dict(s, [])
    return idempotent(request, db, ctx, payload, run)


@router.put("/slots/{name}")
async def update_slot(name: str, request: Request, ctx: KeyCtx = Depends(api_key(W_INT)),
                      db: Session = Depends(get_db)):
    payload = await _json(request)
    sp = resolve_space(request, db, ctx)

    def run():
        try:
            s = slot_svc.update(db, sp.id, name, payload if isinstance(payload, dict) else {}, ctx.actor)
        except ApiError as e:
            raise _as400(e)
        db.flush()
        return 200, slot_svc.to_dict(s, slot_svc.used_by_map(db, sp.id).get(name, []))
    return idempotent(request, db, ctx, payload, run)


@router.delete("/slots/{name}", status_code=204)
def delete_slot(name: str, request: Request, ctx: KeyCtx = Depends(api_key(W_INT)), db: Session = Depends(get_db)):
    sp = resolve_space(request, db, ctx)

    def run():
        slot_svc.delete(db, sp.id, name, ctx.actor)
        return 204, None
    return idempotent(request, db, ctx, {"delete": name, "space": sp.id}, run)


# ---------------- 意图 ----------------
@router.get("/intents")
def list_intents(request: Request, category: str | None = None, l1: str | None = None, l2: str | None = None,
                 status: str | None = None,
                 q: str | None = None, updated_after: datetime | None = None,
                 page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100),
                 ctx: KeyCtx = Depends(api_key(R_INT)), db: Session = Depends(get_db)):
    if status and status not in STATUSES:
        raise bad_request("status 应为 草稿 / 启用 / 停用")
    sp = resolve_space(request, db, ctx)
    keep = intent_svc.preload_categories(db, sp.id)
    total, rows = intent_svc.list_page(db, sp.id, page, page_size,
                                       category_ids=intent_svc.path_filter(db, sp, category, l1, l2), status=status,
                                       q=q, updated_after=updated_after)
    out = {"total": total, "items": [intent_svc.api_summary(i) for i in rows]}
    del keep
    return out


@router.get("/intents/{code}")
def get_intent(code: str, request: Request, ctx: KeyCtx = Depends(api_key(R_INT)), db: Session = Depends(get_db)):
    sp = resolve_space(request, db, ctx)
    return intent_svc.api_detail(intent_svc.get(db, code, sp.id))


@router.patch("/intents/{code}")
async def patch_intent(code: str, request: Request, ctx: KeyCtx = Depends(api_key(W_INT)),
                       db: Session = Depends(get_db)):
    payload = await _json(request)
    sp = resolve_space(request, db, ctx)

    def run():
        if not isinstance(payload, dict):
            raise bad_request("请求体应为对象")
        allowed = {"name", "description", "examples", "slots", "status", "handling"}
        extra = [k for k in payload if k not in allowed and k not in importer.IGNORED_KEYS]
        if extra:
            raise bad_request("不支持修改的字段：" + "、".join(extra), fields=extra)
        it = intent_svc.get(db, code, sp.id)
        if payload.get("status") == "启用":
            raise forbidden("请使用 POST /api/v1/intents/{code}:enable 启用意图，PATCH 不能把意图设为启用", "ENABLE_FORBIDDEN")
        if "status" in payload and payload["status"] not in ("草稿", "停用"):
            raise bad_request("status 只能是 草稿 / 停用")
        if it.status == "启用":
            raise forbidden("已启用意图不可通过接口修改", "ENABLED_PROTECTED")
        errs = []
        if "name" in payload and not str(payload.get("name") or "").strip():
            errs.append("名称不能为空")
        if "description" in payload and len(str(payload.get("description") or "").strip()) < 15:
            errs.append("意图描述至少 15 个字")
        if "examples" in payload and (not isinstance(payload["examples"], list) or
                                      len([x for x in payload["examples"] if str(x).strip()]) < 3):
            errs.append("例句至少 3 条")
        defs = {s.name: s for s in slot_svc.list_slots(db, sp.id)}
        for s in payload.get("slots") or []:
            if not isinstance(s, dict) or s.get("name") not in defs:
                errs.append(f"槽位「{(s or {}).get('name') if isinstance(s, dict) else s}」不在本空间槽位列表中，请先 POST /api/v1/slots")
            elif s.get("required") and not (s.get("ask") or defs[s["name"]].default_ask):
                errs.append(f"必填槽位 {s['name']} 缺少追问话术")
        if errs:
            raise unprocessable("内容校验不通过", problems=errs)
        data = {k: v for k, v in payload.items() if k in allowed}
        it, _ = intent_svc.update(db, it, data, ctx.actor)
        rec = ImportRecord(id=importer.next_import_id(db), kind="意图", space_id=sp.id, file=f"PATCH {code}",
                           source="AI 工具（API）", by=ctx.actor.name, result="更新 1", status="构建成功",
                           details={"counts": {"更新": 1}})
        db.add(rec)
        db.flush()
        return 200, intent_svc.api_detail(it)
    return idempotent(request, db, ctx, payload, run)


@router.post("/intents:batchUpsert")
async def batch_upsert(request: Request, ctx: KeyCtx = Depends(api_key(W_INT)), db: Session = Depends(get_db)):
    payload = await _json(request)
    sp = resolve_space(request, db, ctx)

    def run():
        if not isinstance(payload, dict):
            raise bad_request("请求体应为对象 {mode, intents}")
        res = importer.batch_upsert(db, sp, payload, ctx.actor)
        db.flush()
        return 200, res
    return idempotent(request, db, ctx, payload, run)



@router.post("/batchUpsert")
async def batch_apply(request: Request, ctx: KeyCtx = Depends(api_key(W_INT)), db: Session = Depends(get_db)):
    """一次性写入分类节点和意图。默认全部成功才提交；任一失败回滚并在 details.items 中按条返回错误。"""
    payload = await _json(request)
    if not isinstance(payload, dict):
        raise bad_request("请求体应为对象")
    sp = resolve_space(request, db, ctx)
    if payload.get("dry_run"):
        return importer.batch_apply(db, sp, payload, ctx.actor)

    def run():
        res = importer.batch_apply(db, sp, payload, ctx.actor)
        db.flush()
        return 200, res
    return idempotent(request, db, ctx, {"space": sp.id, **payload}, run)


@router.post("/intents/{code}:enable")
async def enable_intent(code: str, request: Request, ctx: KeyCtx = Depends(api_key(W_INT)),
                        db: Session = Depends(get_db)):
    raw = await request.body()
    payload = json.loads(raw) if raw else {}
    if payload and not isinstance(payload, dict):
        raise bad_request("请求体应为对象")
    sp = resolve_space(request, db, ctx)

    def run():
        it = intent_svc.set_status(db, intent_svc.get(db, code, sp.id), "启用", ctx.actor)
        db.flush()
        return 200, {**intent_svc.api_detail(it), "action": "enabled"}
    return idempotent(request, db, ctx, {"space": sp.id, "code": code, "op": "enable", **(payload or {})}, run)


@router.post("/intents/{code}:disable")
async def disable_intent(code: str, request: Request, ctx: KeyCtx = Depends(api_key(W_INT)),
                         db: Session = Depends(get_db)):
    raw = await request.body()
    payload = json.loads(raw) if raw else {}
    if payload and not isinstance(payload, dict):
        raise bad_request("请求体应为对象")
    sp = resolve_space(request, db, ctx)

    def run():
        it = intent_svc.set_status(db, intent_svc.get(db, code, sp.id), "停用", ctx.actor)
        db.flush()
        return 200, {**intent_svc.api_detail(it), "action": "disabled"}
    return idempotent(request, db, ctx, {"space": sp.id, "code": code, "op": "disable", **(payload or {})}, run)


# ---------------- 知识库（全局） ----------------
@router.get("/kbs")
def list_kbs(ctx: KeyCtx = Depends(api_key(R_KB)), db: Session = Depends(get_db)):
    return [kb_svc.kb_dict(k) for k in kb_svc.list_kbs(db)]


@router.post("/kbs", status_code=201)
async def create_kb(request: Request, ctx: KeyCtx = Depends(api_key(W_KB)), db: Session = Depends(get_db)):
    payload = await _json(request)
    if not isinstance(payload, dict):
        raise bad_request("请求体应为对象")

    def run():
        kb = kb_svc.create_kb(db, payload.get("name"), payload.get("type") or "表格知识库",
                              ctx.actor, description=payload.get("description") or "")
        db.flush()
        return 201, {**kb_svc.kb_dict(kb, admin=True), "action": "created"}
    return idempotent(request, db, ctx, payload, run)


@router.get("/kbs/{kb_id}")
def get_kb(kb_id: str, ctx: KeyCtx = Depends(api_key(R_KB)), db: Session = Depends(get_db)):
    kb = kb_svc.get_kb(db, kb_id)
    d = kb_svc.kb_dict(kb, admin=True)
    d["documents"] = [kb_svc.doc_dict(kb, x) for x in kb.documents]
    return d


@router.patch("/kbs/{kb_id}")
async def patch_kb(kb_id: str, request: Request, ctx: KeyCtx = Depends(api_key(W_KB)),
                   db: Session = Depends(get_db)):
    payload = await _json(request)
    if not isinstance(payload, dict):
        raise bad_request("请求体应为对象")

    def run():
        kb = kb_svc.update_kb(db, kb_svc.get_kb(db, kb_id), payload, ctx.actor)
        db.flush()
        return 200, {**kb_svc.kb_dict(kb, admin=True), "action": "updated"}
    return idempotent(request, db, ctx, {"kb_id": kb_id, **payload}, run)


@router.get("/kbs/{kb_id}/documents")
def list_documents(kb_id: str, name: str | None = None, ctx: KeyCtx = Depends(api_key(R_KB)),
                   db: Session = Depends(get_db)):
    kb = kb_svc.get_kb(db, kb_id)
    return [kb_svc.doc_dict(kb, d) for d in kb.documents if not name or name.lower() in d.name.lower()]


@router.post("/kbs/{kb_id}/documents:import", status_code=202)
async def import_documents(kb_id: str, request: Request, background: BackgroundTasks,
                           ctx: KeyCtx = Depends(api_key(W_KB)), db: Session = Depends(get_db)):
    ctype = request.headers.get("content-type", "")
    if "multipart/form-data" not in ctype:
        raise bad_request("请使用 multipart/form-data 上传 files", "BAD_CONTENT_TYPE")
    form = await request.form()
    files = []
    for f in form.getlist("files"):
        if hasattr(f, "read"):
            files.append((f.filename or "file", await f.read()))
    ow = str(form.get("overwrite") or "false").lower() in ("true", "1", "yes")
    payload = {"files": [[n, hashlib.sha256(b).hexdigest()] for n, b in files], "overwrite": ow, "kb": kb_id}
    jobs = []

    def run():
        kb = kb_svc.get_kb(db, kb_id)
        task, job = kb_svc.import_documents(db, kb, files, ow, ctx.actor, source="AI 工具（API）")
        db.flush()
        jobs.append(job)
        return 202, task
    resp = idempotent(request, db, ctx, payload, run)
    for j in jobs:
        if j["files"]:
            background.add_task(kb_svc.run_build, j)
    return resp


@router.get("/kbs/{kb_id}/chunks")
def list_chunks(kb_id: str, doc_id: str | None = None, q: str | None = None, page: int = Query(1, ge=1),
                page_size: int = Query(50, ge=1, le=100), ctx: KeyCtx = Depends(api_key(R_KB)),
                db: Session = Depends(get_db)):
    kb = kb_svc.get_kb(db, kb_id)
    total, items = kb_svc.list_chunks(db, kb, doc_id, q, page, page_size)
    return {"total": total, "items": items}


@router.put("/kbs/{kb_id}/chunks/{chunk_id}")
async def update_chunk(kb_id: str, chunk_id: str, request: Request, ctx: KeyCtx = Depends(api_key(W_KB)),
                       db: Session = Depends(get_db)):
    payload = await _json(request)

    def run():
        if not isinstance(payload, dict):
            raise bad_request("请求体应为对象")
        kb = kb_svc.get_kb(db, kb_id)
        c = kb_svc.update_chunk(db, kb, chunk_id, payload, ctx.actor)
        db.flush()
        lm = kb_svc.linked_map(db, kb)
        return 200, kb_svc.chunk_dict(kb, c, c.document, lm.get(chunk_id, []))
    return idempotent(request, db, ctx, payload, run)


@router.post("/kbs/{kb_id}/search")
async def search(kb_id: str, request: Request, ctx: KeyCtx = Depends(api_key(R_KB)), db: Session = Depends(get_db)):
    p = await _json(request)
    if not isinstance(p, dict) or not isinstance(p.get("query"), str):
        raise bad_request("query 必填")
    for k in ("top_k",):
        if k in p and (not isinstance(p[k], int) or isinstance(p[k], bool)):
            raise bad_request("top_k 应为整数")
    if "threshold" in p and not isinstance(p["threshold"], (int, float)):
        raise bad_request("threshold 应为数字")
    return kb_svc.search(db, kb_id, p["query"], p, p.get("top_k"), p.get("threshold"))


@router.get("/imports/{import_id}")
def get_import(import_id: str, ctx: KeyCtx = Depends(api_key(R_KB)), db: Session = Depends(get_db)):
    rec = db.get(ImportRecord, import_id)
    if not rec:
        raise ApiError(404, "IMPORT_NOT_FOUND", f"导入任务 {import_id} 不存在")
    return kb_svc.import_task(rec)


# ---------------- 工具（全局） ----------------
@router.get("/tools")
def list_tools(source: str | None = Query(None, pattern="^(external|builtin)$"),
               ctx: KeyCtx = Depends(api_key(R_TOOL)), db: Session = Depends(get_db)):
    return [tool_svc.api_dict(t) for t in tool_svc.list_tools(db, source)]


@router.get("/tools/{name}/skill-package")
def skill_package(name: str, version: int | None = Query(None, ge=1), ctx: KeyCtx = Depends(api_key(R_TOOL)),
                  db: Session = Depends(get_db)):
    """下载技能包（zip，路径相对包根，含 SKILL.md）。Bot 按运行时配置里 tools[].skill.version 缓存。"""
    from ...services import skills as skill_svc
    t = tool_svc.get(db, name)
    if t.type != "skill":
        raise bad_request(f"工具 {name} 不是技能", "NOT_A_SKILL")
    pkg, data = skill_svc.archive(db, name, version)
    return Response(data, media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="{name}-v{pkg.version}.zip"', "X-Skill-Version": str(pkg.version)})


@router.get("/tools/{name}")
def get_tool(name: str, ctx: KeyCtx = Depends(api_key(R_TOOL)), db: Session = Depends(get_db)):
    return tool_svc.api_dict(tool_svc.get(db, name))


def _tool_write_result(t, action: str) -> dict:
    return {**tool_svc.api_dict(t), "action": action}


@router.post("/tools")
async def upsert_tool(request: Request, ctx: KeyCtx = Depends(api_key(W_TOOL)), db: Session = Depends(get_db)):
    """按工具名创建或更新（MCP 子工具除外，请登记 MCP 服务后同步）。dry_run=true 只校验不写入。"""
    payload = await _json(request)
    if not isinstance(payload, dict):
        raise bad_request("请求体应为对象")

    def _validate_only():
        name = str(payload.get("name") or "").strip()
        existing = db.get(Tool, name) if name else None
        tool_svc.validate_payload(payload, existing is None, existing)
        return 200, {"ok": True, "dry_run": True, "action": "updated" if existing else "created", "name": name}

    if payload.get("dry_run"):
        return _validate_only()[1]

    def run():
        t, action = tool_svc.upsert(db, payload, ctx.actor)
        db.flush()
        return (200 if action == "updated" else 201), _tool_write_result(t, action)
    return idempotent(request, db, ctx, {k: v for k, v in payload.items() if k != "secret"}, run)


@router.put("/tools/{name}")
async def update_tool(name: str, request: Request, ctx: KeyCtx = Depends(api_key(W_TOOL)),
                     db: Session = Depends(get_db)):
    payload = await _json(request)
    if not isinstance(payload, dict):
        raise bad_request("请求体应为对象")
    payload = {**payload, "name": name}
    if payload.get("dry_run"):
        existing = tool_svc.get(db, name)
        tool_svc.validate_payload(payload, False, existing)
        return {"ok": True, "dry_run": True, "action": "updated", "name": name}

    def run():
        t = tool_svc.update(db, name, payload, ctx.actor)
        db.flush()
        return 200, _tool_write_result(t, "updated")
    return idempotent(request, db, ctx, {k: v for k, v in payload.items() if k != "secret"}, run)


@router.post("/tools/{name}:disable")
async def disable_tool(name: str, request: Request, ctx: KeyCtx = Depends(api_key(W_TOOL)),
                      db: Session = Depends(get_db)):
    raw = await request.body()
    payload = json.loads(raw) if raw else {}
    if payload and not isinstance(payload, dict):
        raise bad_request("请求体应为对象")
    payload = payload or {"name": name}

    def run():
        t = tool_svc.set_enabled(db, name, False, ctx.actor)
        db.flush()
        return 200, _tool_write_result(t, "disabled")
    return idempotent(request, db, ctx, payload, run)


@router.post("/skill-packages", status_code=201)
async def upload_skill_package(request: Request, ctx: KeyCtx = Depends(api_key(W_TOOL)),
                               db: Session = Depends(get_db)):
    """上传技能包（multipart files 或单个 zip），返回暂存 package_id，再在 POST /tools 里引用。"""
    from ...services import skills as skill_svc
    form = await request.form()
    ups = [f for f in form.getlist("files") if hasattr(f, "read")]
    paths = [str(x) for x in form.getlist("paths")]
    files = []
    for i, f in enumerate(ups):
        name = paths[i] if i < len(paths) and paths[i] else (f.filename or "file")
        files.append((name, await f.read()))
    pkg = skill_svc.stage(db, files, ctx.actor)
    db.commit()
    return skill_svc.staged_dict(pkg)


@router.post("/mcp-servers", status_code=201)
async def create_mcp_server(request: Request, ctx: KeyCtx = Depends(api_key(W_TOOL)),
                            db: Session = Depends(get_db)):
    payload = await _json(request)
    if not isinstance(payload, dict):
        raise bad_request("请求体应为对象")

    def run():
        sv = mcp_svc.create(db, payload, ctx.actor)
        warnings = getattr(sv, "warnings", [])
        db.flush()
        return 201, {**mcp_svc.admin_dict(db, sv), "warnings": warnings, "action": "created"}
    return idempotent(request, db, ctx, {k: v for k, v in payload.items() if k != "secret"}, run)


@router.put("/mcp-servers/{name}")
async def update_mcp_server(name: str, request: Request, ctx: KeyCtx = Depends(api_key(W_TOOL)),
                            db: Session = Depends(get_db)):
    payload = await _json(request)
    if not isinstance(payload, dict):
        raise bad_request("请求体应为对象")

    def run():
        sv = mcp_svc.update(db, name, payload, ctx.actor)
        warnings = getattr(sv, "warnings", [])
        db.flush()
        return 200, {**mcp_svc.admin_dict(db, sv), "warnings": warnings, "action": "updated"}
    return idempotent(request, db, ctx, {k: v for k, v in payload.items() if k != "secret"}, run)


@router.post("/mcp-servers/{name}/sync/preview")
async def preview_mcp_sync(name: str, request: Request, ctx: KeyCtx = Depends(api_key(W_TOOL)),
                           db: Session = Depends(get_db)):
    from fastapi.concurrency import run_in_threadpool
    body = await _json(request)
    sv = mcp_svc.get(db, name)
    return await run_in_threadpool(mcp_svc.preview, db, sv, body if isinstance(body, dict) else {}, ctx.actor)


@router.post("/mcp-servers/{name}/sync/apply")
async def apply_mcp_sync(name: str, request: Request, ctx: KeyCtx = Depends(api_key(W_TOOL)),
                         db: Session = Depends(get_db)):
    payload = await _json(request)
    if not isinstance(payload, dict):
        raise bad_request("请求体应为对象")

    def run():
        sv = mcp_svc.get(db, name)
        res = mcp_svc.apply(db, sv, payload, ctx.actor)
        db.flush()
        return 200, {**res, "server": mcp_svc.admin_dict(db, sv)}
    return idempotent(request, db, ctx, payload, run)


@router.post("/intents/{code}/tools")
async def bind_intent_tool(code: str, request: Request, ctx: KeyCtx = Depends(api_key(W_INT)),
                           db: Session = Depends(get_db)):
    """绑定或更新意图的工具与 param_map。需要「意图:写」（只绑定已有工具，不需要「工具:写」）。"""
    payload = await _json(request)
    if not isinstance(payload, dict):
        raise bad_request("请求体应为对象")
    sp = resolve_space(request, db, ctx)
    it = intent_svc.get(db, code, sp.id)
    if payload.get("dry_run"):
        cleaned = intent_svc.preview_bind(db, it, payload)
        return {"ok": True, "dry_run": True, "code": it.code, "bind": cleaned}

    def run():
        updated, warnings = intent_svc.bind_tool(db, it, payload, ctx.actor)
        db.flush()
        return 200, {**intent_svc.api_detail(updated), "warnings": warnings}
    return idempotent(request, db, ctx, {"code": code, "space": sp.id, **payload}, run)


@router.delete("/intents/{code}/tools/{tool}")
def unbind_intent_tool(code: str, tool: str, request: Request, ctx: KeyCtx = Depends(api_key(W_INT)),
                       db: Session = Depends(get_db)):
    sp = resolve_space(request, db, ctx)
    it = intent_svc.get(db, code, sp.id)

    def run():
        updated = intent_svc.unbind_tool(db, it, tool, ctx.actor)
        db.flush()
        return 200, intent_svc.api_detail(updated)
    return idempotent(request, db, ctx, {"code": code, "space": sp.id, "tool": tool, "op": "unbind"}, run)



@router.post("/intents/{code}/knowledge")
async def bind_intent_knowledge(code: str, request: Request, ctx: KeyCtx = Depends(api_key(W_INT, W_KB)),
                                db: Session = Depends(get_db)):
    """给意图关联知识。需要「意图:写」和「知识:写」。"""
    payload = await _json(request)
    if not isinstance(payload, dict):
        raise bad_request("请求体应为对象")
    sp = resolve_space(request, db, ctx)
    it = intent_svc.get(db, code, sp.id)
    if payload.get("dry_run"):
        cleaned = intent_svc.preview_kb_ref(db, it, payload)
        return {"ok": True, "dry_run": True, "code": it.code, "ref": cleaned}

    def run():
        updated, warnings = intent_svc.bind_knowledge(db, it, payload, ctx.actor)
        db.flush()
        return 200, {**intent_svc.api_detail(updated), "warnings": warnings}
    return idempotent(request, db, ctx, {"code": code, "space": sp.id, **payload}, run)


@router.delete("/intents/{code}/knowledge")
def unbind_intent_knowledge(code: str, request: Request, ctx: KeyCtx = Depends(api_key(W_INT, W_KB)),
                            db: Session = Depends(get_db),
                            kb_id: str | None = Query(None), doc_id: str | None = Query(None),
                            chunk_id: str | None = Query(None)):
    sp = resolve_space(request, db, ctx)
    it = intent_svc.get(db, code, sp.id)
    ref = {"kb_id": kb_id, "doc_id": doc_id, "chunk_id": chunk_id}

    def run():
        updated = intent_svc.unbind_knowledge(db, it, ref, ctx.actor)
        db.flush()
        return 200, intent_svc.api_detail(updated)
    return idempotent(request, db, ctx, {"code": code, "space": sp.id, **ref, "op": "unbind_kb"}, run)


# ---------------- 运行时配置 ----------------
@router.get("/runtime-config")
def runtime_config(request: Request, since_version: int | None = Query(None, ge=0),
                   ctx: KeyCtx = Depends(api_key(R_INT)), db: Session = Depends(get_db)):
    sp = resolve_space(request, db, ctx)
    if since_version is not None and since_version == sp.version:
        return Response(status_code=304, headers={"ETag": f'"{sp.id}-v{sp.version}"'})
    return JSONResponse(runtime.build(db, sp), headers={"ETag": f'"{sp.id}-v{sp.version}"'})


_ = (get_settings, utcnow, RefIndex)
