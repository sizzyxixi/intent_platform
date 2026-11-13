"""单句测试（模拟识别，复刻 demo 逻辑）与知识引用展示。"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Chunk, Document, Intent, KnowledgeBase, Space
from . import handling as handling_svc
from . import spaces as space_svc
from .common import FINAL_ACTIONS
from .recognizer import recognizer, slot_value
from .validation import RefIndex, effective_ask


def chunk_title(c: Chunk) -> str:
    if c.cells:
        # JSONB 不保留键顺序：按知识库表头第一列取标题
        h = (c.document.kb.headers or []) if c.document is not None and c.document.kb is not None else []
        if h and h[0] in c.cells:
            return str(c.cells.get(h[0]) or "")
        return str(next(iter(c.cells.values()), "") or "")
    return c.content


def describe_ref(db: Session, r: dict, cache: dict | None = None) -> dict:
    cache = cache if cache is not None else {}
    key = (r.get("kb_id"), r.get("doc_id"))
    if key not in cache:
        kb = db.get(KnowledgeBase, r.get("kb_id") or "")
        doc = db.scalar(select(Document).where(Document.kb_id == r.get("kb_id"), Document.doc_id == r.get("doc_id"))) if kb else None
        chunks = list(db.scalars(select(Chunk).where(Chunk.document_id == doc.id).order_by(Chunk.position))) if doc else []
        cache[key] = (kb, doc, chunks)
    kb, doc, chunks = cache[key]
    out = dict(r)
    out.setdefault("usage", "reference")
    if not doc:
        out.update(valid=False, label=f"（已失效）{r.get('chunk_id')}", content="")
        return out
    if r.get("chunk_id") == "*":
        out.update(valid=True, whole=True, label=f"{kb.name} / {doc.name} / 整篇（{len(chunks)} 条）",
                   content="\n".join(c.content for c in chunks), kb_name=kb.name, doc_name=doc.name)
        return out
    c = next((c for c in chunks if c.chunk_id == r.get("chunk_id")), None)
    if not c:
        out.update(valid=False, label=f"（已失效）{r.get('chunk_id')}", content="")
        return out
    out.update(valid=True, whole=False, label=f"{kb.name} / {doc.name} / #{c.position} {chunk_title(c)[:18]}",
               content=c.content, order=c.position, kb_name=kb.name, doc_name=doc.name)
    return out


def run_test(db: Session, sp: Space, text: str, draft: dict | None) -> dict:
    rc = space_svc.recognition(sp)
    th, cm, fa = rc["threshold"], rc["candidate_min"], sp.final_action
    fa_label = FINAL_ACTIONS.get(fa, fa)
    fb = rc.get("fallback") or handling_svc.space_fallback(sp)
    if fb["mode"] == "fixed_reply":
        final_txt = f"{fb['mode_label']}（{fb.get('reply_strictness_label') or ''}）：" + " / ".join(fb.get("replies") or [])
    elif fb["mode"] == "llm_free":
        final_txt = fb["mode_label"] + (f"（{fb['prompt']}）" if fb.get("prompt") else "")
    elif fb.get("message"):
        final_txt = fb["mode_label"] + f"（{fb['message']}）"
    else:
        final_txt = fa_label + (f"（「{sp.final_message or ''}」）" if fa == "decline" else "")
    pool = [{"code": i.code, "name": i.name, "examples": i.examples, "slots": i.slots, "kb_refs": i.kb_refs,
             "tools": i.tools, "handling": i.handling} for i in db.scalars(select(Intent).where(Intent.space_id == sp.id, Intent.status == "启用"))
            if not draft or i.code != draft.get("code")]
    if draft:
        pool.append({**draft, "_me": True})
    rz = recognizer()
    scored = [(rz.score(text, c.get("examples") or [], c.get("name") or ""), c) for c in pool]
    scored = sorted([x for x in scored if x[0] > 0], key=lambda x: -x[0])[:3]
    base = {"threshold": th, "candidate_min": cm, "final_action": fa, "final_action_label": fa_label,
            "fallback": fb, "final_text": final_txt, "recognizer": rz.name, "simulated": True,
            "candidates": [{"code": c["code"], "name": c["name"], "score": s, "is_current": bool(c.get("_me"))}
                           for s, c in scored]}
    top0 = scored[0][0] if scored else 0
    if not scored or top0 < th:
        cands = [x for x in base["candidates"] if x["score"] >= cm][:sp.max_candidates]
        if cands:
            return {**base, "verdict": "clarify", "top_score": top0, "clarify_prompt": sp.clarify_prompt,
                    "clarify_candidates": cands, "max_clarify": sp.max_clarify}
        return {**base, "verdict": "final", "top_score": top0, "final_message": sp.final_message,
                "fallback": fb}
    s, c = scored[0]
    idx = RefIndex(db)
    defs = idx.slots(sp.id)
    vals, miss = {}, []
    for sl in c.get("slots") or []:
        v = slot_value(text, sl["name"])
        vals[sl["name"]] = v
        if not v and sl.get("required"):
            miss.append({"name": sl["name"], "ask": effective_ask(sl, defs) or f"请补充{sl['name']}"})
    cache = {}
    refs = [describe_ref(db, r, cache) for r in c.get("kb_refs") or []]
    ref = [r for r in refs if r.get("usage", "reference") == "reference"]
    chk = [r for r in refs if r.get("usage") == "validate"]
    tools = []
    for b in c.get("tools") or []:
        t = idx.tools().get(b.get("tool"))
        if not t:
            tools.append({"tool": b.get("tool"), "registered": False})
            continue
        args = []
        for p in t.params or []:
            m = (b.get("param_map") or {}).get(p["name"]) or {}
            src, v = m.get("source"), m.get("value")
            shown = (vals.get(v) or "待追问") if src == "槽位" else ("{" + str(v) + "}" if src == "上下文" else v)
            args.append({"name": p["name"], "value": shown or "—"})
        tools.append({"tool": t.name, "type": t.type, "registered": True, "args": args})
    if miss:
        decision = "追问：" + " ".join(f"「{m['ask']}」" for m in miss) + \
            f"（每个必填槽位最多追问 {sp.max_slot_ask} 次，仍缺 → {fa_label}）"
    else:
        decision = ("调用工具" if tools else "按参考知识回答") + (f" → 生成后执行 {len(chk)} 项校验" if chk else "")
    return {**base, "verdict": "hit", "top_score": s, "hit": {"code": c["code"], "name": c["name"],
            "is_current": bool(c.get("_me"))},
            "slots": [{"name": k, "value": v} for k, v in vals.items()], "missing": miss,
            "reference": [{"label": r["label"], "preview": (r.get("content") or "").replace("\n", " ")[:60]} for r in ref],
            "validate": [{"label": r["label"], "preview": (r.get("content") or "").replace("\n", " ")[:60]} for r in chk],
            "tools": tools, "tool_retry": sp.tool_retry, "decision": decision}
