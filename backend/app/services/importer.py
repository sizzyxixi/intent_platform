"""意图导入：文件（JSON / CSV / Excel / Markdown）与开放接口 batchUpsert 共用同一套判定与入库逻辑。"""
import csv
import io
import json
import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..errors import ApiError, bad_request
from ..models import ImportRecord, Intent, Space
from . import handling as handling_svc
from . import intents as intent_svc
from . import slots as slot_svc
from . import spaces as space_svc
from . import taxonomy as tax_svc
from .common import INTENT_CODE_RE, Actor, utcnow
from .validation import RefIndex, validate

IGNORED_KEYS = ("fallback", "human", "handoff", "兜底", "兜底条件", "转人工", "转人工条件")
CSV_HEADERS = ["编码", "名称", "分类路径", "意图描述", "例句", "槽位"]
SLOT_STR_RE = re.compile(r"^([^\s(（]+)\s*(?:[(（](必填)?(?:[:：])?(.*?)[)）])?$")


def next_import_id(db: Session) -> str:
    n = db.execute(select(func.nextval("import_seq"))).scalar_one()
    return f"IMP-{n}"


# ---------------- 解析 ----------------
def _slot_from(s) -> dict | None:
    if isinstance(s, str):
        s = s.strip()
        if not s:
            return None
        m = SLOT_STR_RE.match(s)
        if m:
            return {"name": m.group(1), "required": bool(m.group(2)), "ask": (m.group(3) or "").strip()}
        return {"name": s, "required": False, "ask": ""}
    if isinstance(s, dict):
        n = str(s.get("name") or s.get("n") or "").strip()
        if not n:
            return None
        req = s.get("required")
        if req is None:
            req = s.get("req") == "必填"
        return {"name": n, "required": bool(req), "ask": str(s.get("ask") or "").strip()}
    return None


def normalize_item(o: dict) -> dict:
    ex = o.get("examples", o.get("ex"))
    if isinstance(ex, str):
        ex = [x.strip() for x in re.split(r"[｜|\n]", ex)]
    slots = o.get("slots") or []
    if isinstance(slots, str):
        slots = [x for x in re.split(r"[；;]", slots)]
    ignored = []
    for k in IGNORED_KEYS:
        v = o.get(k)
        if v not in (None, "", [], {}):
            ignored.append(k)
    return {
        "code": str(o.get("code") or "").strip(),
        "name": str(o.get("name") or "").strip(),
        "category_path": tax_svc.path_segments(o),
        "description": str(o.get("description") or o.get("definition") or o.get("def") or "").strip(),
        "examples": [str(x).strip() for x in (ex or []) if str(x).strip()],
        "slots": [x for x in (_slot_from(s) for s in slots) if x],
        "ignored_fields": ignored,
        "_present": {k for k in ("code", "name", "description", "examples", "handling") if o.get(k) not in (None, "")},
        "handling": o.get("handling"),
        "handling_error": None,
    }


def _rows_to_items(rows: list[list[str]]) -> list[dict]:
    if not rows:
        raise bad_request("文件为空", "EMPTY_FILE")
    hd = [str(h or "").strip().lstrip("\ufeff") for h in rows[0]]
    if "意图描述" not in hd and "定义" in hd:
        hd[hd.index("定义")] = "意图描述"
    need = ["编码", "名称", "意图描述", "例句"]
    miss = [x for x in need if x not in hd]
    if "分类路径" not in hd and "L1" not in hd:  # L1/L2 两列是 0.5 版模板，继续兼容
        miss.insert(2, "分类路径")
    if miss:
        raise bad_request("表头缺少：" + "、".join(miss) + "。模板表头：" + ",".join(CSV_HEADERS), "HEADER_MISMATCH",
                          expected=CSV_HEADERS, actual=hd)
    out = []
    for r in rows[1:]:
        if not any(str(c or "").strip() for c in r):
            continue
        g = lambda n: (str(r[hd.index(n)] or "").strip() if n in hd and hd.index(n) < len(r) else "")
        fb = g("兜底条件") or (("转人工：" + g("转人工条件")) if g("转人工条件") else "")
        out.append(normalize_item({"code": g("编码"), "name": g("名称"), "category_path": g("分类路径"),
                                   "l1": g("L1"), "l2": g("L2"),
                                   "description": g("意图描述"), "examples": g("例句"), "slots": g("槽位"),
                                   "fallback": fb}))
    return out


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise bad_request("无法识别文件编码，请使用 UTF-8", "BAD_ENCODING")


