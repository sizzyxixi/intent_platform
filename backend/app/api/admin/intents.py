"""控制台：空间、分类、空间设置、意图、槽位、意图导入、运行时配置。"""
import json

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...db import get_db
from ...deps import Role, get_space_or_404, require
from ...errors import bad_request, unprocessable
from ...models import ImportRecord, Intent, Space
from ...services import importer, intents as intent_svc, runtime, slots as slot_svc, spaces as space_svc
from ...services import taxonomy as tax_svc
from ...services.common import STATUSES, iso
from ...services.testing import describe_ref, run_test
from ...services.validation import RefIndex, validate
from .common import json_body

router = APIRouter(prefix="/api/admin")
VIEW, EDIT, ADMIN = require(Role.VIEWER), require(Role.EDITOR), require(Role.ADMIN)


def _space_full(db: Session, sp: Space) -> dict:
    d = space_svc.space_dict(sp)
    d.update(id=sp.id, version=sp.version, levels=list(sp.levels or []), recognition=space_svc.recognition(sp, True),
             intent_count=db.scalar(select(func.count()).select_from(Intent).where(Intent.space_id == sp.id)),
             enabled_count=db.scalar(select(func.count()).select_from(Intent).where(
                 Intent.space_id == sp.id, Intent.status == "启用")))
    return d


# ---------------- 空间 ----------------
@router.get("/spaces")
def list_spaces(actor=Depends(VIEW), db: Session = Depends(get_db)):
    return [_space_full(db, s) for s in db.scalars(select(Space).order_by(Space.position))]


@router.post("/spaces", status_code=201)
async def create_space(request: Request, actor=Depends(ADMIN), db: Session = Depends(get_db)):
    sp = space_svc.create_space(db, await json_body(request), actor)
    db.commit()
    return _space_full(db, sp)


@router.patch("/spaces/{sid}")
async def patch_space(sid: str, request: Request, actor=Depends(EDIT), db: Session = Depends(get_db)):
    """更新空间名称与描述。bot 字段忽略。"""
    sp = get_space_or_404(db, sid)
    space_svc.update_space(db, sp, await json_body(request), actor)
    db.commit()
    return _space_full(db, sp)


@router.get("/spaces/{sid}/taxonomy")
def get_taxonomy(sid: str, actor=Depends(VIEW), db: Session = Depends(get_db)):
    return tax_svc.admin_taxonomy(db, get_space_or_404(db, sid))


@router.post("/spaces/{sid}/categories", status_code=201)
async def add_category(sid: str, request: Request, actor=Depends(EDIT), db: Session = Depends(get_db)):
    sp = get_space_or_404(db, sid)
    c = tax_svc.add_category(db, sp, await json_body(request), actor)
    db.commit()
    return {"id": c.id, **tax_svc.admin_taxonomy(db, sp)}


@router.patch("/spaces/{sid}/categories/{cid}")
async def update_category(sid: str, cid: int, request: Request, actor=Depends(EDIT), db: Session = Depends(get_db)):
    sp = get_space_or_404(db, sid)
    tax_svc.update_category(db, sp, tax_svc.get_category(db, sp, cid), await json_body(request), actor)
    db.commit()
    return tax_svc.admin_taxonomy(db, sp)


@router.delete("/spaces/{sid}/categories/{cid}")
def delete_category(sid: str, cid: int, actor=Depends(EDIT), db: Session = Depends(get_db)):
    sp = get_space_or_404(db, sid)
    tax_svc.delete_category(db, sp, tax_svc.get_category(db, sp, cid), actor)
    db.commit()
    return tax_svc.admin_taxonomy(db, sp)


@router.post("/spaces/{sid}/levels/preview")
async def preview_levels(sid: str, request: Request, actor=Depends(EDIT), db: Session = Depends(get_db)):
    sp = get_space_or_404(db, sid)
    out = tax_svc.preview_levels(db, sp, await json_body(request))
    db.rollback()
    return out


@router.put("/spaces/{sid}/levels")
async def put_levels(sid: str, request: Request, actor=Depends(EDIT), db: Session = Depends(get_db)):
    sp = get_space_or_404(db, sid)
    out = tax_svc.apply_levels(db, sp, await json_body(request), actor)
    db.commit()
    return {**out, "taxonomy": tax_svc.admin_taxonomy(db, sp)}


@router.get("/spaces/{sid}/recognition")
def get_recognition(sid: str, actor=Depends(VIEW), db: Session = Depends(get_db)):
    return space_svc.recognition(get_space_or_404(db, sid), True)


@router.put("/spaces/{sid}/recognition")
async def put_recognition(sid: str, request: Request, actor=Depends(EDIT), db: Session = Depends(get_db)):
    sp = get_space_or_404(db, sid)
    changed = space_svc.update_recognition(db, sp, await json_body(request), actor)
    db.commit()
    return {**space_svc.recognition(sp, True), "changed": changed}


