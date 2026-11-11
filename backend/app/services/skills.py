"""技能包：上传含 SKILL.md 的文件夹（或 .zip）→ 解析前置元数据 name / description → 暂存 → 保存到技能工具即成为新版本。

- 技能没有入参 / 出参：意图抽取的槽位作为上下文交给 AI，输出格式写在意图的回复要求里。
- 包根目录 = 层级最浅的 SKILL.md 所在目录；导出 / 下载时的路径都相对包根。
- 只保存文件，不执行任何脚本。
"""
from __future__ import annotations

import hashlib
import io
import posixpath
import re
import zipfile
from datetime import timedelta

import yaml
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..errors import bad_request, not_found, unprocessable
from ..models import SkillPackage, Tool
from .common import TOOL_NAME_RE, Actor, iso, utcnow

MAX_FILES = 500
FRONT_RE = re.compile(r"^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(\r?\n|$)", re.S)
STAGING_TTL = timedelta(hours=24)


def _clean_path(p: str) -> str | None:
    p = (p or "").replace("\\", "/").strip()
    while p.startswith("./"):
        p = p[2:]
    if not p or p.startswith("/") or re.match(r"^[A-Za-z]:", p):
        return None
    parts = [x for x in p.split("/") if x not in ("", ".")]
    if any(x == ".." for x in parts):
        return None
    if any(x in ("__MACOSX",) or x == ".DS_Store" for x in parts):
        return ""  # 忽略系统垃圾文件
    return "/".join(parts)


def parse_frontmatter(text: str) -> dict:
    text = (text or "").lstrip("\ufeff")
    m = FRONT_RE.match(text)
    if not m:
        raise unprocessable("SKILL.md 缺少 YAML 前置元数据（需以 --- 包围，含 name 和 description）", "SKILL_FRONTMATTER")
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        raise unprocessable(f"SKILL.md 前置元数据不是合法 YAML：{e}", "SKILL_FRONTMATTER")
    if not isinstance(fm, dict):
        raise unprocessable("SKILL.md 前置元数据应为键值对", "SKILL_FRONTMATTER")
    name = str(fm.get("name") or "").strip()
    desc = str(fm.get("description") or "").strip()
    if not name or not desc:
        raise unprocessable("SKILL.md 前置元数据必须包含 name 和 description", "SKILL_FRONTMATTER")
    return {"name": name[:100], "description": desc}


def suggest_tool_name(skill_name: str) -> str | None:
    slug = re.sub(r"[\s\-]+", "_", skill_name.strip().lower())
    return slug if TOOL_NAME_RE.match(slug) and len(slug) <= 64 else None


def _build(entries: list[tuple[str, bytes]], source: str, original: str | None) -> dict:
    """entries: [(相对路径, 内容)] → 规整为包根下的文件，生成 zip。"""
    files: dict[str, bytes] = {}
    for p, data in entries:
        cp = _clean_path(p)
        if cp is None:
            raise bad_request(f"文件路径不合法：{p}", "SKILL_BAD_PATH")
        if cp:
            files[cp] = data
    mds = sorted((p for p in files if posixpath.basename(p).lower() == "skill.md"), key=lambda p: (p.count("/"), p))
    if not mds:
        raise unprocessable("技能包中没有 SKILL.md", "SKILL_NO_MD")
    root = posixpath.dirname(mds[0])
    pre = root + "/" if root else ""
    pkg = {p[len(pre):]: d for p, d in files.items() if p.startswith(pre)}
    if len(pkg) > MAX_FILES:
        raise bad_request(f"技能包最多 {MAX_FILES} 个文件", "SKILL_TOO_MANY")
    md_key = posixpath.basename(mds[0])
    try:
        md = pkg[md_key].decode("utf-8")
    except UnicodeDecodeError:
        raise unprocessable("SKILL.md 需为 UTF-8 文本", "SKILL_FRONTMATTER")
    fm = parse_frontmatter(md)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(pkg):
            z.writestr(p, pkg[p])
    archive = buf.getvalue()
    order = sorted(pkg, key=lambda p: (p != md_key, p))
    return {"name": fm["name"], "description": fm["description"], "skill_md": md,
            "files": [{"path": p, "size": len(pkg[p])} for p in order], "source": source,
            "original_name": original, "size": sum(len(v) for v in pkg.values()), "archive": archive,
            "sha256": hashlib.sha256(archive).hexdigest()}