def parse_file(filename: str, raw: bytes) -> list[dict]:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    try:
        if ext == "json":
            j = json.loads(_decode(raw))
            lst = j if isinstance(j, list) else (j.get("intents") or [])
            items = [normalize_item(x) for x in lst if isinstance(x, dict)]
        elif ext in ("csv",):
            items = _rows_to_items(list(csv.reader(io.StringIO(_decode(raw)))))
        elif ext in ("xlsx",):
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
            ws = wb.worksheets[0]
            items = _rows_to_items([["" if c is None else str(c) for c in row] for row in ws.iter_rows(values_only=True)])
        elif ext in ("md", "markdown", "txt"):
            items = parse_markdown(_decode(raw))
        else:
            raise bad_request("只支持 .json / .csv / .xlsx / .md 文件", "UNSUPPORTED_EXTENSION")
    except ApiError:
        raise
    except Exception as e:  # noqa: BLE001
        raise bad_request(f"解析失败：{e}", "PARSE_ERROR")
    if not items:
        raise bad_request("未解析到任何意图，请检查文件格式", "EMPTY_FILE")
    return items


def parse_markdown(text: str) -> list[dict]:
    out = []
    for block in re.split(r"\n(?=##\s)", "\n" + text):
        m = re.match(r"^\s*##\s+(\S+)\s+(.+)", block)
        if not m:
            continue
        o: dict = {"code": m.group(1), "name": m.group(2).strip()}
        for line in block.split("\n")[1:]:
            x = re.match(r"^(归属|意图描述|定义|例句|槽位|转人工|兜底)[：:]\s*(.*)$", re.sub(r"^\s*[-*]\s*", "", line))
            if not x:
                continue
            k, v = x.group(1), x.group(2).strip()
            if k == "归属":
                o["category_path"] = [x for x in re.split(r"\s*[/／]\s*", v) if x.strip()]
            elif k in ("意图描述", "定义"):
                o["description"] = v
            elif k == "例句":
                o["examples"] = [s.strip() for s in re.split(r"[｜|]", v)]
            elif k == "槽位":
                o["slots"] = [s.strip() for s in re.split(r"[；;]", v) if s.strip()]
            else:
                o["fallback"] = v
        out.append(normalize_item(o))
    return out


# ---------------- 判定 ----------------
def _slot_map(item_slots: list[dict], defs: dict) -> tuple[list[dict], list[str]]:
    out, created, seen = [], [], set()
    for s in item_slots:
        n = s["name"]
        if n not in defs and n not in created:
            created.append(n)
        if n in seen:
            continue
        seen.add(n)
        d = defs.get(n)
        ask = "" if (d and s.get("ask") == d.default_ask) else (s.get("ask") or "")
        out.append({"name": n, "required": bool(s.get("required")), "ask": ask})
    return out, created


