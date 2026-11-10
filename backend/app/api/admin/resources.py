"""控制台：知识库（全局）、工具中心（全局）、API Key、接入文档。"""
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from ...db import get_db
from ...deps import SCOPES, Role, require
from ...errors import bad_request, not_found
from ...models import ImportRecord, McpServer, Space
from ...services import intents as intent_svc, keys as key_svc, knowledge as kb_svc, tools as tool_svc
from ...services import mcp_servers as mcp_svc
from ...services.common import iso
from .common import json_body

router = APIRouter(prefix="/api/admin")
VIEW, EDIT, ADMIN = require(Role.VIEWER), require(Role.EDITOR), require(Role.ADMIN)
DOCS_DIR = Path(__file__).resolve().parents[4] / "docs"


def _intent_refs(db: Session, users) -> list[dict]:
    sp = kb_svc.space_names(db)
    return [{"code": u.code, "name": u.name, "status": u.status, "space": u.space_id,
             "space_name": sp.get(u.space_id, u.space_id)} for u in users]


# ---------------- 知识库 ----------------
@router.get("/kbs")
def list_kbs(name: str | None = None, actor=Depends(VIEW), db: Session = Depends(get_db)):
    out = []
    for kb in kb_svc.list_kbs(db, name):
        d = kb_svc.kb_dict(kb, admin=True)
        d["linked_count"] = len(intent_svc.users_of_kb(db, kb.id))
        out.append(d)
    return out


@router.post("/kbs", status_code=201)
async def create_kb(request: Request, actor=Depends(EDIT), db: Session = Depends(get_db)):
    b = await json_body(request)
    kb = kb_svc.create_kb(db, b.get("name"), b.get("type"), b.get("tags") or {}, actor)
    db.commit()
    return kb_svc.kb_dict(kb_svc.get_kb(db, kb.id), admin=True)


@router.get("/kbs/{kb_id}")
def get_kb(kb_id: str, actor=Depends(VIEW), db: Session = Depends(get_db)):
    kb = kb_svc.get_kb(db, kb_id)
    d = kb_svc.kb_dict(kb, admin=True)
    d["documents"] = [kb_svc.doc_dict(kb, x) for x in kb.documents]
    d["linked"] = _intent_refs(db, intent_svc.users_of_kb(db, kb.id))
    return d


@router.patch("/kbs/{kb_id}")
async def update_kb(kb_id: str, request: Request, actor=Depends(EDIT), db: Session = Depends(get_db)):
    kb = kb_svc.update_kb(db, kb_svc.get_kb(db, kb_id), await json_body(request), actor)
    db.commit()
    return get_kb(kb.id, actor, db)


@router.get("/kbs/{kb_id}/impact")
def kb_impact(kb_id: str, doc_id: str | None = None, actor=Depends(VIEW), db: Session = Depends(get_db)):
    kb_svc.get_kb(db, kb_id)
    return {"affected": _intent_refs(db, intent_svc.users_of_kb(db, kb_id, doc_id))}


@router.delete("/kbs/{kb_id}")
def delete_kb(kb_id: str, actor=Depends(EDIT), db: Session = Depends(get_db)):
    res = kb_svc.delete_kb(db, kb_svc.get_kb(db, kb_id), actor)
    db.commit()
    return res


@router.delete("/kbs/{kb_id}/documents/{doc_id}")
def delete_doc(kb_id: str, doc_id: str, actor=Depends(EDIT), db: Session = Depends(get_db)):
    kb = kb_svc.get_kb(db, kb_id)
    res = kb_svc.delete_doc(db, kb, kb_svc.get_doc(kb, doc_id), actor)
    db.commit()
    return res


@router.post("/kbs/{kb_id}/documents:import", status_code=202)
async def import_docs(kb_id: str, request: Request, background: BackgroundTasks, actor=Depends(EDIT),
                      db: Session = Depends(get_db)):
    form = await request.form()
    files = [(f.filename or "file", await f.read()) for f in form.getlist("files") if hasattr(f, "read")]
    ow = str(form.get("overwrite") or "false").lower() in ("true", "1")
    kb = kb_svc.get_kb(db, kb_id)
    task, job = kb_svc.import_documents(db, kb, files, ow, actor, source="文件上传")
    db.commit()
    if job["files"]:
        background.add_task(kb_svc.run_build, job)
    return task


