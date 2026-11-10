"""启用前必填校验（需求文档 5.1「启用前必填校验」第 1～5 条）。"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Category, Chunk, Document, McpServer, SlotDef, Space, Tool
from .common import INTENT_CODE_RE


class RefIndex:
    """按需加载知识切片、工具、槽位，供批量校验复用。"""

    def __init__(self, db: Session):
        self.db = db
        self._docs: dict[str, dict[str, set[str]]] = {}
        self._tools: dict[str, Tool] | None = None
        self._slots: dict[str, dict[str, SlotDef]] = {}
        self._cats: dict[str, tuple[int, dict[int, int]]] = {}
        self._servers: set[str] | None = None

    def kb_docs(self, kb_id: str) -> dict[str, set[str]]:
        if kb_id not in self._docs:
            m: dict[str, set[str]] = {}
            rows = self.db.execute(
                select(Document.doc_id, Chunk.chunk_id).select_from(Document)
                .join(Chunk, Chunk.document_id == Document.id, isouter=True).where(Document.kb_id == kb_id))
            for doc_id, chunk_id in rows:
                m.setdefault(doc_id, set())
                if chunk_id:
                    m[doc_id].add(chunk_id)
            self._docs[kb_id] = m
        return self._docs[kb_id]

    def ref_ok(self, r: dict) -> bool:
        docs = self.kb_docs(r.get("kb_id", ""))
        d = docs.get(r.get("doc_id", ""))
        if d is None:
            return False
        return r.get("chunk_id") == "*" or r.get("chunk_id") in d

    def tools(self) -> dict[str, Tool]:
        if self._tools is None:
            self._tools = {t.name: t for t in self.db.scalars(select(Tool))}
        return self._tools

    def servers(self) -> set[str]:
        if not hasattr(self, "_servers") or self._servers is None:
            self._servers = set(self.db.scalars(select(McpServer.name)))
        return self._servers

    def slots(self, space_id: str) -> dict[str, SlotDef]:
        if space_id not in self._slots:
            self._slots[space_id] = {s.name: s for s in self.db.scalars(
                select(SlotDef).where(SlotDef.space_id == space_id))}
        return self._slots[space_id]

    def categories(self, space_id: str) -> tuple[int, dict[int, int]]:
        """(分类层数, {分类 id: depth})"""
        if space_id not in self._cats:
            sp = self.db.get(Space, space_id)
            n = len(sp.levels or [""]) - 1 if sp else 0
            self._cats[space_id] = (n, dict(self.db.execute(
                select(Category.id, Category.depth).where(Category.space_id == space_id)).all()))
        return self._cats[space_id]

    def invalidate(self):
        self._servers = None
        self._cats.clear()
        self._docs.clear()
        self._tools = None
        self._slots.clear()


def effective_ask(slot: dict, defs: dict[str, SlotDef]) -> str:
    d = defs.get(slot.get("name"))
    return (slot.get("ask") or "").strip() or (d.default_ask if d else "")


def validate(it: dict, idx: RefIndex) -> list[str]:
    """it: {code,name,description,examples,slots,kb_refs,tools,space_id}。返回缺项（中文）。"""
    e: list[str] = []
    if not (it.get("name") or "").strip():
        e.append("名称不能为空")
    if not INTENT_CODE_RE.match(it.get("code") or ""):
        e.append("编码格式应为 前缀.分类编码.意图（大写，如 B.STOCK.AVAIL / M.DESIGN.MAIN）")
    if "category_id" in it:
        n, depth = idx.categories(it["space_id"])
        cid = it.get("category_id")
        if n and (cid is None or depth.get(cid) != n):
            e.append("未放到最末一层分类下，请在「所属分类」里选择")
    desc = (it.get("description") or "").strip()
    if not desc:
        e.append("意图描述不能为空")
    elif len(desc) < 15:
        e.append(f"意图描述至少 15 个字（当前 {len(desc)} 个字），请写清用户想做什么、什么算、什么不算")
    ex = [x for x in (it.get("examples") or []) if str(x).strip()]
    if len(ex) < 3:
        e.append(f"例句至少 3 条（当前 {len(ex)} 条）")
    defs = idx.slots(it["space_id"])
    names = [s.get("name") for s in it.get("slots") or []]
    if any(n and names.count(n) > 1 for n in names):
        e.append("槽位重复")
    for s in it.get("slots") or []:
        n = s.get("name")
        if not n:
            e.append("存在未选择的槽位")
        elif n not in defs:
            e.append(f"槽位「{n}」不在本空间槽位列表中")
        elif s.get("required") and not effective_ask(s, defs):
            e.append(f"必填槽位「{n}」缺少追问话术")
    refs = it.get("kb_refs") or []
    binds = it.get("tools") or []
    if not refs and not binds:
        e.append("至少关联 1 条知识或 1 个工具")
    for r in refs:
        if not idx.ref_ok(r):
            e.append(f"关联的知识切片已失效：{r.get('kb_id')}/{r.get('doc_id')}/{r.get('chunk_id')}")
    tools = idx.tools()
    slot_names = {s.get("name") for s in it.get("slots") or []}
    for b in binds:
        t = tools.get(b.get("tool"))
        if not t:
            if b.get("tool") in idx.servers():
                e.append(f"「{b.get('tool')}」是 MCP 服务，不能整体绑定，请下钻选择具体工具")
            else:
                e.append(f"工具未注册：{b.get('tool')}")
            continue
        if t.maintenance:
            e.append(f"工具 {t.name} 是维护类平台内置工具，不能在意图中使用")
        if t.remote_missing:
            e.append(f"工具 {t.name} 在 MCP 服务 {t.server} 最近一次同步中已不存在，请更换工具")
        elif not t.enabled:
            e.append(f"工具 {t.name} 已停用")
        pm = b.get("param_map") or {}
        known = {p["name"] for p in t.params}
        for k in pm:
            if k not in known:
                e.append(f"工具 {t.name} 没有入参「{k}」")
        for p in t.params:
            m = pm.get(p["name"])
            if p.get("required") and (not m or not str(m.get("value") or "").strip()):
                e.append(f"工具 {t.name} 的必填入参「{p['name']}」未配置映射")
            if m and m.get("source") == "槽位" and m.get("value") and m["value"] not in slot_names:
                e.append(f"工具 {t.name} 入参「{p['name']}」映射的槽位「{m['value']}」不存在")
    return e
