"""知识检索：可插拔接口。默认实现基于 PostgreSQL 全文检索（中文字二元组 + 英文词，array_to_tsvector）
与 pg_trgm（word_similarity）。以后可实现 VectorSearchBackend 替换，不改调用方。"""
import re
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..config import get_settings
from .common import effective_tags, tag_hit

_CJK = r"\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af"
_TOKEN_RE = re.compile(rf"[{_CJK}]+|[a-z0-9]+(?:[.'][a-z0-9]+)*")


def tokens(s: str) -> list[str]:
    out = []
    for m in _TOKEN_RE.finditer((s or "").lower()):
        w = m.group(0)
        if re.match(rf"[{_CJK}]", w):
            if len(w) == 1:
                out.append(w)
            else:
                out.extend(w[i:i + 2] for i in range(len(w) - 1))
        else:
            out.append(w)
    return out


@dataclass
class Hit:
    chunk_pk: int
    score: float


class SearchBackend(Protocol):
    def index_sql(self) -> str: ...

    def index_params(self, content: str) -> dict: ...

    def search(self, db: Session, query: str, kb_id: str | None, filters: dict, top_k: int,
               threshold: float) -> list[Hit]: ...


class PostgresSearchBackend:
    name = "postgres"

    def index_sql(self) -> str:
        return "array_to_tsvector(CAST(:toks AS text[]))"

    def index_params(self, content: str) -> dict:
        return {"toks": sorted(set(tokens(content)))}

    def reindex(self, db: Session, chunk_pks: list[int]) -> None:
        if not chunk_pks:
            return
        rows = db.execute(text("SELECT id, content FROM chunks WHERE id = ANY(:ids)"), {"ids": chunk_pks}).all()
        for pk, content in rows:
            db.execute(text(f"UPDATE chunks SET search_vector = {self.index_sql()} WHERE id = :id"),
                       {"id": pk, **self.index_params(content)})

    def search(self, db, query, kb_id, filters, top_k, threshold):
        q_toks = sorted(set(tokens(query)))
        if not q_toks:
            return []
        tsq = " | ".join("'" + t.replace("'", "''") + "'" for t in q_toks)
        sql = text("""
            SELECT c.id, c.content, c.tags, k.tags AS kb_tags,
                   word_similarity(:q, c.content) AS ws,
                   tsvector_to_array(c.search_vector) AS toks
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            JOIN knowledge_bases k ON k.id = c.kb_id
            WHERE d.status = '构建成功'
              AND (CAST(:kb AS text) IS NULL OR c.kb_id = CAST(:kb AS text))
              AND (c.search_vector @@ CAST(:tsq AS tsquery) OR c.content ILIKE :like)
            ORDER BY ts_rank(c.search_vector, CAST(:tsq AS tsquery)) DESC
            LIMIT 500""")
        rows = db.execute(sql, {"q": query, "kb": kb_id, "tsq": tsq, "like": f"%{query.strip()}%"}).all()
        qs = set(q_toks)
        hits = []
        for pk, content, ctags, ktags, ws, toks in rows:
            if not tag_hit(effective_tags(ktags, ctags), filters):
                continue
            cs = set(toks or [])
            inter = len(qs & cs)
            recall = inter / len(qs)
            precision = inter / max(len(cs), 1)
            score = 0.75 * recall + 0.25 * precision
            if query.strip() and query.strip().lower() in (content or "").lower():
                score = max(score, 0.9 if len(query.strip()) > 1 else 0.5)
            score = max(score, float(ws or 0))
            score = round(min(score, 1.0), 2)
            if score >= threshold:
                hits.append(Hit(pk, score))
        hits.sort(key=lambda h: -h.score)
        return hits[:top_k]


_backend = None


def backend() -> PostgresSearchBackend:
    global _backend
    if _backend is None:
        name = get_settings().search_backend
        if name != "postgres":
            raise RuntimeError(f"未知的检索后端 {name}")
        _backend = PostgresSearchBackend()
    return _backend