@router.get("/kbs/{kb_id}/chunks")
def list_chunks(kb_id: str, doc_id: str | None = None, q: str | None = None, page: int = Query(1, ge=1),
                page_size: int = Query(50, ge=1, le=500), actor=Depends(VIEW), db: Session = Depends(get_db)):
    kb = kb_svc.get_kb(db, kb_id)
    total, items = kb_svc.list_chunks(db, kb, doc_id, q, page, page_size, admin=True)
    return {"total": total, "items": items}


@router.put("/kbs/{kb_id}/chunks/{chunk_id}")
async def update_chunk(kb_id: str, chunk_id: str, request: Request, actor=Depends(EDIT), db: Session = Depends(get_db)):
    kb = kb_svc.get_kb(db, kb_id)
    c = kb_svc.update_chunk(db, kb, chunk_id, await json_body(request), actor)
    db.commit()
    kb = kb_svc.get_kb(db, kb_id)
    c = next(x for d in kb.documents for x in d.chunks if x.chunk_id == chunk_id)
    return kb_svc.chunk_dict(kb, c, c.document, kb_svc.linked_map(db, kb).get(chunk_id, []), True, kb_svc.space_names(db))


@router.post("/kbs/{kb_id}/search")
async def search_kb(kb_id: str, request: Request, actor=Depends(VIEW), db: Session = Depends(get_db)):
    b = await json_body(request)
    return kb_svc.search(db, kb_id, b.get("query") or "", b, b.get("top_k"), b.get("threshold"), admin=True)


@router.post("/kb-search")
async def search_all(request: Request, actor=Depends(VIEW), db: Session = Depends(get_db)):
    b = await json_body(request)
    return kb_svc.search(db, b.get("kb_id") or None, b.get("query") or "", b, b.get("top_k"), b.get("threshold"),
                         admin=True)


@router.get("/kb-settings")
def get_kb_settings(actor=Depends(VIEW), db: Session = Depends(get_db)):
    return kb_svc.search_settings(db)


@router.put("/kb-settings")
async def put_kb_settings(request: Request, actor=Depends(EDIT), db: Session = Depends(get_db)):
    b = await json_body(request)
    try:
        v = kb_svc.save_search_settings(db, int(b.get("top_k")), float(b.get("threshold")), actor)
    except (TypeError, ValueError):
        raise bad_request("top_k / threshold 格式错误")
    db.commit()
    return v


@router.get("/kb-imports")
def kb_imports(actor=Depends(VIEW), db: Session = Depends(get_db)):
    recs = db.scalars(select(ImportRecord).where(ImportRecord.kind == "知识").order_by(ImportRecord.at.desc()))
    return [{"id": r.id, "kb_id": r.kb_id, "file": r.file, "source": r.source, "at": iso(r.at), "by": r.by,
             "result": r.result, "status": r.status, "details": r.details} for r in recs]


@router.get("/imports/{import_id}")
def get_import(import_id: str, actor=Depends(VIEW), db: Session = Depends(get_db)):
    r = db.get(ImportRecord, import_id)
    if not r:
        raise not_found("导入任务不存在", "IMPORT_NOT_FOUND")
    return kb_svc.import_task(r)


# ---------------- 工具 ----------------
@router.get("/tools")
def list_tools(source: str | None = None, actor=Depends(VIEW), db: Session = Depends(get_db)):
    sp = kb_svc.space_names(db)
    return [tool_svc.admin_dict(t, intent_svc.users_of_tool(db, t.name), sp) for t in tool_svc.list_tools(db, source)]


@router.get("/tools/{name}")
def get_tool(name: str, actor=Depends(VIEW), db: Session = Depends(get_db)):
    t = tool_svc.get(db, name)
    return tool_svc.admin_dict(t, intent_svc.users_of_tool(db, name), kb_svc.space_names(db))


@router.post("/tools", status_code=201)
async def create_tool(request: Request, actor=Depends(EDIT), db: Session = Depends(get_db)):
    t = tool_svc.create(db, await json_body(request), actor)
    db.commit()
    return tool_svc.admin_dict(t, [], {})


@router.put("/tools/{name}")
async def update_tool(name: str, request: Request, actor=Depends(EDIT), db: Session = Depends(get_db)):
    t = tool_svc.update(db, name, await json_body(request), actor)
    db.commit()
    return tool_svc.admin_dict(t, intent_svc.users_of_tool(db, name), kb_svc.space_names(db))


@router.delete("/tools/{name}")
def delete_tool(name: str, actor=Depends(EDIT), db: Session = Depends(get_db)):
    tool_svc.delete(db, name, actor)
    db.commit()
    return {"ok": True}