def judge(db: Session, sp: Space, items: list[dict], api: bool = False) -> list[dict]:
    """返回每行判定：type 新增/更新/重复/冲突，reasons（API 冲突码），notes，diff。"""
    t = tax_svc.Tax(db, sp)
    defs = slot_svc.list_slots(db, sp.id)
    defs = {s.name: s for s in defs}
    codes = [i["code"] for i in items if i["code"]]
    existing = {it.code: it for it in db.scalars(select(Intent).where(Intent.code.in_(codes)))} if codes else {}
    names = {n: c for c, n in db.execute(select(Intent.code, Intent.name).where(Intent.space_id == sp.id))}
    name_to_code = {}
    for c, n in names.items():
        name_to_code.setdefault(n, c)
    rows, seen = [], set()
    for i, x in enumerate(items):
        r = {"index": i, "data": x, "type": "新增", "action": "导入", "issues": [], "reasons": [], "notes": [],
             "diff": [], "path_invalid": False, "path_options": [], "new_slots": []}

        def issue(code, msg):
            r["issues"].append(msg)
            r["reasons"].append(code)

        ex = existing.get(x["code"])
        if api:
            missing = [k for k in ("code", "name", "description", "examples") if not x.get(k)]
            if t.n and not x["category_path"]:
                missing.insert(2, "category_path")
            if missing:
                issue("MISSING_FIELD", "缺少字段：" + "、".join(missing))
        segs = x["category_path"]
        root = t.match_child(None, segs[0]) if (t.n and segs) else None
        if not INTENT_CODE_RE.match(x["code"]):
            issue("INVALID_CODE", "编码格式无效（应为 前缀.分类编码.意图，大写）")
        elif not x["code"].startswith(sp.code_prefix + "."):
            issue("INVALID_CODE", f"编码前缀应为本空间的 {sp.code_prefix}")
        elif root is not None and root.code and not x["code"].startswith(f"{sp.code_prefix}.{root.code}."):
            issue("INVALID_CODE", f"编码应以 {sp.code_prefix}.{root.code}. 开头（与第一层分类「{root.name}」一致）")
        if ex and ex.space_id != sp.id:
            issue("CODE_IN_OTHER_SPACE", f"编码已被其他空间（{ex.space_id}）使用")
            ex = None
        mapped, created = _slot_map(x["slots"], defs)
        x["slots_mapped"], r["new_slots"] = mapped, created
        if x["ignored_fields"]:
            r["notes"].append("文件里的旧兜底/转人工条件已忽略；意图处理方式请用 handling 字段配置")
        if x.get("handling") not in (None, "", {}, []):
            try:
                x["handling"] = handling_svc.parse_handling(x["handling"])
            except Exception as e:
                issue("INVALID_HANDLING", getattr(e, "message", None) or str(e))
                x["handling"] = None
        else:
            x["handling"] = None
        if x.get("handling") and ex:
            msg = handling_svc.check_bindings(x["handling"], ex.tools, ex.kb_refs)
            if msg:
                issue("HANDLING_BINDINGS", msg)
        if created:
            r["notes"].append("本空间没有的槽位将自动新建为文本槽位：" + "、".join(created))
        if not x["name"] and "MISSING_FIELD" not in r["reasons"]:
            issue("MISSING_FIELD", "缺少名称")
        if len(x["name"]) > 30:
            issue("MISSING_FIELD", "名称最多 30 个字")
        x["category_id"] = None
        if t.n:
            node, _ = t.resolve(segs) if segs else (None, 0)
            if node:
                x["category_id"] = node.id
            elif root is None and "MISSING_FIELD" not in r["reasons"]:
                issue("UNKNOWN_CATEGORY", f"分类「{segs[0] if segs else ''}」不存在，先调用 POST /api/v1/taxonomy/nodes 或使用 auto_create_parents")
            elif root is not None:
                opts = [o for o in t.leaf_options() if o["path"][0]["name"] == root.name]
                if api or not opts:
                    issue("UNKNOWN_CATEGORY", f"分类路径「{' / '.join(segs)}」不存在"
                                              f"（需要 {t.n} 层：{' / '.join(t.levels[:-1])}）。"
                                              "先调用 POST /api/v1/taxonomy/nodes 或使用 auto_create_parents")
                else:
                    r["path_invalid"] = True
                    r["path_options"] = [{"id": o["id"], "label": o["label"]} for o in opts]
                    r["path_original"] = " / ".join(segs)
                    r["notes"].append(f"分类路径「{' / '.join(segs)}」不存在，请选择")
                    x["category_id"] = opts[0]["id"]
        if x["code"] in seen:
            issue("DUPLICATE_IN_BATCH", "文件内编码重复")
        seen.add(x["code"])
        dn = name_to_code.get(x["name"])
        if x["name"] and dn and dn != x["code"]:
            issue("DUPLICATE_NAME", f"与已有意图 {dn} 同名")
        if api:
            if x["description"] and len(x["description"]) < 15:
                issue("DESCRIPTION_TOO_SHORT", f"意图描述至少 15 个字（当前 {len(x['description'])}）")
            if x.get("examples") and len(x["examples"]) < 3:
                issue("TOO_FEW_EXAMPLES", f"例句至少 3 条（当前 {len(x['examples'])}）")
            for s in mapped:
                d = defs.get(s["name"])
                if s["required"] and not (s["ask"] or (d.default_ask if d else f"请补充{s['name']}")):
                    issue("SLOT_ASK_MISSING", f"必填槽位 {s['name']} 缺少追问话术")
        if r["issues"]:
            r["type"], r["action"] = "冲突", "跳过"
        elif ex:
            r["existing_status"] = ex.status
            new_ex = [e for e in x["examples"] if e not in (ex.examples or [])]
            new_slots = [s for s in mapped if s["name"] not in {y["name"] for y in ex.slots or []}]
            h_changed = bool(x.get("handling") and handling_svc.parse_handling(ex.handling or {"mode": "tool_flow"}) != x["handling"])
            same = ex.name == x["name"] and (not x["description"] or ex.description == x["description"]) \
                and not new_ex and not new_slots and not h_changed
            if same:
                r["type"], r["action"] = "重复", "跳过"
            else:
                r["type"], r["action"] = "更新", "合并"
                if ex.name != x["name"]:
                    r["diff"].append("名称")
                if x["description"] and ex.description != x["description"]:
                    r["diff"].append("意图描述")
                if new_ex:
                    r["diff"].append(f"新例句 {len(new_ex)} 条")
                if new_slots:
                    r["diff"].append(f"新槽位 {len(new_slots)} 个")
                if h_changed:
                    r["diff"].append("处理方式")
        r["actions"] = {"冲突": ["跳过"], "更新": ["合并", "覆盖", "跳过"], "重复": ["跳过", "覆盖"],
                        "新增": ["导入", "跳过"]}[r["type"]]
        rows.append(r)
    return rows


