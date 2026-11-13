"""知识库：列表 / 文档 / 切片 / 导入（覆盖确认）/ 检索。全平台通用，不区分空间。"""
import logging
import secrets
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .. import audit
from ..config import get_settings
from ..db import SessionLocal
from ..errors import ApiError, bad_request, conflict, not_found, unprocessable
from ..models import Chunk, Document, ImportRecord, Intent, KnowledgeBase, Setting, Space
from . import intents as intent_svc
from . import spaces as space_svc
from .common import KB_TYPES, TAG_KEYS, Actor, clean_description, effective_tags, iso, norm_tags, utcnow
from .importer import next_import_id
from .parsers import BuildFailure, check_ext, content_hash, parse_table, parse_text, row_text
from .search import backend

log = logging.getLogger("intent.knowledge")


# ---------------- 序列化 ----------------
def kb_status(kb: KnowledgeBase) -> str:
    sts = [d.status for d in kb.documents]
    if "构建中" in sts:
        return "构建中"
    if sts and all(s == "构建失败" for s in sts):
        return "构建失败"
    return "构建成功"


def kb_dict(kb: KnowledgeBase, admin: bool = False) -> dict:
    d = {"kb_id": kb.id, "name": kb.name, "type": kb.type, "status": kb_status(kb),
         "description": kb.description or "",
         "headers": list(kb.headers) if kb.headers else None, "updated_at": iso(kb.updated_at),
         "created_at": iso(kb.created_at)}
    if d["headers"] is None:
        d.pop("headers")
    if admin:
        d["doc_count"] = len(kb.documents)
        d["chunk_count"] = sum(len(x.chunks) for x in kb.documents)
    return d


def data_size(kb: KnowledgeBase, doc: Document) -> str:
    if kb.type == "表格知识库":
        return f"{len(doc.chunks)} 条"
    return f"{sum(len(c.content) for c in doc.chunks)} 字符"


def doc_dict(kb: KnowledgeBase, doc: Document) -> dict:
    d = {"doc_id": doc.doc_id, "name": doc.name, "status": doc.status, "data_size": data_size(kb, doc),
         "uploaded_at": iso(doc.uploaded_at), "updated_at": iso(doc.updated_at)}
    if doc.error:
        d["error"] = doc.error
    return d


def linked_map(db: Session, kb: KnowledgeBase) -> dict[str, list[Intent]]:
    """chunk_id -> 引用它的意图（含整篇引用）。"""
    by_doc: dict[str, list[str]] = {}
    for d in kb.documents:
        by_doc[d.doc_id] = [c.chunk_id for c in d.chunks]
    m: dict[str, list[Intent]] = {}
    for it in intent_svc.users_of_kb(db, kb.id):
        for r in it.kb_refs or []:
            if r.get("kb_id") != kb.id:
                continue
            cids = by_doc.get(r.get("doc_id"), []) if r.get("chunk_id") == "*" else [r.get("chunk_id")]
            for cid in cids:
                lst = m.setdefault(cid, [])
                if it not in lst:
                    lst.append(it)
    return m


def chunk_dict(kb: KnowledgeBase, c: Chunk, doc: Document, linked: list[Intent], admin: bool = False,
               spaces: dict | None = None) -> dict:
    d = {"chunk_id": c.chunk_id, "doc_id": doc.doc_id, "order": c.position, "content": c.content,
         "linked_intents": [i.code for i in linked], "updated_at": iso(c.updated_at),
         "tags": norm_tags(c.tags), "effective_tags": effective_tags(kb.tags, c.tags)}
    if c.cells is not None:
        d["cells"] = dict(c.cells)
    if admin:
        d["doc_name"] = doc.name
        d["linked"] = [{"code": i.code, "name": i.name, "status": i.status, "space": i.space_id,
                        "space_name": (spaces or {}).get(i.space_id, i.space_id)} for i in linked]
    return d


def space_names(db: Session) -> dict[str, str]:
    return {s.id: s.name for s in db.scalars(select(Space))}


# ---------------- 查询 ----------------
def list_kbs(db: Session, name: str | None = None) -> list[KnowledgeBase]:
    stmt = select(KnowledgeBase).options(selectinload(KnowledgeBase.documents).selectinload(Document.chunks))
    if name:
        stmt = stmt.where(KnowledgeBase.name.ilike(f"%{name}%"))
    return list(db.scalars(stmt.order_by(KnowledgeBase.position, KnowledgeBase.created_at)))


