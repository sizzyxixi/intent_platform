"""1688 智能客服空间（cs）按《意图 × 工具调用 映射维护表 v1》重建意图、分类、槽位、工具绑定和知识库。

    python -m app.cs1688 backup --out backup_before_cs_rebuild.json
    python -m app.cs1688 apply --yes [--backup backup_before_cs_rebuild.json]
    python -m app.cs1688 status

数据：seed/cs_1688/cs_1688.json（由 seed/cs_1688/build.py 从维护表生成）。
- 删除 cs 空间全部旧意图（示例数据）、旧分类、未用到的旧槽位、cs 的示例导入记录；
- 删除 1688 客服示例知识库（DEMO_KBS，且不被其他空间引用）及本脚本上次生成的知识库（kb_cs_*），再按维护表重建；
- 意图只绑定西之月目录里真实存在的 xclaw 工具；文档里规划中、目录里没有的工具不注册，记为缺口；
- 其他空间（mc）不动。可重复执行（幂等）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import delete, select

from . import audit
from .db import SessionLocal
from .models import Category, Chunk, Document, ImportRecord, Intent, IntentLog, KnowledgeBase, SlotDef, Space, Tool
from .services import intents as intent_svc, knowledge as kb_svc, slots as slot_svc
from .services.common import Actor, utcnow

SPACE = "cs"
DATA = Path(__file__).resolve().parents[1] / "seed" / "cs_1688" / "cs_1688.json"
DEMO_KBS = ("kb_aq", "kb_rule", "kb_test", "kb_sales")
OWN_PREFIX = "kb_cs_"
ACTOR = Actor("system", "cs1688", "映射表导入脚本")
TAGS = {"platform": ["1688"], "language": ["zh"], "site": [], "category": []}


def load() -> dict:
    return json.loads(DATA.read_text(encoding="utf-8"))


def _row(o, cols):
    return {c: (getattr(o, c).isoformat() if hasattr(getattr(o, c), "isoformat") else getattr(o, c)) for c in cols}


def _cols(model):
    return [c.name for c in model.__table__.columns]


def target_kbs(db) -> list[KnowledgeBase]:
    """要删除的知识库：示例库 + 本脚本生成的库，且不被其他空间的意图引用。"""
    used_elsewhere = set()
    for it in db.scalars(select(Intent).where(Intent.space_id != SPACE)):
        used_elsewhere |= {r.get("kb_id") for r in it.kb_refs or []}
    out = []
    for kb in db.scalars(select(KnowledgeBase)):
        if (kb.id in DEMO_KBS or kb.id.startswith(OWN_PREFIX)) and kb.id not in used_elsewhere:
            out.append(kb)
    return out


def backup(db, path: Path) -> dict:
    sp = db.get(Space, SPACE)
    kbs = target_kbs(db)
    data = {
        "at": utcnow().isoformat(), "space": _row(sp, _cols(Space)),
        "categories": [_row(c, _cols(Category)) for c in db.scalars(select(Category).where(Category.space_id == SPACE))],
        "slots": [_row(s, _cols(SlotDef)) for s in db.scalars(select(SlotDef).where(SlotDef.space_id == SPACE))],
        "intents": [{**_row(i, _cols(Intent)), "logs": [_row(lg, _cols(IntentLog)) for lg in i.logs]}
                    for i in db.scalars(select(Intent).where(Intent.space_id == SPACE))],
        "import_records": [_row(r, _cols(ImportRecord)) for r in db.scalars(select(ImportRecord).where(ImportRecord.space_id == SPACE))],
        "knowledge_bases": [{**_row(k, _cols(KnowledgeBase)),
                             "documents": [{**_row(d, _cols(Document)), "chunks": [_row(c, _cols(Chunk)) for c in d.chunks]}
                                           for d in k.documents]} for k in kbs],
        "tool_confirm": {t.name: (t.config or {}).get("confirm_required") for t in db.scalars(
            select(Tool).where(Tool.name.in_(load()["confirm_tools"])))},
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return {"intents": len(data["intents"]), "categories": len(data["categories"]), "slots": len(data["slots"]),
            "kbs": [k["id"] for k in data["knowledge_bases"]], "import_records": len(data["import_records"])}


# ---------------- 知识库内容（只来自维护表） ----------------
def kb_specs(spec: dict) -> list[dict]:
    d = spec["doc"]
    tm = spec["tool_map"]
    m = {r["intent_bucket"]: r for r in d["mapping"]}
    reply = [{"意图": r["intent_bucket"], "阶段": r["阶段"], "台账意图对照": r["台账意图对照"], "必填槽位": r["必填槽位"],
              "主工具(按序)": r["主工具(按序)"], "辅工具": r["辅工具"], "置信≥0.85": r["置信≥0.85"],
              "置信0.60–0.85": r["置信0.60–0.85"], "低置信": r["低置信"], "禁止": r["禁止"], "备注": r["备注"],
              "全量会话量": r["全量会话量"], "文字转单率": r["文字转单率"]} for r in d["mapping"]]
    tools = []
    for t in d["tools"]:
        x = tm.get(t["tool_id"], {})
        tools.append({**{k: t[k] for k in ("tool_id", "工具名", "能力", "必要入参", "可选入参", "成功返回(必须进首句的字段)",
                                          "失败策略", "是否成交类", "授权/硬门", "状态")},
                      "平台对应工具": "、".join(x.get("tools") or []) or "（无，缺口）", "匹配说明": x.get("why", "")})
    conf = [dict(r) for r in d["rules"]]
    for k in ("用途", "工具原则", "覆盖", "不做承诺"):
        if d["notes"].get(k):
            conf.append({"规则ID": f"通用-{k}", "条件": "全部意图", "允许工具": "—", "禁止工具": "—", "输出": d["notes"][k]})
    close = [{"意图": r["intent_bucket"], "阶段": r["阶段"], "高转化收束动作": r["高转化收束动作"]}
             for r in d["mapping"] if r["高转化收束动作"] not in ("", "—")]
    ledger = [dict(r) for r in d["ledger"]]
    _ = m
    return [
        {"id": "kb_cs_reply", "name": "1688客服-回复规则与禁止项", "doc_id": "RULE", "file": "回复规则与禁止项_v1.json", "rows": reply},
        {"id": "kb_cs_tools", "name": "1688客服-工具使用规则", "doc_id": "TOOL", "file": "工具字典_v1.json", "rows": tools},
        {"id": "kb_cs_conf", "name": "1688客服-置信度与并行规则", "doc_id": "CONF", "file": "置信度与并行_v1.json", "rows": conf},
        {"id": "kb_cs_close", "name": "1688客服-高转化收束话术", "doc_id": "CLOSE", "file": "高转化收束动作_v1.json", "rows": close},
        {"id": "kb_cs_ledger", "name": "1688客服-台账意图对照", "doc_id": "LEDGER", "file": "台账意图对照_v1.json", "rows": ledger},
    ]


def build_kbs(db, spec: dict) -> dict[str, dict[str, str]]:
    """建库 → 导入 → 同步构建。返回 {kb_id: {首列值: chunk_id}}。"""
    index = {}
    for k in kb_specs(spec):
        kb = KnowledgeBase(id=k["id"], name=k["name"], type="表格知识库", headers=[], tags=dict(TAGS),
                           position=-int(utcnow().timestamp()))
        db.add(kb)
        db.flush()
        audit.record(db, ACTOR, "kb.create", "kb", kb.id, f"新建知识库「{kb.name}」（映射维护表 v1）", None, {"tags": kb.tags})
        raw = json.dumps(k["rows"], ensure_ascii=False).encode("utf-8")
        _task, job = kb_svc.import_documents(db, kb, [(k["file"], raw)], False, ACTOR, source="映射维护表 v1")
        doc = kb.documents[0]
        doc.doc_id = k["doc_id"]
        db.flush()
        kb_svc._build(db, job)
        db.flush()
        h0 = kb.headers[0]
        index[kb.id] = {(c.cells or {}).get(h0, ""): c.chunk_id for c in doc.chunks}
        index[kb.id]["__doc__"] = doc.doc_id
    return index


def kb_refs_for(it: dict, spec: dict, idx: dict) -> list[dict]:
    b = it["bucket"]
    refs = []

    def add(kb, key, usage):
        cid = idx[kb].get(key)
        if cid:
            refs.append({"kb_id": kb, "doc_id": idx[kb]["__doc__"], "chunk_id": cid, "usage": usage})
    add("kb_cs_reply", b, "validate")
    refs.append({"kb_id": "kb_cs_conf", "doc_id": "CONF", "chunk_id": "*", "usage": "validate"})
    add("kb_cs_close", b, "reference")
    for t in it["doc_tools"]:
        add("kb_cs_tools", t, "reference")
    for r in spec["doc"]["ledger"]:
        if b in [x.strip() for x in r["映射到intent_bucket"].replace("或", "/").split("/")] or r["映射到intent_bucket"] == b:
            add("kb_cs_ledger", r["台账意图"], "reference")
    return refs


def apply(db, spec: dict) -> dict:
    sp = db.get(Space, SPACE)
    if sp is None:
        raise SystemExit("找不到 cs 空间")
    need = {b["tool"] for it in spec["intents"] for b in it["tools"]} | set(spec["confirm_tools"])
    have = set(db.scalars(select(Tool.name).where(Tool.name.in_(need))))
    if need - have:
        raise SystemExit(f"工具目录里缺少：{sorted(need - have)}（先执行 python -m app.westmonth import）")
    rep: dict = {}
    # 1) 旧意图 / 导入记录
    old = list(db.scalars(select(Intent).where(Intent.space_id == SPACE).order_by(Intent.code)))
    rep["deleted_intents"] = [{"code": i.code, "name": i.name, "status": i.status} for i in old]
    for i in old:
        db.delete(i)
    recs = list(db.scalars(select(ImportRecord).where(ImportRecord.space_id == SPACE)))
    rep["deleted_import_records"] = [r.id for r in recs]
    for r in recs:
        db.delete(r)
    db.flush()
    # 2) 知识库
    kbs = target_kbs(db)
    rep["deleted_kbs"] = [{"id": k.id, "name": k.name, "docs": len(k.documents),
                           "chunks": sum(len(d.chunks) for d in k.documents)} for k in kbs]
    for k in kbs:
        kb_svc.delete_kb(db, k, ACTOR)
    db.flush()
    # 3) 分类
    db.execute(delete(Category).where(Category.space_id == SPACE, Category.depth > 1))
    db.execute(delete(Category).where(Category.space_id == SPACE))
    sp.levels = spec["space"]["levels"]
    cat_ids = {}
    for pos, c in enumerate(spec["categories"], 1):
        n = Category(space_id=SPACE, parent_id=None, depth=1, code=c["code"], name=c["name"], position=pos)
        db.add(n)
        db.flush()
        cat_ids[c["code"]] = n.id
    # 4) 槽位
    want = {s["name"]: s for s in spec["slots"]}
    rep["deleted_slots"] = []
    for s in list(db.scalars(select(SlotDef).where(SlotDef.space_id == SPACE))):
        if s.name not in want:
            rep["deleted_slots"].append(s.name)
            db.delete(s)
    db.flush()
    for pos, s in enumerate(spec["slots"], 1):
        cur = db.scalar(select(SlotDef).where(SlotDef.space_id == SPACE, SlotDef.name == s["name"]))
        if cur is None:
            cur = slot_svc.create(db, SPACE, s, ACTOR)
        cur.display_name, cur.type, cur.default_ask = s["display_name"], s["type"], s["default_ask"]
        cur.options = s["options"] if s["type"] == "枚举" else []
        cur.position = pos
    db.flush()
    # 5) 成交类工具标记需确认
    for t in db.scalars(select(Tool).where(Tool.name.in_(spec["confirm_tools"]))):
        if not (t.config or {}).get("confirm_required"):
            t.config = {**(t.config or {}), "confirm_required": True}
            audit.record(db, ACTOR, "tool.update", "tool", t.name, "成交类工具：标记写操作需确认（映射维护表 R1–R3）")
    # 6) 知识库
    idx = build_kbs(db, spec)
    rep["kbs"] = [{"id": k["id"], "name": k["name"], "docs": 1, "chunks": len(k["rows"])} for k in kb_specs(spec)]
    db.flush()
    # 7) 意图
    rep["intents"] = []
    for it in spec["intents"]:
        row = intent_svc.create(db, sp, {"code": it["code"], "name": it["name"], "category_id": cat_ids[it["category"]]}, ACTOR)
        row.owner = spec["owner"]
        db.flush()
        data = {"description": it["description"], "examples": it["examples"], "slots": it["slots"],
                "kb_refs": kb_refs_for(it, spec, idx), "tools": it["tools"]}
        try:
            with db.begin_nested():
                intent_svc.update(db, row, {**data, "status": "启用"}, ACTOR)
            st, why = "启用", []
        except Exception as e:  # noqa: BLE001
            why = (getattr(e, "details", None) or {}).get("problems") or [str(e)]
            intent_svc.update(db, row, data, ACTOR)
            st = "草稿"
        rep["intents"].append({"code": it["code"], "name": it["name"], "status": st, "problems": why,
                               "tools": [b["tool"] for b in it["tools"]], "kb_refs": len(data["kb_refs"])})
    sp.version += 1
    audit.record(db, ACTOR, "space.rebuild", "space", SPACE, "按《意图 × 工具调用 映射维护表 v1》重建 1688 智能客服意图与知识库", SPACE,
                 {"intents": len(rep["intents"]), "deleted_intents": len(rep["deleted_intents"])})
    return rep


def status(db) -> dict:
    its = list(db.scalars(select(Intent).where(Intent.space_id == SPACE)))
    return {"intents": len(its), "enabled": sum(i.status == "启用" for i in its),
            "bindings": sum(len(i.tools or []) for i in its), "kb_refs": sum(len(i.kb_refs or []) for i in its),
            "kbs": {k.id: [k.name, len(k.documents), sum(len(d.chunks) for d in k.documents)]
                    for k in db.scalars(select(KnowledgeBase).where(KnowledgeBase.id.like(OWN_PREFIX + "%")))}}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m app.cs1688")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backup")
    b.add_argument("--out", required=True)
    a = sub.add_parser("apply")
    a.add_argument("--yes", action="store_true")
    a.add_argument("--backup")
    a.add_argument("--report")
    sub.add_parser("status")
    args = ap.parse_args(argv)
    with SessionLocal() as db:
        if args.cmd == "backup":
            print(json.dumps(backup(db, Path(args.out)), ensure_ascii=False))
        elif args.cmd == "status":
            print(json.dumps(status(db), ensure_ascii=False, indent=1))
        else:
            if not args.yes:
                sys.exit("会删除 cs 空间的旧意图和示例知识库，确认请加 --yes")
            if args.backup:
                print("备份：", json.dumps(backup(db, Path(args.backup)), ensure_ascii=False))
            rep = apply(db, load())
            db.commit()
            if args.report:
                Path(args.report).write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
            print(json.dumps({k: (len(v) if isinstance(v, list) and k.startswith("deleted_intents") else v) for k, v in rep.items()},
                             ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