# ---------------- 入库 ----------------
def _apply_one(db: Session, sp: Space, x: dict, action: str, by: str, file: str, idx: RefIndex) -> str:
    """返回 created / merged / overwritten / unchanged。"""
    ex = db.get(Intent, x["code"])
    if not ex:
        pos = (db.scalar(select(func.max(Intent.position)).where(Intent.space_id == sp.id)) or 0) + 1
        it = Intent(code=x["code"], space_id=sp.id, name=x["name"], category_id=x.get("category_id"),
                    description=x["description"], examples=x["examples"], slots=x["slots_mapped"], kb_refs=[],
                    tools=[], handling=x.get("handling") or dict(handling_svc.DEFAULT_HANDLING),
                    status="草稿", owner=by, position=pos, updated_at=utcnow())
        intent_svc.add_log(it, by, f"通过导入新建（{file}）")
        db.add(it)
        db.flush()
        return "created"
    before = intent_svc.as_dict(ex)
    if action == "覆盖":
        ex.name = x["name"] or ex.name
        ex.description = x["description"] or ex.description
        ex.examples = x["examples"] or list(ex.examples)
        if x["slots_mapped"]:
            ex.slots = x["slots_mapped"]
        result = "overwritten"
    else:
        exs = list(ex.examples or [])
        for e in x["examples"]:
            if e not in exs:
                exs.append(e)
        ex.examples = exs
        sl = list(ex.slots or [])
        for s in x["slots_mapped"]:
            if s["name"] not in {y["name"] for y in sl}:
                sl.append(s)
        ex.slots = sl
        if not ex.description:
            ex.description = x["description"]
        if not ex.name:
            ex.name = x["name"]
        result = "merged"
    if x.get("handling"):
        ex.handling = x["handling"]
    if intent_svc.as_dict(ex) == before:
        return "unchanged"
    ex.updated_at = utcnow()
    msg = f"导入{'覆盖' if result == 'overwritten' else '合并'}（{file}）"
    if ex.status == "启用":
        space_svc.bump_versions(db, {sp.id})
        idx.invalidate()
        if validate(intent_svc.as_dict(ex), idx):
            ex.status = "草稿"
            msg += "，校验未通过已转为草稿"
    intent_svc.add_log(ex, by, msg)
    return result