def get_kb(db: Session, kb_id: str) -> KnowledgeBase:
    kb = db.scalar(select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
                   .options(selectinload(KnowledgeBase.documents).selectinload(Document.chunks)))
    if not kb:
        raise not_found(f"知识库 {kb_id} 不存在", "KB_NOT_FOUND")
    return kb


def get_doc(kb: KnowledgeBase, doc_id: str) -> Document:
    d = next((x for x in kb.documents if x.doc_id == doc_id), None)
    if not d:
        raise not_found(f"文档 {doc_id} 不存在", "DOC_NOT_FOUND")
    return d


def list_chunks(db: Session, kb: KnowledgeBase, doc_id: str | None, q: str | None, page: int, page_size: int,
                admin: bool = False) -> tuple[int, list[dict]]:
    if doc_id:
        get_doc(kb, doc_id)
    lm = linked_map(db, kb)
    sp = space_names(db) if admin else None
    items = []
    for d in kb.documents:
        if doc_id and d.doc_id != doc_id:
            continue
        for c in d.chunks:
            if q and q.lower() not in c.content.lower():
                continue
            items.append((d, c))
    total = len(items)
    sl = items[(page - 1) * page_size: page * page_size]
    return total, [chunk_dict(kb, c, d, lm.get(c.chunk_id, []), admin, sp) for d, c in sl]


def search_settings(db: Session) -> dict:
    s = db.get(Setting, "kb_search")
    v = dict(s.value) if s else {}
    return {"top_k": int(v.get("top_k", 5)), "threshold": float(v.get("threshold", 0.2))}


def save_search_settings(db: Session, top_k: int, threshold: float, actor: Actor) -> dict:
    if not (1 <= int(top_k) <= 20) or not (0 <= float(threshold) <= 1):
        raise unprocessable("TopK 为 1～20，相似度阈值为 0～1")
    s = db.get(Setting, "kb_search") or Setting(key="kb_search", value={})
    s.value = {"top_k": int(top_k), "threshold": float(threshold)}
    db.merge(s)
    audit.record(db, actor, "kb.settings", "setting", "kb_search", "修改知识检索设置", None, s.value)
    return s.value


def search(db: Session, kb_id: str | None, query: str, filters: dict, top_k: int | None, threshold: float | None,
           admin: bool = False) -> list[dict]:
    if not (query or "").strip():
        raise bad_request("query 不能为空")
    st = search_settings(db)
    top_k = st["top_k"] if top_k is None else int(top_k)
    threshold = st["threshold"] if threshold is None else float(threshold)
    if not (1 <= top_k <= 20):
        raise bad_request("top_k 应为 1～20")
    if not (0 <= threshold <= 1):
        raise bad_request("threshold 应为 0～1")
    if kb_id:
        get_kb(db, kb_id)
    hits = backend().search(db, query, kb_id, {k: filters.get(k) for k in TAG_KEYS if filters.get(k)}, top_k, threshold)
    out, kbs = [], {}
    sp = space_names(db) if admin else None
    for h in hits:
        c = db.get(Chunk, h.chunk_pk)
        kb = kbs.get(c.kb_id) or get_kb(db, c.kb_id)
        kbs[kb.id] = kb
        lm = kbs.setdefault("_lm_" + kb.id, linked_map(db, kb))
        doc = c.document
        cd = chunk_dict(kb, c, doc, lm.get(c.chunk_id, []), admin, sp)
        cd["kb_id"] = kb.id
        out.append({"score": h.score, "doc_name": doc.name, "kb_name": kb.name, "chunk": cd})
    return out


# ---------------- 维护：知识库 ----------------
def _check_tags(tags) -> dict:
    return norm_tags(tags if isinstance(tags, dict) else {})