@router.get("/spaces/{sid}/runtime-config")
def download_runtime(sid: str, actor=Depends(VIEW), db: Session = Depends(get_db)):
    sp = get_space_or_404(db, sid)
    body = runtime.build(db, sp)
    return JSONResponse(body, headers={"Content-Disposition":
                                       f'attachment; filename="runtime-config-{sp.id}-v{sp.version}.json"'})


# ---------------- 意图 ----------------
def _row(it: Intent, idx: RefIndex) -> dict:
    d = intent_svc.api_summary(it)
    d.update(kb_count=len(it.kb_refs or []), tool_count=len(it.tools or []),
             tools=[b["tool"] for b in it.tools or []], owner=it.owner,
             problem_count=len(validate(intent_svc.as_dict(it), idx)) if it.status != "启用" else 0)
    return d


@router.get("/spaces/{sid}/intents")
def list_intents(sid: str, category: int | None = None, status: str | None = None,
                 q: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=500),
                 actor=Depends(VIEW), db: Session = Depends(get_db)):
    sp = get_space_or_404(db, sid)
    if status and status not in STATUSES:
        raise bad_request("status 应为 草稿 / 启用 / 停用")
    cats = None
    if category is not None:
        t = tax_svc.Tax(db, sp)
        if category not in t.nodes:
            raise bad_request("分类不存在", "CATEGORY_NOT_FOUND")
        cats = t.descendants(category)
    keep = intent_svc.preload_categories(db, sid)
    total, rows = intent_svc.list_page(db, sid, page, page_size, category_ids=cats, status=status, q=q)
    idx = RefIndex(db)
    st = dict(db.execute(select(Intent.status, func.count()).where(Intent.space_id == sid).group_by(Intent.status)).all())
    out = {"total": total, "items": [_row(i, idx) for i in rows], "all_count": sum(st.values()), "status_counts": st}
    del keep
    return out


@router.post("/spaces/{sid}/intents", status_code=201)
async def create_intent(sid: str, request: Request, actor=Depends(EDIT), db: Session = Depends(get_db)):
    sp = get_space_or_404(db, sid)
    it = intent_svc.create(db, sp, await json_body(request), actor)
    db.commit()
    return _detail(db, it)


def _detail(db: Session, it: Intent) -> dict:
    idx = RefIndex(db)
    d = intent_svc.admin_detail(it, idx)
    cache = {}
    d["kb_refs"] = [describe_ref(db, r, cache) for r in it.kb_refs or []]
    for r in d["kb_refs"]:
        r["content"] = (r.get("content") or "")[:2000]
    return d


@router.get("/intents/{code}")
def get_intent(code: str, actor=Depends(VIEW), db: Session = Depends(get_db)):
    return _detail(db, intent_svc.get(db, code))


@router.put("/intents/{code}")
async def save_intent(code: str, request: Request, actor=Depends(EDIT), db: Session = Depends(get_db)):
    it = intent_svc.get(db, code)
    it, warnings = intent_svc.update(db, it, await json_body(request), actor)
    db.commit()
    return {**_detail(db, it), "warnings": warnings}


@router.post("/intents/{code}/status")
async def set_status(code: str, request: Request, actor=Depends(EDIT), db: Session = Depends(get_db)):
    b = await json_body(request)
    it = intent_svc.set_status(db, intent_svc.get(db, code), b.get("status"), actor)
    db.commit()
    return _detail(db, it)


@router.post("/intents/{code}/validate")
async def validate_draft(code: str, request: Request, actor=Depends(VIEW), db: Session = Depends(get_db)):
    """校验编辑中的草稿（不保存）。"""
    it = intent_svc.get(db, code)
    b = await json_body(request)
    data = intent_svc.as_dict(it)
    for k in ("name", "category_id", "description", "examples", "slots", "kb_refs", "tools", "handling"):
        if k in b:
            data[k] = b[k]
    idx = RefIndex(db)
    data["slots"] = intent_svc._clean_slots(data["slots"])
    data["kb_refs"] = intent_svc._clean_refs(data["kb_refs"])
    data["tools"] = intent_svc._clean_binds(data["tools"])
    return {"problems": intent_svc.check_structure(db, data, idx) + validate(data, idx)}


