"""开放接口 /api/v1（严格对应 docs/openapi.yaml 0.5.0）。"""
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
from ...models import IdempotencyRecord, ImportRecord, Space
from ...services import importer, intents as intent_svc, knowledge as kb_svc, runtime, slots as slot_svc
from ...services import spaces as space_svc, tools as tool_svc
from ...services.common import STATUSES, utcnow
from ...services.validation import RefIndex

router = APIRouter(prefix="/api/v1")
R_INT, W_INT, R_KB, W_KB, R_TOOL = "意图:读", "意图:写", "知识:读", "知识:写", "工具:读"


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
    return space_svc.taxonomy_dict(db, sp.id)


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
def list_intents(request: Request, l1: str | None = None, l2: str | None = None, status: str | None = None,
                 q: str | None = None, updated_after: datetime | None = None,
                 page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100),
                 ctx: KeyCtx = Depends(api_key(R_INT)), db: Session = Depends(get_db)):
    if status and status not in STATUSES:
        raise bad_request("status 应为 草稿 / 启用 / 停用")
    sp = resolve_space(request, db, ctx)
    total, rows = intent_svc.list_page(db, sp.id, page, page_size, l1=l1, l2=l2, status=status, q=q,
                                       updated_after=updated_after)
    return {"total": total, "items": [intent_svc.api_summary(i) for i in rows]}


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
        allowed = {"name", "description", "examples", "slots", "status"}
        extra = [k for k in payload if k not in allowed and k not in importer.IGNORED_KEYS]
        if extra:
            raise bad_request("不支持修改的字段：" + "、".join(extra), fields=extra)
        it = intent_svc.get(db, code, sp.id)
        if payload.get("status") == "启用":
            raise forbidden("启用由运营在平台上完成，接口不可把意图设为启用", "ENABLE_FORBIDDEN")
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


# ---------------- 知识库（全局） ----------------
@router.get("/kbs")
def list_kbs(ctx: KeyCtx = Depends(api_key(R_KB)), db: Session = Depends(get_db)):
    return [kb_svc.kb_dict(k) for k in kb_svc.list_kbs(db)]


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


# ---------------- 运行时配置 ----------------
@router.get("/runtime-config")
def runtime_config(request: Request, since_version: int | None = Query(None, ge=0),
                   ctx: KeyCtx = Depends(api_key(R_INT)), db: Session = Depends(get_db)):
    sp = resolve_space(request, db, ctx)
    if since_version is not None and since_version == sp.version:
        return Response(status_code=304, headers={"ETag": f'"{sp.id}-v{sp.version}"'})
    return JSONResponse(runtime.build(db, sp), headers={"ETag": f'"{sp.id}-v{sp.version}"'})


_ = (get_settings, utcnow, RefIndex)