def _ensure_slots(db: Session, sp: Space, rows: list[dict], actor: Actor) -> list[str]:
    created = []
    existing = {s.name for s in slot_svc.list_slots(db, sp.id)}
    for r in rows:
        for n in r["new_slots"]:
            if n not in existing and n not in created:
                slot_svc.ensure_text_slot(db, sp.id, n, actor)
                created.append(n)
    db.flush()
    return created


def result_text(c: dict) -> str:
    parts = [f"{k} {c[k]}" for k in ("新增", "更新", "跳过") if c.get(k)]
    return " · ".join(parts) or "无变更"


def apply_file_import(db: Session, sp: Space, items: list[dict], choices: dict, file: str, source: str,
                      actor: Actor) -> dict:
    """控制台导入：choices = {index: {action, category_id}}，服务端重新判定后只执行允许的动作。"""
    rows = judge(db, sp, items)
    todo = []
    for r in rows:
        ch = choices.get(str(r["index"])) or choices.get(r["index"]) or {}
        act = ch.get("action") or r["action"]
        if act not in r["actions"]:
            act = r["action"]
        if r["path_invalid"] and ch.get("category_id") in [o["id"] for o in r["path_options"]]:
            r["data"]["category_id"] = ch["category_id"]
        r["final"] = act
        if act != "跳过":
            todo.append(r)
    new_slots = _ensure_slots(db, sp, todo, actor)
    idx = RefIndex(db)
    c = {"新增": 0, "更新": 0, "跳过": 0}
    for r in rows:
        if r["final"] == "跳过":
            c["跳过"] += 1
            continue
        res = _apply_one(db, sp, r["data"], r["final"], actor.name, file, idx)
        c["新增" if res == "created" else ("跳过" if res == "unchanged" else "更新")] += 1
    rec = ImportRecord(id=next_import_id(db), kind="意图", space_id=sp.id, file=file, source=source, by=actor.name,
                       result=result_text(c), status="构建成功",
                       details={"counts": c, "new_slots": new_slots})
    db.add(rec)
    audit.record(db, actor, "intent.import", "import", rec.id, f"导入意图 {file}：{rec.result}", sp.id,
                 {"counts": c, "new_slots": new_slots})
    return {"import_id": rec.id, "result": rec.result, "counts": c, "new_slots": new_slots}