def parse_upload(files: list[tuple[str, bytes]]) -> dict:
    """files: 上传的 [(文件名或相对路径, 内容)]。单个 .zip 按压缩包解析，否则按文件夹解析。"""
    limit = get_settings().max_upload_mb * 1024 * 1024
    if not files:
        raise bad_request("请上传包含 SKILL.md 的文件夹或 .zip", "SKILL_EMPTY")
    if sum(len(d) for _, d in files) > limit:
        raise bad_request(f"技能包超过 {get_settings().max_upload_mb} MB", "PAYLOAD_TOO_LARGE")
    if len(files) == 1 and files[0][0].lower().endswith(".zip"):
        name, data = files[0]
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            raise bad_request("不是有效的 zip 文件", "SKILL_BAD_ZIP")
        infos = [i for i in z.infolist() if not i.is_dir()]
        if len(infos) > MAX_FILES * 2:
            raise bad_request(f"技能包最多 {MAX_FILES} 个文件", "SKILL_TOO_MANY")
        if sum(i.file_size for i in infos) > limit:
            raise bad_request(f"技能包解压后超过 {get_settings().max_upload_mb} MB", "PAYLOAD_TOO_LARGE")
        return _build([(i.filename, z.read(i)) for i in infos], "zip", posixpath.basename(name.replace("\\", "/")))
    return _build(files, "folder", None)


def stage(db: Session, files: list[tuple[str, bytes]], actor: Actor) -> SkillPackage:
    p = parse_upload(files)
    db.execute(delete(SkillPackage).where(SkillPackage.tool_name.is_(None),
                                          SkillPackage.created_at < utcnow() - STAGING_TTL))
    pkg = SkillPackage(tool_name=None, version=0, created_by=actor.name, **p)
    db.add(pkg)
    db.flush()
    return pkg


def staged_dict(pkg: SkillPackage) -> dict:
    return {"package_id": pkg.id, "name": pkg.name, "description": pkg.description, "skill_md": pkg.skill_md,
            "files": pkg.files, "source": pkg.source, "original_name": pkg.original_name, "size": pkg.size,
            "suggested_tool_name": suggest_tool_name(pkg.name), "sha256": pkg.sha256}


def attach(db: Session, tool_name: str, package_id, current_version: int) -> SkillPackage:
    """把暂存包挂到技能工具上，版本号 = 当前版本 + 1。"""
    try:
        pid = int(package_id)
    except (TypeError, ValueError):
        raise unprocessable("请先上传包含 SKILL.md 的技能包", "SKILL_PACKAGE_REQUIRED")
    pkg = db.get(SkillPackage, pid)
    if not pkg or pkg.tool_name is not None:
        raise unprocessable("技能包不存在或已被使用，请重新上传", "SKILL_PACKAGE_REQUIRED")
    mx = db.scalar(select(func.max(SkillPackage.version)).where(SkillPackage.tool_name == tool_name)) or 0
    pkg.tool_name = tool_name
    pkg.version = max(mx, current_version) + 1
    db.flush()
    return pkg


def config_of(pkg: SkillPackage) -> dict:
    return {"version": pkg.version, "package_id": pkg.id, "skill_name": pkg.name, "files": pkg.files,
            "source": pkg.source, "original_name": pkg.original_name, "sha256": pkg.sha256}


def current(db: Session, t: Tool) -> SkillPackage | None:
    pid = (t.config or {}).get("package_id")
    return db.get(SkillPackage, pid) if pid else None


def versions(db: Session, name: str) -> list[dict]:
    rows = db.scalars(select(SkillPackage).where(SkillPackage.tool_name == name).order_by(SkillPackage.version.desc()))
    return [{"version": p.version, "files": len(p.files or []), "size": p.size, "sha256": p.sha256,
             "created_by": p.created_by, "created_at": iso(p.created_at)} for p in rows]


def archive(db: Session, name: str, version: int | None = None) -> tuple[SkillPackage, bytes]:
    stmt = select(SkillPackage).where(SkillPackage.tool_name == name)
    if version:
        stmt = stmt.where(SkillPackage.version == version)
    pkg = db.scalars(stmt.order_by(SkillPackage.version.desc()).limit(1)).first()
    if not pkg:
        raise not_found(f"技能 {name} 没有技能包" + (f" v{version}" if version else ""), "SKILL_PACKAGE_NOT_FOUND")
    return pkg, pkg.archive


def build_from_texts(files: dict[str, str]) -> dict:
    """种子 / 测试用：{路径: 文本} → 包。"""
    return _build([(p, c.encode("utf-8")) for p, c in files.items()], "folder", None)