@router.post("/spaces/{sid}/test")
async def test_sentence(sid: str, request: Request, actor=Depends(VIEW), db: Session = Depends(get_db)):
    sp = get_space_or_404(db, sid)
    b = await json_body(request)
    text = str(b.get("text") or "").strip()
    if not text:
        raise unprocessable("请输入一句话")
    draft = b.get("draft") if isinstance(b.get("draft"), dict) else None
    if draft:
        draft = {"code": draft.get("code"), "name": draft.get("name") or "", "examples": [str(x) for x in draft.get("examples") or []],
                 "slots": intent_svc._clean_slots(draft.get("slots")), "kb_refs": intent_svc._clean_refs(draft.get("kb_refs")),
                 "tools": intent_svc._clean_binds(draft.get("tools"))}
    return run_test(db, sp, text, draft)


# ---------------- 槽位 ----------------
@router.get("/spaces/{sid}/slots")
def list_slots(sid: str, q: str | None = None, actor=Depends(VIEW), db: Session = Depends(get_db)):
    get_space_or_404(db, sid)
    ub = slot_svc.used_by_map(db, sid)
    names = dict(db.execute(select(Intent.code, Intent.name).where(Intent.space_id == sid)).all())
    out = []
    for s in slot_svc.list_slots(db, sid, q):
        d = slot_svc.to_dict(s, ub.get(s.name, []))
        d["used_by_detail"] = [{"code": c, "name": names.get(c, "")} for c in ub.get(s.name, [])]
        out.append(d)
    return out


@router.post("/spaces/{sid}/slots", status_code=201)
async def create_slot(sid: str, request: Request, actor=Depends(EDIT), db: Session = Depends(get_db)):
    get_space_or_404(db, sid)
    s = slot_svc.create(db, sid, await json_body(request), actor)
    db.commit()
    return slot_svc.to_dict(s, [])


@router.put("/spaces/{sid}/slots/{name}")
async def update_slot(sid: str, name: str, request: Request, actor=Depends(EDIT), db: Session = Depends(get_db)):
    get_space_or_404(db, sid)
    s = slot_svc.update(db, sid, name, await json_body(request), actor)
    db.commit()
    return slot_svc.to_dict(s, slot_svc.used_by_map(db, sid).get(name, []))


@router.delete("/spaces/{sid}/slots/{name}")
def delete_slot(sid: str, name: str, actor=Depends(EDIT), db: Session = Depends(get_db)):
    get_space_or_404(db, sid)
    slot_svc.delete(db, sid, name, actor)
    db.commit()
    return {"ok": True}


# ---------------- 意图导入 ----------------
@router.post("/spaces/{sid}/imports/preview")
async def import_preview(sid: str, request: Request, actor=Depends(EDIT), db: Session = Depends(get_db)):
    sp = get_space_or_404(db, sid)
    form = await request.form()
    files = [f for f in form.getlist("files") if hasattr(f, "read")]
    if not files:
        raise bad_request("请选择文件")
    items, names = [], []
    for f in files:
        raw = await f.read()
        items += importer.parse_file(f.filename or "file", raw)
        names.append(f.filename or "file")
    source = form.get("source") or "文件上传"
    if source not in ("文件上传", "AI 工具产出"):
        source = "文件上传"
    rows = importer.judge(db, sp, items)
    clean = [{k: v for k, v in x.items() if not k.startswith("_")} for x in items]
    return {"file": "、".join(names), "source": source, "items": clean, "rows": _rows_public(rows)}


def _rows_public(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        out.append({k: r.get(k) for k in ("index", "type", "action", "actions", "issues", "reasons", "notes", "diff",
                                          "path_invalid", "path_options", "new_slots")} | {"data": {
            k: r["data"].get(k) for k in ("code", "name", "category_path", "category_id", "description", "examples", "slots",
                                          "ignored_fields")}})
    return out


@router.post("/spaces/{sid}/imports/apply")
async def import_apply(sid: str, request: Request, actor=Depends(EDIT), db: Session = Depends(get_db)):
    sp = get_space_or_404(db, sid)
    b = await json_body(request)
    items = b.get("items")
    if not isinstance(items, list) or not items:
        raise bad_request("items 不能为空")
    items = [importer.normalize_item(x) for x in items if isinstance(x, dict)]
    src = b.get("source") if b.get("source") in ("文件上传", "AI 工具产出") else "文件上传"
    res = importer.apply_file_import(db, sp, items, b.get("choices") or {}, str(b.get("file") or "导入文件")[:200],
                                     src, actor)
    db.commit()
    return res


@router.get("/spaces/{sid}/imports")
def list_imports(sid: str, actor=Depends(VIEW), db: Session = Depends(get_db)):
    get_space_or_404(db, sid)
    recs = db.scalars(select(ImportRecord).where(ImportRecord.kind == "意图", ImportRecord.space_id == sid)
                      .order_by(ImportRecord.at.desc(), ImportRecord.id.desc()))
    return [{"id": r.id, "kind": r.kind, "file": r.file, "source": r.source, "at": iso(r.at), "by": r.by,
             "result": r.result, "status": r.status, "details": r.details} for r in recs]


_ = json
