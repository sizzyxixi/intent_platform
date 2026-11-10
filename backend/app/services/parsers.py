"""知识文档解析：表格库每行一个切片；图文库提取正文后按段落切分。"""
import csv
import hashlib
import io
import json
import re
from html.parser import HTMLParser

from ..errors import bad_request
from .common import TABLE_EXT, TEXT_EXT

MAX_CHUNK = 800


class BuildFailure(Exception):
    """解析阶段无法提取内容（如图片 OCR 未配置），文档记为「构建失败」。"""


def ext_of(name: str) -> str:
    return ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""


def check_ext(kb_type: str, name: str) -> None:
    allowed = TABLE_EXT if kb_type == "表格知识库" else TEXT_EXT
    if ext_of(name) not in allowed:
        raise bad_request(f"文件 {name} 的扩展名不支持，{kb_type}支持：{' '.join(allowed)}", "UNSUPPORTED_EXTENSION",
                          file=name, allowed=list(allowed))


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise bad_request("无法识别文件编码，请使用 UTF-8", "BAD_ENCODING")


def parse_table(name: str, raw: bytes) -> tuple[list[str], list[dict]]:
    e = ext_of(name)
    rows: list[list[str]]
    if e == ".csv":
        rows = [r for r in csv.reader(io.StringIO(_decode(raw)))]
    elif e == ".xlsx":
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        rows = [["" if c is None else str(c) for c in r] for r in wb.worksheets[0].iter_rows(values_only=True)]
    elif e == ".xls":
        import xlrd
        sh = xlrd.open_workbook(file_contents=raw).sheet_by_index(0)
        rows = [[str(c) for c in sh.row_values(i)] for i in range(sh.nrows)]
    elif e in (".json", ".jsonl"):
        txt = _decode(raw)
        if e == ".jsonl":
            objs = [json.loads(l) for l in txt.splitlines() if l.strip()]
        else:
            j = json.loads(txt)
            objs = j if isinstance(j, list) else (j.get("rows") or j.get("items") or [])
        objs = [o for o in objs if isinstance(o, dict)]
        if not objs:
            raise bad_request(f"{name} 中没有数据行", "EMPTY_FILE")
        headers = [str(k) for k in objs[0].keys()]
        rows = [headers] + [[("" if o.get(h) is None else str(o.get(h))) for h in headers] for o in objs]
    else:
        raise bad_request(f"不支持的表格格式 {e}", "UNSUPPORTED_EXTENSION")
    rows = [r for r in rows if any(str(c).strip() for c in r)]
    if not rows:
        raise bad_request(f"{name} 为空", "EMPTY_FILE")
    headers = [str(h).strip() for h in rows[0]]
    while headers and not headers[-1]:
        headers.pop()
    out = []
    for r in rows[1:]:
        cells = {h: (str(r[i]).strip() if i < len(r) else "") for i, h in enumerate(headers)}
        out.append(cells)
    return headers, out


class _Text(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article", "table"}

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip -= 1
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def _split_long(t: str) -> list[str]:
    if len(t) <= MAX_CHUNK:
        return [t]
    out, cur = [], ""
    for s in re.split(r"(?<=[。！？.!?；;])", t):
        if len(cur) + len(s) > MAX_CHUNK and cur:
            out.append(cur)
            cur = ""
        cur += s
        while len(cur) > MAX_CHUNK:
            out.append(cur[:MAX_CHUNK])
            cur = cur[MAX_CHUNK:]
    if cur.strip():
        out.append(cur)
    return [x.strip() for x in out if x.strip()]


def _lines_to_chunks(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        line = re.sub(r"^\s{0,3}(#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|>\s*)", "", line).strip()
        line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
        if line and not re.match(r"^[-=|:\s]+$", line):
            out.extend(_split_long(line))
    return out


def parse_text(name: str, raw: bytes) -> list[str]:
    e = ext_of(name)
    if e in (".md", ".txt"):
        chunks = _lines_to_chunks(_decode(raw))
    elif e in (".html", ".htm"):
        p = _Text()
        p.feed(_decode(raw))
        chunks = _lines_to_chunks("".join(p.parts))
    elif e == ".docx":
        import docx
        d = docx.Document(io.BytesIO(raw))
        text = "\n".join(p.text for p in d.paragraphs)
        for t in d.tables:
            for row in t.rows:
                text += "\n" + " | ".join(c.text.strip() for c in row.cells)
        chunks = _lines_to_chunks(text)
    elif e == ".pptx":
        from pptx import Presentation
        prs = Presentation(io.BytesIO(raw))
        chunks = []
        for slide in prs.slides:
            texts = [sh.text_frame.text for sh in slide.shapes if getattr(sh, "has_text_frame", False) and sh.text_frame.text.strip()]
            t = re.sub(r"\s*\n\s*", "；", "\n".join(texts)).strip("；")
            if t:
                chunks.extend(_split_long(t))
    elif e == ".pdf":
        from pypdf import PdfReader
        r = PdfReader(io.BytesIO(raw))
        chunks = []
        for page in r.pages:
            t = (page.extract_text() or "").strip()
            for para in re.split(r"\n\s*\n", t):
                para = re.sub(r"\s*\n\s*", "", para).strip()
                if para:
                    chunks.extend(_split_long(para))
    elif e in (".doc", ".ppt"):
        raise BuildFailure(f"暂不支持解析旧版 {e} 文件，请另存为 {e}x 后重新导入")
    elif e in (".png", ".jpg", ".jpeg", ".webp"):
        raise BuildFailure("图片 OCR 服务未配置，图片无法提取文字（见 README「知识库」一节）")
    else:
        raise bad_request(f"不支持的格式 {e}", "UNSUPPORTED_EXTENSION")
    if not chunks:
        raise BuildFailure("未从文件中提取到文字内容")
    return chunks


def row_text(headers: list[str], cells: dict) -> str:
    return "\n".join(f"{h}：{cells.get(h, '')}" for h in headers)


def content_hash(parts: list[str]) -> str:
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()
