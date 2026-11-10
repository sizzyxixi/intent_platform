"""数据模型（SQLAlchemy 2）。字段含义见 docs/意图管理平台_全局需求文档.md 第 7 节。"""
from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text,
    UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _now():
    return func.now()


class Space(Base):
    __tablename__ = "spaces"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    bot: Mapped[str] = mapped_column(String(100))
    code_prefix: Mapped[str] = mapped_column(String(4), unique=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    # 空间设置（识别与兜底）
    threshold: Mapped[float] = mapped_column(Float, default=0.6)
    max_candidates: Mapped[int] = mapped_column(Integer, default=3)
    max_clarify: Mapped[int] = mapped_column(Integer, default=2)
    max_slot_ask: Mapped[int] = mapped_column(Integer, default=2)
    tool_retry: Mapped[int] = mapped_column(Integer, default=1)
    final_action: Mapped[str] = mapped_column(String(32), default="general_assistant")
    clarify_prompt: Mapped[str] = mapped_column(Text, default="我想确认一下，您是想要以下哪一项？")
    final_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    recognition_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recognition_updated_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # 分类层级名称（含最末层「意图」），如 ["大类", "问题桶", "意图"]；分类层数 = len - 1（0～5）
    levels: Mapped[list] = mapped_column(JSONB, default=lambda: ["大类", "问题桶", "意图"])
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now(), onupdate=_now())


class Category(Base):
    """分类节点（邻接表）。depth 从 1 开始；意图挂在 depth == 分类层数 的叶子节点上。"""
    __tablename__ = "categories"
    __table_args__ = (
        Index("ix_categories_space_parent", "space_id", "parent_id"),
        Index("uq_categories_code", "space_id", "code", unique=True, postgresql_where=text("code IS NOT NULL")),
        Index("uq_categories_sibling_name", "space_id", text("coalesce(parent_id, 0)"), "name", unique=True),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    space_id: Mapped[str] = mapped_column(ForeignKey("spaces.id", ondelete="CASCADE"))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id", ondelete="CASCADE"), nullable=True)
    depth: Mapped[int] = mapped_column(Integer)
    code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    name: Mapped[str] = mapped_column(String(100))
    position: Mapped[int] = mapped_column(Integer, default=0)
    parent: Mapped["Category | None"] = relationship(remote_side=[id])


class SlotDef(Base):
    __tablename__ = "slots"
    __table_args__ = (UniqueConstraint("space_id", "name"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    space_id: Mapped[str] = mapped_column(ForeignKey("spaces.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    display_name: Mapped[str] = mapped_column(String(100))
    type: Mapped[str] = mapped_column(String(8))  # 文本/数字/日期/枚举
    options: Mapped[list] = mapped_column(JSONB, default=list)
    default_ask: Mapped[str] = mapped_column(Text, default="")
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now(), onupdate=_now())


class Intent(Base):
    __tablename__ = "intents"
    __table_args__ = (
        Index("ix_intents_space_category", "space_id", "category_id"),
        Index("ix_intents_slots", "slots", postgresql_using="gin", postgresql_ops={"slots": "jsonb_path_ops"}),
        Index("ix_intents_kb_refs", "kb_refs", postgresql_using="gin", postgresql_ops={"kb_refs": "jsonb_path_ops"}),
        Index("ix_intents_tools", "tools", postgresql_using="gin", postgresql_ops={"tools": "jsonb_path_ops"}),
    )
    code: Mapped[str] = mapped_column(String(80), primary_key=True)
    space_id: Mapped[str] = mapped_column(ForeignKey("spaces.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    # 所在分类叶子节点；空间分类层数为 0 时为空
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id", ondelete="RESTRICT"), nullable=True)
    description: Mapped[str] = mapped_column(Text, default="")
    examples: Mapped[list] = mapped_column(JSONB, default=list)
    # [{name, required, ask}]
    slots: Mapped[list] = mapped_column(JSONB, default=list)
    # [{kb_id, doc_id, chunk_id, usage}]  usage: reference / validate
    kb_refs: Mapped[list] = mapped_column(JSONB, default=list)
    # [{tool, param_map:{param:{source,value}}}]
    tools: Mapped[list] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(8), default="草稿", index=True)
    owner: Mapped[str] = mapped_column(String(100), default="")
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())
    category: Mapped[Category | None] = relationship()
    logs: Mapped[list["IntentLog"]] = relationship(
        back_populates="intent", cascade="all, delete-orphan", order_by="IntentLog.id")


class IntentLog(Base):
    __tablename__ = "intent_logs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    intent_code: Mapped[str] = mapped_column(ForeignKey("intents.code", ondelete="CASCADE"), index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())
    by: Mapped[str] = mapped_column(String(100))
    message: Mapped[str] = mapped_column(Text)
    intent: Mapped[Intent] = relationship(back_populates="logs")


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    type: Mapped[str] = mapped_column(String(20))  # 表格知识库 / 图文知识库-富文本
    headers: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    tags: Mapped[dict] = mapped_column(JSONB, default=dict)
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())
    documents: Mapped[list["Document"]] = relationship(
        back_populates="kb", cascade="all, delete-orphan", order_by="Document.id")


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("kb_id", "doc_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kb_id: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True)
    doc_id: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(8), default="构建中")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())
    kb: Mapped[KnowledgeBase] = relationship(back_populates="documents")
    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="Chunk.position")


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("kb_id", "chunk_id"),
        Index("ix_chunks_search", "search_vector", postgresql_using="gin"),
        Index("ix_chunks_content_trgm", "content", postgresql_using="gin",
              postgresql_ops={"content": "gin_trgm_ops"}),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kb_id: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    chunk_id: Mapped[str] = mapped_column(String(60))
    position: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    cells: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    tags: Mapped[dict] = mapped_column(JSONB, default=dict)
    search_vector = mapped_column(TSVECTOR, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())
    document: Mapped[Document] = relationship(back_populates="chunks")


class Tool(Base):
    __tablename__ = "tools"
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    type: Mapped[str] = mapped_column(String(8))  # http / mcp / cli / skill
    display_name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text)
    params: Mapped[list] = mapped_column(JSONB, default=list)  # [{name,type,required,description}]
    source: Mapped[str] = mapped_column(String(10), default="external")  # external / builtin
    maintenance: Mapped[bool] = mapped_column(Boolean, default=False)
    space_scoped: Mapped[bool] = mapped_column(Boolean, default=False)
    endpoints: Mapped[list] = mapped_column(JSONB, default=list)
    mcp_tools: Mapped[list] = mapped_column(JSONB, default=list)
    # 类型特有字段：http{url,method,auth,output_example} mcp{mcp_server,mcp_tool,input_schema,output_example}
    # cli{bin,command_template,auth,login_command,install,verified_version,output_format,confirm_required,output_description}
    # skill{skill_id,platform,output_description}
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner: Mapped[str] = mapped_column(String(100), default="")
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())


