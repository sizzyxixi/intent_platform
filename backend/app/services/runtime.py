"""按空间导出运行时配置：已启用意图 + recognition + 槽位定义 + 校验集全文 + 引用到的工具。"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Intent, Space, Tool
from . import knowledge as kb_svc
from . import slots as slot_svc
from . import spaces as space_svc
from . import tools as tool_svc
from .common import iso, norm_tags, utcnow
from .validation import RefIndex


def build(db: Session, sp: Space) -> dict:
    idx = RefIndex(db)
    enabled = list(db.scalars(select(Intent).where(Intent.space_id == sp.id, Intent.status == "启用")
                              .order_by(Intent.position, Intent.code)))
    from .intents import effective_slots
    intents_out, tool_names, vsets = [], [], {}
    for it in enabled:
        tools = []
        for b in it.tools or []:
            t = idx.tools().get(b["tool"])
            if not t:
                continue
            tools.append({"tool": t.name, "type": t.type, "param_map": b.get("param_map") or {}})
            if t.name not in tool_names:
                tool_names.append(t.name)
        intents_out.append({"code": it.code, "name": it.name, "l1": it.l1, "l2": it.l2,
                            "description": it.description, "examples": list(it.examples or []),
                            "slots": effective_slots(it, idx), "knowledge": list(it.kb_refs or []), "tools": tools})
        for r in it.kb_refs or []:
            if r.get("usage") != "validate":
                continue
            key = (r["kb_id"], r["doc_id"], r["chunk_id"])
            vsets.setdefault(key, []).append(it.code)
    validation_sets = []
    kbs: dict = {}
    for (kb_id, doc_id, chunk_id), used in vsets.items():
        try:
            kb = kbs.get(kb_id) or kb_svc.get_kb(db, kb_id)
        except Exception:  # noqa: BLE001
            continue
        kbs[kb_id] = kb
        doc = next((d for d in kb.documents if d.doc_id == doc_id), None)
        if not doc:
            continue
        chunks = doc.chunks if chunk_id == "*" else [c for c in doc.chunks if c.chunk_id == chunk_id]
        items = []
        for c in chunks:
            item = {"chunk_id": c.chunk_id, "content": c.content, "tags": kb_svc.effective_tags(kb.tags, c.tags)}
            if c.cells is not None:
                item["cells"] = dict(c.cells)
            items.append(item)
        validation_sets.append({"kb_id": kb_id, "kb_name": kb.name, "doc_id": doc_id, "doc_name": doc.name,
                                "chunk_id": chunk_id, "used_by": used, "items": items})
    tools_out = [tool_svc.api_dict(t) for t in db.scalars(select(Tool).where(Tool.name.in_(tool_names)))]
    tools_out.sort(key=lambda d: tool_names.index(d["name"]))
    return {
        "space": {"id": sp.id, "name": sp.name, "bot": sp.bot},
        "version": sp.version,
        "generated_at": iso(utcnow()),
        "recognition": space_svc.recognition(sp),
        "slots": [{k: v for k, v in slot_svc.to_dict(s).items() if k != "options" or v}
                  for s in slot_svc.list_slots(db, sp.id)],
        "intents": intents_out,
        "validation_sets": validation_sets,
        "tools": tools_out,
    }


_ = norm_tags