@router.patch("/tools/{name}")
async def patch_tool(name: str, request: Request, actor=Depends(EDIT), db: Session = Depends(get_db)):
    t = mcp_svc.patch_tool(db, tool_svc.get(db, name), await json_body(request), actor)
    db.commit()
    return tool_svc.admin_dict(t, intent_svc.users_of_tool(db, name), kb_svc.space_names(db))


# ---------------- MCP 服务 ----------------
@router.get("/mcp-servers")
def list_mcp_servers(actor=Depends(VIEW), db: Session = Depends(get_db)):
    return [mcp_svc.admin_dict(db, s) for s in db.scalars(select(McpServer).order_by(McpServer.position, McpServer.name))]


@router.get("/mcp-servers/{name}")
def get_mcp_server(name: str, actor=Depends(VIEW), db: Session = Depends(get_db)):
    return mcp_svc.admin_dict(db, mcp_svc.get(db, name))


@router.post("/mcp-servers", status_code=201)
async def create_mcp_server(request: Request, actor=Depends(EDIT), db: Session = Depends(get_db)):
    sv = mcp_svc.create(db, await json_body(request), actor)
    db.commit()
    return mcp_svc.admin_dict(db, sv)


@router.put("/mcp-servers/{name}")
async def update_mcp_server(name: str, request: Request, actor=Depends(EDIT), db: Session = Depends(get_db)):
    sv = mcp_svc.update(db, name, await json_body(request), actor)
    db.commit()
    return mcp_svc.admin_dict(db, sv)


@router.delete("/mcp-servers/{name}")
def delete_mcp_server(name: str, actor=Depends(EDIT), db: Session = Depends(get_db)):
    mcp_svc.delete(db, name, actor)
    db.commit()
    return {"ok": True}


@router.post("/mcp-servers/{name}/sync/preview")
async def preview_mcp_sync(name: str, request: Request, actor=Depends(EDIT), db: Session = Depends(get_db)):
    body = await json_body(request)
    sv = mcp_svc.get(db, name)
    return await run_in_threadpool(mcp_svc.preview, db, sv, body, actor)  # 在线同步会访问外部服务，不阻塞事件循环


@router.post("/mcp-servers/{name}/sync/apply")
async def apply_mcp_sync(name: str, request: Request, actor=Depends(EDIT), db: Session = Depends(get_db)):
    sv = mcp_svc.get(db, name)
    res = mcp_svc.apply(db, sv, await json_body(request), actor)
    db.commit()
    return {**res, "server": mcp_svc.admin_dict(db, sv)}


@router.post("/tools/{name}/test")
async def test_tool(name: str, request: Request, actor=Depends(VIEW), db: Session = Depends(get_db)):
    b = await json_body(request)
    return tool_svc.preview(tool_svc.get(db, name), b.get("values") or {}, b.get("space"))


# ---------------- API Key ----------------
@router.get("/api-keys")
def list_keys(actor=Depends(ADMIN), db: Session = Depends(get_db)):
    from ...models import ApiKey
    return [key_svc.to_dict(k) for k in db.scalars(select(ApiKey).order_by(ApiKey.created_at))]


@router.post("/api-keys", status_code=201)
async def create_key(request: Request, actor=Depends(ADMIN), db: Session = Depends(get_db)):
    k, tok = key_svc.create(db, await json_body(request), actor)
    db.commit()
    return {**key_svc.to_dict(k), "token": tok}


@router.post("/api-keys/{kid}/revoke")
def revoke_key(kid: str, actor=Depends(ADMIN), db: Session = Depends(get_db)):
    k = key_svc.revoke(db, kid, actor)
    db.commit()
    return key_svc.to_dict(k)


# ---------------- 元数据 / 文档 ----------------
@router.get("/meta")
def meta(actor=Depends(VIEW), db: Session = Depends(get_db)):
    from ...config import get_settings
    return {"scopes": list(SCOPES), "spaces": [{"id": s.id, "name": s.name} for s in db.scalars(select(Space).order_by(Space.position))],
            "public_base_url": get_settings().public_base_url}


@router.get("/docs/{name}")
def get_doc_file(name: str, actor=Depends(VIEW)):
    files = {"ai-guide": "AI接入说明.md", "openapi": "openapi.yaml", "requirements": "意图管理平台_全局需求文档.md"}
    if name not in files:
        raise not_found("文档不存在")
    from ...config import get_settings
    p = Path(get_settings().docs_dir or DOCS_DIR) / files[name]
    if not p.exists():
        raise not_found("文档文件缺失")
    from urllib.parse import quote
    return PlainTextResponse(p.read_text(encoding="utf-8"), headers={
        "Content-Disposition": f"inline; filename*=UTF-8''{quote(files[name])}"})