def create_kb(db: Session, name: str, kb_type: str, actor: Actor, description: str = "",
              tags=None) -> KnowledgeBase:
    """tags 为兼容参数，忽略不写入（历史适用范围已迁到 description）。"""
    name = (name or "").strip()
    if not name:
        raise unprocessable("知识库名称必填")
    if kb_type not in KB_TYPES:
        raise unprocessable("知识库类型应为 表格知识库 / 图文知识库-富文本")
    if db.scalar(select(KnowledgeBase).where(KnowledgeBase.name == name)):
        raise conflict(f"知识库名称「{name}」已存在", "KB_NAME_EXISTS")
    desc = clean_description(description)
    kb = KnowledgeBase(id="kb_" + secrets.token_hex(4), name=name, type=kb_type, description=desc,
                       headers=[] if kb_type == "表格知识库" else None, tags={},
                       position=-int(utcnow().timestamp()))
    db.add(kb)
    db.flush()
    audit.record(db, actor, "kb.create", "kb", kb.id, f"新建知识库「{name}」", None,
                 {"type": kb_type, "description": desc})
    return kb


def update_kb(db: Session, kb: KnowledgeBase, data: dict, actor: Actor) -> KnowledgeBase:
    # tags / platform / site / language / category 若随请求传入则忽略
    before = {"name": kb.name, "description": kb.description or ""}
    if "name" in data:
        n = (data.get("name") or "").strip()
        if not n:
            raise unprocessable("知识库名称必填")
        if n != kb.name and db.scalar(select(KnowledgeBase).where(KnowledgeBase.name == n)):
            raise conflict(f"知识库名称「{n}」已存在", "KB_NAME_EXISTS")
        kb.name = n
    if "description" in data:
        kb.description = clean_description(data.get("description"))
    after = {"name": kb.name, "description": kb.description or ""}
    if before != after:
        kb.updated_at = utcnow()
        audit.record(db, actor, "kb.update", "kb", kb.id, f"编辑知识库「{kb.name}」", None,
                     {"before": before, "after": after})
    return kb


def _drop_refs(db: Session, pred, by: str, reason: str) -> tuple[list[str], set[str]]:
    """移除满足 pred(ref) 的知识关联；返回 (被降级的意图, 需升版本的空间)。"""
    touched, bump = [], set()
    for it in db.scalars(select(Intent).where(func.jsonb_array_length(Intent.kb_refs) > 0)):
        refs = [r for r in it.kb_refs if not pred(r)]
        if len(refs) != len(it.kb_refs):
            it.kb_refs = refs
            it.updated_at = utcnow()
            intent_svc.add_log(it, by, reason + "，已移除失效的知识关联")
            touched.append(it)
            if it.status == "启用":
                bump.add(it.space_id)
    db.flush()
    down = intent_svc.recheck_enabled(db, touched, by, reason)
    return down, bump


def delete_kb(db: Session, kb: KnowledgeBase, actor: Actor) -> dict:
    users = intent_svc.users_of_kb(db, kb.id)
    down, bump = _drop_refs(db, lambda r: r.get("kb_id") == kb.id, actor.name, f"知识库「{kb.name}」被删除")
    space_svc.bump_versions(db, bump)
    audit.record(db, actor, "kb.delete", "kb", kb.id, f"删除知识库「{kb.name}」", None,
                 {"affected_intents": [u.code for u in users], "downgraded": down})
    db.delete(kb)
    return {"affected_intents": [u.code for u in users], "downgraded": down}


def delete_doc(db: Session, kb: KnowledgeBase, doc: Document, actor: Actor) -> dict:
    users = intent_svc.users_of_kb(db, kb.id, doc.doc_id)
    down, bump = _drop_refs(db, lambda r: r.get("kb_id") == kb.id and r.get("doc_id") == doc.doc_id, actor.name,
                            f"文档「{doc.name}」被删除")
    space_svc.bump_versions(db, bump)
    kb.documents.remove(doc)
    kb.updated_at = utcnow()
    audit.record(db, actor, "kb.doc_delete", "document", f"{kb.id}/{doc.doc_id}", f"删除文档「{doc.name}」", None,
                 {"affected_intents": [u.code for u in users], "downgraded": down})
    return {"affected_intents": [u.code for u in users], "downgraded": down}


