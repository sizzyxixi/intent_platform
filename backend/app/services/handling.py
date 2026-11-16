"""意图处理方式，以及空间未匹配时的兜底（同一套 mode）。"""
from ..errors import unprocessable

INTENT_MODES = {
    "tool_flow": "工具与知识流程",
    "handoff_human": "转人工",
    "fixed_reply": "固定话术",
    "llm_free": "让 AI 自由发挥",
}
FALLBACK_MODES = {k: v for k, v in INTENT_MODES.items() if k != "tool_flow"}
REPLY_STRICTNESS = {
    "paraphrase": "按意思表达",
    "verbatim": "完全固定",
}
# 给使用方智能体的短指令；只在对外输出里出现，不落库。
REPLY_INSTRUCTIONS = {
    "paraphrase": "按话术意思表达，可改写措辞但不得改变原意",
    "verbatim": "必须原样输出其中一条话术，不得改写、增删或补充",
}
# 空间设置旧字段 final_action → 新 mode
LEGACY_TO_MODE = {
    "handoff_human": "handoff_human",
    "general_assistant": "llm_free",
    "decline": "fixed_reply",
    "llm_free": "llm_free",
    "fixed_reply": "fixed_reply",
}
MODE_TO_LEGACY = {
    "handoff_human": "handoff_human",
    "llm_free": "general_assistant",
    "fixed_reply": "decline",
}
DEFAULT_HANDLING = {"mode": "tool_flow"}
MSG_MAX = 2000
REPLIES_MAX = 10


def default_fallback(space_id: str) -> dict:
    return {"mode": "handoff_human"} if space_id == "cs" else {"mode": "llm_free"}


def is_tool_flow(h: dict | None) -> bool:
    return (h or {}).get("mode", "tool_flow") == "tool_flow"


def has_bindings(tools=None, kb_refs=None) -> bool:
    return bool(tools) or bool(kb_refs)


def binding_conflict_message(mode: str) -> str:
    label = INTENT_MODES.get(mode, mode)
    return (f"处理方式为「{label}」时不能绑定工具或知识。"
            "请先解绑全部工具和知识，或把处理方式改回「工具与知识流程」。")


def _clean_text(v, field: str, required: bool = False) -> str:
    s = str(v or "").strip()
    if required and not s:
        raise unprocessable(f"{field}不能为空")
    if len(s) > MSG_MAX:
        raise unprocessable(f"{field}不超过 {MSG_MAX} 个字")
    return s


def parse_handling(raw, *, fallback: bool = False) -> dict:
    """校验并清洗 handling / fallback 对象。fallback=True 时不允许 tool_flow。"""
    allowed = FALLBACK_MODES if fallback else INTENT_MODES
    kind = "空间兜底" if fallback else "处理方式"
    if raw is None or raw == "":
        raise unprocessable(f"请提供{kind}")
    if not isinstance(raw, dict):
        raise unprocessable(f"{kind}应为对象")
    mode_in = str(raw.get("mode") or "").strip()
    mode = LEGACY_TO_MODE.get(mode_in, mode_in)
    if mode not in allowed:
        opts = " / ".join(allowed)
        raise unprocessable(f"{kind} mode 应为 {opts}",
                            details=[{"field": "mode", "message": f"{kind} mode 应为 {opts}"}])
    out = {"mode": mode}
    if mode == "handoff_human":
        msg = _clean_text(raw.get("message"), "转人工说明")
        if msg:
            out["message"] = msg
    elif mode == "fixed_reply":
        replies = raw.get("replies")
        if replies is None and raw.get("message"):
            replies = [raw.get("message")]
        if isinstance(replies, str):
            replies = [replies]
        if not isinstance(replies, list) or not replies:
            raise unprocessable("固定话术至少写一条回复",
                                details=[{"field": "replies", "message": "固定话术至少写一条回复"}])
        if len(replies) > REPLIES_MAX:
            raise unprocessable(f"固定话术最多 {REPLIES_MAX} 条")
        cleaned = []
        for i, r in enumerate(replies):
            s = _clean_text(r, f"固定话术第 {i + 1} 条", required=True)
            if s not in cleaned:
                cleaned.append(s)
        out["replies"] = cleaned
        st = str(raw.get("reply_strictness") or "paraphrase").strip()
        if st not in REPLY_STRICTNESS:
            raise unprocessable("reply_strictness 应为 paraphrase（按意思表达）或 verbatim（完全固定）",
                                details=[{"field": "reply_strictness",
                                          "message": "应为 paraphrase 或 verbatim"}])
        out["reply_strictness"] = st
    elif mode == "llm_free":
        prompt = _clean_text(raw.get("prompt") or raw.get("message"), "发挥说明")
        if prompt:
            out["prompt"] = prompt
    return out


def from_legacy(final_action: str | None, final_message: str | None) -> dict:
    mode = LEGACY_TO_MODE.get(final_action or "", "llm_free")
    raw = {"mode": mode}
    msg = (final_message or "").strip()
    if mode == "fixed_reply":
        raw["replies"] = [msg] if msg else ["抱歉，这个问题我暂时处理不了。"]
        raw["reply_strictness"] = "paraphrase"
    elif mode == "handoff_human" and msg:
        raw["message"] = msg
    elif mode == "llm_free" and msg:
        raw["prompt"] = msg
    return parse_handling(raw, fallback=True)


def public_handling(h: dict | None, *, fallback: bool = False) -> dict:
    """对外输出：只带当前 mode 用得到的字段；固定话术另附 instruction。"""
    raw = h or (default_fallback("") if fallback else DEFAULT_HANDLING)
    mode = raw.get("mode") or ("llm_free" if fallback else "tool_flow")
    labels = FALLBACK_MODES if fallback else INTENT_MODES
    out = {"mode": mode, "mode_label": labels.get(mode, mode)}
    if mode == "handoff_human" and raw.get("message"):
        out["message"] = raw["message"]
    if mode == "fixed_reply":
        out["replies"] = list(raw.get("replies") or [])
        st = raw.get("reply_strictness") or "paraphrase"
        if st not in REPLY_STRICTNESS:
            st = "paraphrase"
        out["reply_strictness"] = st
        out["reply_strictness_label"] = REPLY_STRICTNESS[st]
        out["instruction"] = REPLY_INSTRUCTIONS[st]
    if mode == "llm_free" and raw.get("prompt"):
        out["prompt"] = raw["prompt"]
    return out


def legacy_from(h: dict | None) -> tuple[str, str | None]:
    """同步到空间表旧字段 final_action / final_message，兼容已有读取方。"""
    h = h or {}
    mode = h.get("mode") or "llm_free"
    fa = MODE_TO_LEGACY.get(mode, "general_assistant")
    if mode == "fixed_reply":
        replies = h.get("replies") or []
        return fa, (replies[0] if replies else None)
    if mode == "handoff_human":
        return fa, h.get("message")
    return fa, h.get("prompt")


def space_fallback(sp) -> dict:
    raw = getattr(sp, "fallback", None)
    if isinstance(raw, dict) and raw.get("mode"):
        return public_handling(raw, fallback=True)
    return public_handling(from_legacy(getattr(sp, "final_action", None),
                                       getattr(sp, "final_message", None)), fallback=True)


def check_bindings(handling: dict | None, tools=None, kb_refs=None) -> str | None:
    """有冲突时返回错误文案，否则 None。"""
    mode = (handling or {}).get("mode", "tool_flow")
    if mode != "tool_flow" and has_bindings(tools, kb_refs):
        return binding_conflict_message(mode)
    return None