class ImportRecord(Base):
    __tablename__ = "import_records"
    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    kind: Mapped[str] = mapped_column(String(8))  # 意图 / 知识
    space_id: Mapped[str | None] = mapped_column(ForeignKey("spaces.id", ondelete="CASCADE"), nullable=True, index=True)
    kb_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    file: Mapped[str] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(40))  # 文件上传 / AI 工具产出 / AI 工具（API）
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())
    by: Mapped[str] = mapped_column(String(100))
    result: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(8), default="构建成功")
    details: Mapped[dict] = mapped_column(JSONB, default=dict)


class ApiKey(Base):
    __tablename__ = "api_keys"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    owner: Mapped[str] = mapped_column(String(100), default="")
    token_hash: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    token_hint: Mapped[str] = mapped_column(String(40), default="")
    spaces: Mapped[list] = mapped_column(JSONB, default=list)
    scopes: Mapped[list] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(8), default="有效")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(10), default="viewer")  # admin / editor / viewer
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_entity", "entity_type", "entity_id"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now(), index=True)
    actor_type: Mapped[str] = mapped_column(String(10))  # user / api_key / system
    actor_id: Mapped[str] = mapped_column(String(64))
    actor_name: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(40))
    entity_type: Mapped[str] = mapped_column(String(20))
    entity_id: Mapped[str] = mapped_column(String(120))
    space_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
    request_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (UniqueConstraint("api_key_id", "key"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    api_key_id: Mapped[str] = mapped_column(String(40))
    key: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    status_code: Mapped[int] = mapped_column(Integer)
    response_body: Mapped[dict | list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now(), index=True)


class RateCounter(Base):
    __tablename__ = "rate_counters"
    api_key_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    window: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # epoch minute
    count: Mapped[int] = mapped_column(Integer, default=0)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB, default=dict)