def batch_upsert(db: Session, sp: Space, payload: dict, actor: Actor, file: str = "batchUpsert") -> dict:
    mode = payload.get("mode") or "merge"
    if mode not in ("merge", "overwrite"):
        raise bad_request("mode 应为 merge 或 overwrite")
    raw = payload.get("intents")
    if not isinstance(raw, list) or not raw:
        raise bad_request("intents 至少 1 条")
    from ..config import get_settings
    if len(raw) > get_settings().max_batch:
        raise ApiError(413, "PAYLOAD_TOO_LARGE", f"单次最多 {get_settings().max_batch} 条，请拆批",
                       {"count": len(raw)})
    items = [normalize_item(x if isinstance(x, dict) else {}) for x in raw]
    created_nodes = []
    if payload.get("auto_create_parents"):
        for orig, x in zip(raw, items):
            segs = tax_svc.path_segments_rich(orig if isinstance(orig, dict) else {}) or x.get("category_path")
            if not segs:
                continue
            try:
                _, created = tax_svc.ensure_path(db, sp, segs, actor)
                created_nodes.extend(created)
            except ApiError:
                pass  # 仍由 judge 给出 UNKNOWN_CATEGORY
    rows = judge(db, sp, items, api=True)
    conflicts, todo = [], []
    for r in rows:
        x = r["data"]
        if r["type"] == "冲突":
            conflicts.append({"code": x["code"], "reason": r["reasons"][0], "message": "；".join(r["issues"])})
            continue
        if mode == "overwrite" and r.get("existing_status") == "启用":
            conflicts.append({"code": x["code"], "reason": "ENABLED_PROTECTED",
                              "message": "已启用意图不可 overwrite，只能 merge"})
            continue
        todo.append(r)
    new_slots = _ensure_slots(db, sp, todo, actor)
    idx = RefIndex(db)
    out_items, created, updated, skipped = [], 0, 0, len(conflicts)
    for r in todo:
        x = r["data"]
        if r["type"] == "重复" and mode == "merge":
            res = "unchanged"
        else:
            res = _apply_one(db, sp, x, "覆盖" if mode == "overwrite" else "合并", actor.name, f"API {file}", idx)
        if res == "created":
            created += 1
        elif res == "unchanged":
            skipped += 1
        else:
            updated += 1
        out_items.append({"code": x["code"], "result": res, "status": db.get(Intent, x["code"]).status})
    ignored = [{"code": r["data"]["code"], "fields": r["data"]["ignored_fields"]} for r in rows
               if r["data"]["ignored_fields"]]
    c = {"新增": created, "更新": updated, "跳过": skipped}
    rec = ImportRecord(id=next_import_id(db), kind="意图", space_id=sp.id, file=file, source="AI 工具（API）",
                       by=actor.name, result=result_text(c), status="构建成功",
                       details={"counts": c, "conflicts": conflicts, "new_slots": new_slots})
    db.add(rec)
    audit.record(db, actor, "intent.batch_upsert", "import", rec.id, f"API 写入意图：{rec.result}", sp.id,
                 {"mode": mode, "counts": c, "conflicts": conflicts})
    out = {"created": created, "updated": updated, "skipped": skipped, "items": out_items, "conflicts": conflicts,
           "import_id": rec.id, "space": sp.id, "new_slots": new_slots, "ignored_fields": ignored}
    if created_nodes:
        out["created_nodes"] = created_nodes
    return out


def batch_apply(db: Session, sp: Space, payload: dict, actor: Actor) -> dict:
    """一次性写入分类节点和意图：先全部校验，任一失败则全部回滚并按条返回错误。"""
    from . import taxonomy as tax_svc
    from ..errors import unprocessable

    dry = bool(payload.get("dry_run"))
    nodes = payload.get("nodes") or []
    intents = payload.get("intents") or []
    if nodes and not isinstance(nodes, list):
        raise bad_request("nodes 应为数组")
    if intents and not isinstance(intents, list):
        raise bad_request("intents 应为数组")
    if not nodes and not intents:
        raise bad_request("请至少提供 nodes 或 intents")

    errors, node_out, created_nodes = [], [], []
    nested = db.begin_nested()
    try:
        for i, n in enumerate(nodes):
            if not isinstance(n, dict):
                errors.append({"index": i, "kind": "node", "reason": "BAD_REQUEST", "message": "节点应为对象"})
                continue
            try:
                c, action = tax_svc.upsert_node(db, sp, n, actor)
                db.flush()
                node_out.append({**tax_svc.node_dict(c, tax_svc.Tax(db, sp)), "action": action})
                if action == "created":
                    created_nodes.append(c.code)
            except ApiError as e:
                errors.append({"index": i, "kind": "node", "code": (n or {}).get("code"),
                               "reason": e.code, "message": e.message})
        intent_res = None
        if intents and not errors:
            try:
                body = {"mode": payload.get("mode") or "merge", "intents": intents,
                        "auto_create_parents": payload.get("auto_create_parents", True)}
                intent_res = batch_upsert(db, sp, body, actor, file="batchApply")
                for c in intent_res.get("conflicts") or []:
                    errors.append({"kind": "intent", "code": c.get("code"), "reason": c.get("reason"),
                                   "message": c.get("message")})
            except ApiError as e:
                errors.append({"kind": "intents", "reason": e.code, "message": e.message})
        if errors:
            nested.rollback()
            raise unprocessable("批量写入未执行（全部回滚）", "BATCH_FAILED", items=errors)
        result = {"ok": True, "dry_run": dry, "space": sp.id, "nodes": node_out,
                  "created_nodes": created_nodes, "intents": intent_res}
        if dry:
            nested.rollback()
        else:
            nested.commit()
        return result
    except Exception:
        if nested.is_active:
            nested.rollback()
        raise
