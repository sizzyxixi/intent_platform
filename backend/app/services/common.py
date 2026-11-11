import re
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

TZ_CN = timezone(timedelta(hours=8))
TAG_KEYS = ("platform", "site", "language", "category")
INTENT_CODE_RE = re.compile(r"^[A-Z]{1,4}(\.[A-Z0-9_]+){1,6}$")
CATEGORY_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,31}$")
MAX_CATEGORY_LEVELS = 5
SLOT_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
STATUSES = ("草稿", "启用", "停用")
SLOT_TYPES = ("文本", "数字", "日期", "枚举", "文件", "图片")
FILE_SLOT_TYPES = ("文件", "图片")
FINAL_ACTIONS = {"handoff_human": "转人工", "general_assistant": "交给通用助手", "decline": "告知无法处理"}
USAGE_LABEL = {"reference": "参考", "validate": "校验"}
KB_TYPES = ("表格知识库", "图文知识库-富文本")
TABLE_EXT = (".xlsx", ".xls", ".csv", ".json", ".jsonl")
TEXT_EXT = (".docx", ".doc", ".pdf", ".pptx", ".ppt", ".html", ".htm", ".md", ".png", ".jpg", ".jpeg", ".webp")


@dataclass
class Actor:
    type: str  # user / api_key / system
    id: str
    name: str
    role: str | None = None
    ip: str | None = None
    request_id: str | None = None


SYSTEM = Actor("system", "system", "系统")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_local(s: str | None) -> datetime | None:
    """演示数据里的时间是北京时间字符串（2026-11-10 10:25:00 / 2026-11-10）。"""
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=TZ_CN)
        except ValueError:
            continue
    return None


def norm_tags(t: dict | None) -> dict:
    t = t or {}
    out = {}
    for k in TAG_KEYS:
        v = t.get(k) or []
        if isinstance(v, str):
            v = [x.strip() for x in re.split(r"[,，]", v) if x.strip()]
        out[k] = [str(x).strip() for x in v if str(x).strip()]
    return out


def effective_tags(kb_tags: dict | None, chunk_tags: dict | None) -> dict:
    kt, ct = norm_tags(kb_tags), norm_tags(chunk_tags)
    return {k: (ct[k] if ct[k] else kt[k]) for k in TAG_KEYS}


def tag_hit(eff: dict, filters: dict) -> bool:
    for k in TAG_KEYS:
        v = filters.get(k)
        if v and eff.get(k) and v not in eff[k]:
            return False
    return True


def split_list(s, seps=r"[,，]") -> list[str]:
    if isinstance(s, list):
        return [str(x).strip() for x in s if str(x).strip()]
    return [x.strip() for x in re.split(seps, s or "") if x.strip()]