def update_chunk(db: Session, kb: KnowledgeBase, chunk_id: str, data: dict, actor: Actor) -> Chunk:
    c = next((x for d in kb.documents for x in d.chunks if x.chunk_id == chunk_id), None)
    if not c:
        raise not_found(f"切片 {chunk_id} 不存在", "CHUNK_NOT_FOUND")
    before = {"content": c.content, "cells": c.cells, "tags": norm_tags(c.tags)}
    if kb.type == "表格知识库":
        if "cells" in data:
            cells = data.get("cells") or {}
            if not isinstance(cells, dict) or list(cells.keys()) != list(kb.headers or []) and set(cells.keys()) != set(kb.headers or []):
                raise bad_request("cells 的键必须与知识库表头一致", "HEADER_MISMATCH",
                                  expected=list(kb.headers or []), actual=list(cells.keys()) if isinstance(cells, dict) else [])
            cells = {h: str(cells.get(h) or "") for h in kb.headers}
            c.cells = cells
            c.content = row_text(kb.headers, cells)
        elif "content" in data:
            raise bad_request("表格知识库请传 cells", "CELLS_REQUIRED")
    else:
        if "content" in data:
            t = (data.get("content") or "").strip()
            if not t:
                raise unprocessable("切片内容不能为空")
            c.content = t
        elif "cells" in data:
            raise bad_request("图文知识库请传 content", "CONTENT_REQUIRED")
    if "tags" in data:
        c.tags = norm_tags(data.get("tags") or {})
    after = {"content": c.content, "cells": c.cells, "tags": norm_tags(c.tags)}
    if before == after:
        return c
    now = utcnow()
    c.updated_at = now
    c.document.updated_at = now
    kb.updated_at = now
    db.flush()
    backend().reindex(db, [c.id])
    bump = set()
    for it in linked_map(db, kb).get(chunk_id, []):
        intent_svc.add_log(it, actor.name, f"关联知识切片 {kb.name} / {c.document.name} / #{c.position} 已编辑")
        if it.status == "启用":
            bump.add(it.space_id)
    space_svc.bump_versions(db, bump)
    audit.record(db, actor, "kb.chunk_update", "chunk", f"{kb.id}/{chunk_id}", f"编辑切片 {kb.name} #{c.position}",
                 None, {"before": before, "after": after})
    return c


# ---------------- 导入 ----------------
@dataclass
class ParsedFile:
    name: str
    raw_hash: str
    headers: list[str] | None = None
    rows: list[dict] | None = None
    texts: list[str] | None = None
    failure: str | None = None
    target: Document | None = None
    action: str = "created"
    lost: list[str] = field(default_factory=list)  # 覆盖后会消失的切片 id


def _parse(kb: KnowledgeBase, name: str, raw: bytes) -> ParsedFile:
    check_ext(kb.type, name)
    pf = ParsedFile(name=name, raw_hash="")
    try:
        if kb.type == "表格知识库":
            pf.headers, pf.rows = parse_table(name, raw)
            first_col = pf.headers[0] if pf.headers else ""
            pf.raw_hash = content_hash([row_text(pf.headers, r) for r in pf.rows])
            _ = first_col
        else:
            pf.texts = parse_text(name, raw)
            pf.raw_hash = content_hash(pf.texts)
    except BuildFailure as e:
        pf.failure = str(e)
    except ApiError:
        raise
    except Exception as e:  # noqa: BLE001
        raise bad_request(f"文件 {name} 解析失败：{e}", "PARSE_ERROR", file=name)
    return pf


def _plan_overwrite(kb: KnowledgeBase, pf: ParsedFile) -> None:
    tgt = next((d for d in kb.documents if d.name == pf.name), None)
    if not tgt and pf.raw_hash:
        tgt = next((d for d in kb.documents if d.content_hash and d.content_hash == pf.raw_hash), None)
    if not tgt:
        return
    pf.target, pf.action = tgt, "overwritten"
    if pf.failure:
        return
    if kb.type == "表格知识库":
        h0 = (kb.headers or pf.headers or [""])[0]
        new_keys = {r.get(h0, "") for r in pf.rows or []}
        pf.lost = [c.chunk_id for c in tgt.chunks if (c.cells or {}).get(h0, "") not in new_keys]
    else:
        new = set(pf.texts or [])
        pf.lost = [c.chunk_id for c in tgt.chunks if c.content not in new]


def affected_by(db: Session, kb: KnowledgeBase, files: list[ParsedFile]) -> list[dict]:
    lm = linked_map(db, kb)
    out, seen = [], set()
    for pf in files:
        if not pf.target:
            continue
        orders = {c.chunk_id: c.position for c in pf.target.chunks}
        for cid in pf.lost:
            for it in lm.get(cid, []):
                # 整篇引用不受影响（文档仍在）
                if any(r.get("kb_id") == kb.id and r.get("doc_id") == pf.target.doc_id and r.get("chunk_id") == cid
                       for r in it.kb_refs):
                    k = (it.code, cid)
                    if k not in seen:
                        seen.add(k)
                        out.append({"code": it.code, "name": it.name, "status": it.status, "space": it.space_id,
                                    "doc": pf.target.name, "chunk_id": cid, "order": orders.get(cid)})
    return out


def _new_doc_id(kb: KnowledgeBase) -> str:
    used = {d.doc_id for d in kb.documents}
    while True:
        d = "D" + secrets.token_hex(3).upper()
        if d not in used:
            return d


def import_documents(db: Session, kb: KnowledgeBase, files: list[tuple[str, bytes]], overwrite: bool, actor: Actor,
                     source: str = "文件上传") -> tuple[dict, dict]:
    s = get_settings()
    if not files:
        raise bad_request("请上传文件", "FILES_REQUIRED")
    if len(files) > s.max_batch:
        raise ApiError(413, "PAYLOAD_TOO_LARGE", f"单次最多 {s.max_batch} 个文件")
    for n, raw in files:
        if len(raw) > s.max_upload_mb * 1024 * 1024:
            raise ApiError(413, "PAYLOAD_TOO_LARGE", f"文件 {n} 超过 {s.max_upload_mb}MB")
    parsed = [_parse(kb, n, raw) for n, raw in files]
    if kb.type == "表格知识库":
        expected = list(kb.headers or [])
        for pf in parsed:
            if pf.failure:
                continue
            if not expected:
                expected = pf.headers
            elif pf.headers != expected:
                raise bad_request(f"文件 {pf.name} 的表头与知识库不一致", "HEADER_MISMATCH",
                                  expected=expected, actual=pf.headers, file=pf.name)
        if expected and not kb.headers:
            kb.headers = expected
    names = [p.name for p in parsed]
    if len(set(names)) != len(names):
        raise bad_request("同一批次内文件名重复", "DUPLICATE_FILE")
    for pf in parsed:
        _plan_overwrite(kb, pf)
    affected = affected_by(db, kb, parsed)
    over = [p for p in parsed if p.target]
    if over and not overwrite:
        raise conflict(
            f"与已有文档同名或同内容（{'、'.join(p.target.name for p in over)}），覆盖会影响 "
            f"{len({a['code'] for a in affected})} 个已关联意图", "OVERWRITE_REQUIRED",
            documents=[p.target.name for p in over], affected_intents=sorted({a["code"] for a in affected}),
            affected=affected)
    now = utcnow()
    rec = ImportRecord(id=next_import_id(db), kind="知识", kb_id=kb.id, file="、".join(names), source=source,
                       by=actor.name, status="构建中", result="构建中",
                       details={"documents": [{"name": p.name, "action": p.action} for p in parsed],
                                "affected_intents": sorted({a["code"] for a in affected})})
    db.add(rec)
    job = {"import_id": rec.id, "kb_id": kb.id, "actor": actor.__dict__, "files": []}
    for pf in parsed:
        doc = pf.target
        if not doc:
            doc = Document(kb_id=kb.id, doc_id=_new_doc_id(kb), name=pf.name, uploaded_at=now, updated_at=now)
            kb.documents.append(doc)
        doc.status = "构建失败" if pf.failure else "构建中"
        doc.error = pf.failure
        doc.updated_at = now
        db.flush()
        if not pf.failure:
            job["files"].append({"doc_pk": doc.id, "headers": pf.headers, "rows": pf.rows, "texts": pf.texts,
                                 "hash": pf.raw_hash, "lost": pf.lost, "action": pf.action, "name": pf.name})
    kb.updated_at = now
    audit.record(db, actor, "kb.import", "kb", kb.id, f"导入文档 {'、'.join(names)}", None,
                 {"import_id": rec.id, "overwrite": [p.name for p in over],
                  "affected_intents": sorted({a["code"] for a in affected})})
    if not job["files"]:
        rec.status = "构建失败"
        rec.result = "；".join(f"{p.name}：{p.failure}" for p in parsed if p.failure)
        rec.details = {**rec.details, "message": rec.result}
    task = import_task(rec)
    return task, job


def _ensure_id(kb: KnowledgeBase, used: set[str], base: str) -> str:
    cid, n = base, 1
    while cid in used:
        n += 1
        cid = f"{base}-{n}"
    used.add(cid)
    return cid


def run_build(job: dict) -> None:
    """后台构建（同进程）：写入切片与检索索引，处理覆盖导致的失效关联。"""
    db = SessionLocal()
    try:
        _build(db, job)
        db.commit()
    except Exception as e:  # noqa: BLE001
        log.exception("knowledge build failed")
        db.rollback()
        rec = db.get(ImportRecord, job["import_id"])
        if rec:
            rec.status = "构建失败"
            rec.result = f"构建失败：{e}"
            for f in job["files"]:
                d = db.get(Document, f["doc_pk"])
                if d:
                    d.status, d.error = "构建失败", str(e)
            db.commit()
    finally:
        db.close()


def _build(db: Session, job: dict) -> None:
    kb = get_kb(db, job["kb_id"])
    actor = Actor(**job["actor"])
    used = {c.chunk_id for d in kb.documents for c in d.chunks}
    all_lost: list[tuple[str, str]] = []
    pks = []
    now = utcnow()
    for f in job["files"]:
        doc = db.get(Document, f["doc_pk"])
        old = {c.chunk_id: c for c in doc.chunks}
        if kb.type == "表格知识库":
            h0 = kb.headers[0]
            old_by_key = {(c.cells or {}).get(h0, ""): c for c in doc.chunks}
            specs = []
            for i, cells in enumerate(f["rows"]):
                prev = old_by_key.get(cells.get(h0, ""))
                specs.append((prev, i + 1, row_text(kb.headers, cells), cells))
        else:
            old_by_text = {c.content: c for c in doc.chunks}
            specs = [(old_by_text.get(t), i + 1, t, None) for i, t in enumerate(f["texts"])]
        keep = set()
        for prev, pos, content, cells in specs:
            if prev is not None and prev.chunk_id not in keep:
                keep.add(prev.chunk_id)
                changed = prev.content != content
                prev.position, prev.content, prev.cells = pos, content, cells
                if changed:
                    prev.updated_at = now
                pks.append(prev)
            else:
                base = f"{doc.doc_id}-{pos}"
                cid = _ensure_id(kb, used, base)
                c = Chunk(kb_id=kb.id, chunk_id=cid, position=pos, content=content, cells=cells, tags={},
                          updated_at=now)
                doc.chunks.append(c)
                pks.append(c)
        for cid, c in old.items():
            if cid not in keep:
                doc.chunks.remove(c)
                all_lost.append((doc.doc_id, cid))
        doc.status, doc.error, doc.content_hash, doc.updated_at = "构建成功", None, f["hash"], now
        db.flush()
    backend().reindex(db, [c.id for c in pks])
    lost = set(all_lost)
    down, bump = [], set()
    if lost:
        down, bump = _drop_refs(db, lambda r: r.get("kb_id") == kb.id and (r.get("doc_id"), r.get("chunk_id")) in lost,
                                actor.name, f"知识库「{kb.name}」覆盖导入")
    space_svc.bump_versions(db, bump)
    kb.updated_at = now
    rec = db.get(ImportRecord, job["import_id"])
    rec.status = "构建成功"
    n = sum(len(f["rows"] or f["texts"] or []) for f in job["files"])
    rec.result = f"构建成功：{len(job['files'])} 个文档，{n} 个切片" + (f"；{len(down)} 个已启用意图转为草稿" if down else "")
    rec.details = {**rec.details, "message": rec.result, "downgraded": down}


def import_task(rec: ImportRecord) -> dict:
    d = rec.details or {}
    t = {"import_id": rec.id, "status": rec.status}
    if rec.kind == "知识":
        t["documents"] = d.get("documents", [])
        t["affected_intents"] = d.get("affected_intents", [])
    t["message"] = d.get("message") or rec.result
    return t


def recover_stuck_builds(db: Session) -> int:
    """进程重启后，把遗留的「构建中」标为失败，提示重新导入。"""
    n = 0
    for d in db.scalars(select(Document).where(Document.status == "构建中")):
        d.status, d.error = "构建失败", "服务重启导致构建中断，请重新导入"
        n += 1
    for r in db.scalars(select(ImportRecord).where(ImportRecord.status == "构建中")):
        r.status, r.result = "构建失败", "服务重启导致构建中断，请重新导入"
    db.commit()
    return n
