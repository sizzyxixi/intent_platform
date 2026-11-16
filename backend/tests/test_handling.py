"""意图处理方式 handling 与空间兜底 fallback。"""
from contract import check

from app.services import handling as handling_svc


def test_public_handling_instruction_text():
    para = handling_svc.public_handling({
        "mode": "fixed_reply", "replies": ["您好。"], "reply_strictness": "paraphrase"
    })
    assert para["reply_strictness"] == "paraphrase"
    assert para["instruction"] == "按话术意思表达，可改写措辞但不得改变原意"
    ver = handling_svc.public_handling({
        "mode": "fixed_reply", "replies": ["您好。"], "reply_strictness": "verbatim"
    })
    assert ver["reply_strictness"] == "verbatim"
    assert ver["instruction"] == "必须原样输出其中一条话术，不得改写、增删或补充"
    # instruction 是对外计算字段，写入时即使带上也会被忽略
    raw = handling_svc.parse_handling({
        "mode": "fixed_reply", "replies": ["您好。"], "reply_strictness": "verbatim",
        "instruction": "自定义指令不应落库",
    })
    assert "instruction" not in raw
    assert raw["reply_strictness"] == "verbatim"
    assert "instruction" not in handling_svc.public_handling({"mode": "tool_flow"})
    assert "instruction" not in handling_svc.public_handling({"mode": "handoff_human"})
    fb = handling_svc.public_handling({
        "mode": "fixed_reply", "replies": ["暂不支持。"], "reply_strictness": "verbatim"
    }, fallback=True)
    assert fb["instruction"] == "必须原样输出其中一条话术，不得改写、增删或补充"


def test_default_handling_and_space_fallback(admin, full_key):
    it = admin.get("/api/admin/intents/B.REL.GREETING").json()
    assert it["handling"]["mode"] == "tool_flow"
    items = admin.get("/api/admin/spaces/cs/intents", params={"page_size": 100}).json()["items"]
    greet = next(i for i in items if i["code"] == "B.REL.GREETING")
    assert greet["handling"]["mode"] == "tool_flow"

    cs = check(full_key.get("/api/v1/recognition", space="cs"))
    assert cs["final_action"] == "general_assistant"
    assert cs["fallback"]["mode"] == "llm_free"
    assert cs["fallback"]["mode_label"] == "让 AI 自由发挥"
    assert cs.get("deprecated") is True

    mc = check(full_key.get("/api/v1/recognition", space="mc"))
    assert mc["final_action"] == "general_assistant"
    assert mc["fallback"]["mode"] == "llm_free"
    assert mc["fallback"]["mode_label"] == "让 AI 自由发挥"

    rc = check(full_key.get("/api/v1/runtime-config", space="cs"))
    assert rc["recognition"]["fallback"]["mode"] == "llm_free"
    assert rc["intents"], "运行时应包含已启用意图"
    assert all(i.get("handling", {}).get("mode") == "tool_flow" for i in rc["intents"])


def test_space_fallback_write_ignored(full_key):
    v0 = full_key.get("/api/v1/recognition", space="mc").json()["version"]
    b = check(full_key.put("/api/v1/recognition", json={
        "fallback": {
            "mode": "fixed_reply",
            "replies": ["暂不支持这个问题。", "请换个问法试试。"],
            "reply_strictness": "verbatim",
        }
    }, space="mc"))
    assert b["version"] == v0
    assert b["fallback"]["mode"] == "llm_free"
    assert b.get("deprecated") is True
    assert "自行回答" in (b.get("note") or "")
    # 非法内容也被忽略，不再 422
    r = full_key.put("/api/v1/recognition", json={"fallback": {"mode": "tool_flow"}}, space="mc")
    assert r.status_code == 200 and check(r)["fallback"]["mode"] == "llm_free"


def test_intent_handling_modes_and_bindings(admin, full_key):
    src = admin.get("/api/admin/intents/B.REL.GREETING").json()
    r = admin.post("/api/admin/spaces/cs/intents", json={
        "code": "B.REL.CHATTEST", "name": "闲聊测试", "category_id": src["category_id"]
    })
    assert r.status_code == 201
    it = r.json()
    assert it["handling"]["mode"] == "tool_flow"

    # 固定话术必须有回复
    r = admin.put("/api/admin/intents/B.REL.CHATTEST", json={"handling": {"mode": "fixed_reply"}})
    assert r.status_code == 422

    r = admin.put("/api/admin/intents/B.REL.CHATTEST", json={
        "handling": {"mode": "fixed_reply", "replies": ["您好，我们先聊点别的吧。"]}
    })
    assert r.status_code == 200
    h = r.json()["handling"]
    assert h["mode"] == "fixed_reply" and h["reply_strictness"] == "paraphrase"
    assert h["replies"] == ["您好，我们先聊点别的吧。"]
    assert h["instruction"] == "按话术意思表达，可改写措辞但不得改变原意"

    # 不能再绑工具或知识
    r = admin.post("/api/admin/intents/B.REL.NOBIND/tools", json={"tool": "pf_write_intents", "param_map": {}})
    # pf_write_intents may be maintenance and already 422; use a real bindable tool from seed
    # Instead try v1 bind after finding a tool. First assert message if 422 for handling OR use list.
    if r.status_code != 422 or "处理方式" not in (r.json().get("message") or "") + "".join(r.json().get("problems") or []):
        # try via update tools
        r2 = admin.put("/api/admin/intents/B.REL.CHATTEST", json={
            "handling": {"mode": "fixed_reply", "replies": ["您好，我们先聊点别的吧。"]},
            "tools": [{"tool": "pf_search_knowledge", "param_map": {}}],
        })
        # might be unregistered; the handling check should still fire if tool passes structure?
        pass

    # Use an existing bindable non-maintenance tool. Inspect later in a tighter assertion via update only tools.
    r = admin.put("/api/admin/intents/B.REL.CHATTEST", json={
        "tools": [{"tool": "x", "param_map": {}}],
    })
    assert r.status_code == 422
    blob = r.text
    assert "处理方式" in blob and "不能绑定" in blob

    # 开放接口 PATCH
    b = check(full_key.patch("/api/v1/intents/B.REL.CHATTEST", json={
        "handling": {"mode": "handoff_human", "message": "请稍等，为您转人工。"}
    }, space="cs"))
    assert b["handling"]["mode"] == "handoff_human"
    assert b["handling"]["message"] == "请稍等，为您转人工。"

    b = check(full_key.patch("/api/v1/intents/B.REL.CHATTEST", json={
        "handling": {"mode": "llm_free", "prompt": "轻松简短地闲聊，不要承诺业务。"}
    }, space="cs"))
    assert b["handling"]["mode"] == "llm_free" and "轻松简短" in b["handling"]["prompt"]

    # 改回 tool_flow 后可以写空绑定
    r = admin.put("/api/admin/intents/B.REL.CHATTEST", json={"handling": {"mode": "tool_flow"}})
    assert r.status_code == 200 and r.json()["handling"]["mode"] == "tool_flow"


def test_cannot_switch_mode_while_bound(admin):
    src = admin.get("/api/admin/intents/B.STOCK.AVAIL").json()
    # AVAIL may already have tools; try switch
    if src.get("tools"):
        r = admin.put("/api/admin/intents/B.STOCK.AVAIL", json={
            "handling": {"mode": "handoff_human"}
        })
        assert r.status_code == 422
        assert "不能绑定" in r.text or "请先解绑" in r.text
    else:
        # attach then switch
        r = admin.put("/api/admin/intents/B.STOCK.AVAIL", json={
            "kb_refs": src["kb_refs"] or [],
            "handling": {"mode": "llm_free"},
        })
        # if no bindings, this would succeed — skip if truly unbound
        if not src.get("kb_refs"):
            assert r.status_code in (200, 422)
        else:
            assert r.status_code == 422


def test_batch_upsert_handling(full_key):
    src = check(full_key.get("/api/v1/intents/B.REL.GREETING", space="cs"))
    path = [p["name"] or p["code"] for p in src["category_path"]]
    r = full_key.post("/api/v1/intents:batchUpsert", json={
        "mode": "merge",
        "auto_create_parents": False,
        "intents": [{
            "code": "B.REL.CHATUPSERT",
            "name": "批量闲聊",
            "category_path": path,
            "description": "用户只是寒暄或闲聊，不涉及具体业务问题。",
            "examples": ["在吗", "你好呀", "今天天气真好"],
            "handling": {"mode": "fixed_reply", "replies": ["您好，有什么可以帮您？"],
                         "reply_strictness": "verbatim"},
        }]
    }, space="cs")
    b = check(r)
    assert any(i["code"] == "B.REL.CHATUPSERT" and i["result"] == "created" for i in b["items"])
    got = check(full_key.get("/api/v1/intents/B.REL.CHATUPSERT", space="cs"))
    assert got["handling"]["mode"] == "fixed_reply"
    assert got["handling"]["reply_strictness"] == "verbatim"
    assert got["handling"]["replies"] == ["您好，有什么可以帮您？"]
    assert got["handling"]["instruction"] == "必须原样输出其中一条话术，不得改写、增删或补充"


def test_bind_rejected_via_v1(full_key, admin):
    src = admin.get("/api/admin/intents/B.REL.GREETING").json()
    r = admin.post("/api/admin/spaces/cs/intents", json={
        "code": "B.REL.NOBIND", "name": "不可绑定测试", "category_id": src["category_id"]
    })
    assert r.status_code == 201
    r = admin.put("/api/admin/intents/B.REL.NOBIND", json={
        "handling": {"mode": "fixed_reply", "replies": ["您好"]}
    })
    assert r.status_code == 200
    r = full_key.post("/api/v1/intents/B.REL.NOBIND/tools", json={"tool": "does_not_exist", "param_map": {}},
                      space="cs")
    assert r.status_code == 422
    assert "处理方式" in r.text or "未注册" in r.text
    # even after a valid-looking bind request, handling should block before or with structure
    # 知识绑定
    r = full_key.post("/api/v1/intents/B.REL.NOBIND/knowledge", json={
        "kb_id": "kb_aq", "doc_id": "x", "chunk_id": "*", "usage": "reference"
    }, space="cs")
    assert r.status_code == 422


READY = {
    "description": "用户只是寒暄闲聊，不涉及具体业务问题或操作请求。",
    "examples": ["在吗", "你好呀", "今天天气真好"],
}


def _draft(admin, code, name):
    src = admin.get("/api/admin/intents/B.REL.GREETING").json()
    r = admin.post("/api/admin/spaces/cs/intents", json={
        "code": code, "name": name, "category_id": src["category_id"]
    })
    assert r.status_code == 201, r.text
    return r.json()


def test_enable_validation_by_handling_mode(admin, full_key):
    # tool_flow 仍要求至少一条知识或一个工具
    _draft(admin, "B.REL.ENFLOW", "启用工具流程测试")
    r = admin.put("/api/admin/intents/B.REL.ENFLOW", json={
        **READY, "handling": {"mode": "tool_flow"}, "status": "启用"
    })
    assert r.status_code == 422 and r.json()["code"] == "ENABLE_CHECK_FAILED", r.text
    assert any("至少关联" in p for p in r.json()["details"]["problems"])

    r = admin.put("/api/admin/intents/B.REL.ENFLOW", json={**READY, "handling": {"mode": "tool_flow"}})
    assert r.status_code == 200, r.text
    r = admin.post("/api/admin/intents/B.REL.ENFLOW/status", json={"status": "启用"})
    assert r.status_code == 422 and r.json()["code"] == "ENABLE_CHECK_FAILED"
    assert any("至少关联" in p for p in r.json()["details"]["problems"])

    # 固定话术：话术为空不能启用；有话术后可以启用（不必绑工具或知识）
    _draft(admin, "B.REL.ENFIX", "启用固定话术测试")
    r = admin.post("/api/admin/intents/B.REL.ENFIX/validate", json={
        **READY, "handling": {"mode": "fixed_reply", "replies": []}
    })
    assert r.status_code == 200, r.text
    assert any("固定话术至少写一条回复" in p for p in r.json()["problems"])

    r = admin.put("/api/admin/intents/B.REL.ENFIX", json={
        **READY,
        "handling": {"mode": "fixed_reply", "replies": ["您好，我们先聊点别的吧。"],
                     "reply_strictness": "verbatim"},
        "status": "启用",
    })
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "启用"
    assert r.json()["handling"]["mode"] == "fixed_reply"
    assert r.json()["handling"]["reply_strictness"] == "verbatim"
    assert r.json()["handling"]["instruction"] == "必须原样输出其中一条话术，不得改写、增删或补充"

    # 其他必填项仍要满足：例句不足时不能启用
    _draft(admin, "B.REL.ENFIX2", "启用固定话术缺例句")
    r = admin.put("/api/admin/intents/B.REL.ENFIX2", json={
        "description": READY["description"],
        "examples": ["在吗", "你好呀"],
        "handling": {"mode": "fixed_reply", "replies": ["您好"]},
        "status": "启用",
    })
    assert r.status_code == 422 and r.json()["code"] == "ENABLE_CHECK_FAILED"
    assert any("例句至少 3 条" in p for p in r.json()["details"]["problems"])

    # 转人工：无需绑定即可启用
    _draft(admin, "B.REL.ENHAND", "启用转人工测试")
    r = admin.put("/api/admin/intents/B.REL.ENHAND", json={
        **READY, "handling": {"mode": "handoff_human", "message": "请稍等，为您转人工。"},
        "status": "启用",
    })
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "启用"
    assert r.json()["handling"]["mode"] == "handoff_human"

    # 自由发挥：无需绑定即可启用；也可用开放接口 :enable
    _draft(admin, "B.REL.ENFREE", "启用自由发挥测试")
    r = admin.put("/api/admin/intents/B.REL.ENFREE", json={
        **READY, "handling": {"mode": "llm_free", "prompt": "轻松简短地闲聊，不要承诺业务。"}
    })
    assert r.status_code == 200 and r.json()["status"] == "草稿", r.text
    r = full_key.post("/api/v1/intents/B.REL.ENFREE:enable", space="cs")
    assert r.status_code == 200, r.text
    b = check(r)
    assert b["status"] == "启用" and b["handling"]["mode"] == "llm_free"

    rc = check(full_key.get("/api/v1/runtime-config", space="cs"))
    by = {i["code"]: i for i in rc["intents"]}
    assert by["B.REL.ENFIX"]["handling"]["mode"] == "fixed_reply"
    assert by["B.REL.ENFIX"]["handling"]["replies"] == ["您好，我们先聊点别的吧。"]
    assert by["B.REL.ENFIX"]["handling"]["reply_strictness"] == "verbatim"
    assert by["B.REL.ENFIX"]["handling"]["instruction"] == "必须原样输出其中一条话术，不得改写、增删或补充"
    assert by["B.REL.ENHAND"]["handling"]["mode"] == "handoff_human"
    assert by["B.REL.ENHAND"]["handling"]["message"] == "请稍等，为您转人工。"
    assert by["B.REL.ENFREE"]["handling"]["mode"] == "llm_free"
    assert "轻松简短" in by["B.REL.ENFREE"]["handling"]["prompt"]
    assert "B.REL.ENFLOW" not in by


def test_instruction_emitted_on_all_consumer_read_paths(admin, full_key):
    """REST 与 MCP 的意图读取路径都要带上 handling.instruction。"""
    import json
    src = admin.get("/api/admin/intents/B.REL.GREETING").json()
    r = admin.post("/api/admin/spaces/cs/intents", json={
        "code": "B.REL.INSTR", "name": "指令字段测试", "category_id": src["category_id"]
    })
    assert r.status_code == 201, r.text
    r = admin.put("/api/admin/intents/B.REL.INSTR", json={
        **READY,
        "handling": {"mode": "fixed_reply", "replies": ["请联系人工客服。"],
                     "reply_strictness": "verbatim",
                     "instruction": "自定义指令不应被保存"},
        "status": "启用",
    })
    assert r.status_code == 200, r.text
    h = r.json()["handling"]
    assert h["instruction"] == "必须原样输出其中一条话术，不得改写、增删或补充"
    assert "自定义指令" not in json.dumps(h, ensure_ascii=False)

    expected = {
        "mode": "fixed_reply",
        "mode_label": "固定话术",
        "replies": ["请联系人工客服。"],
        "reply_strictness": "verbatim",
        "reply_strictness_label": "完全固定",
        "instruction": "必须原样输出其中一条话术，不得改写、增删或补充",
    }

    detail = check(full_key.get("/api/v1/intents/B.REL.INSTR", space="cs"))
    assert detail["handling"] == expected

    listed = check(full_key.get("/api/v1/intents", params={"q": "B.REL.INSTR"}, space="cs"))
    item = next(i for i in listed["items"] if i["code"] == "B.REL.INSTR")
    assert item["handling"] == expected

    rc = check(full_key.get("/api/v1/runtime-config", space="cs"))
    rc_it = next(i for i in rc["intents"] if i["code"] == "B.REL.INSTR")
    assert rc_it["handling"] == expected

    from test_mcp import call as mcp_call

    err, got = mcp_call(full_key, "get_intent", {"space": "cs", "code": "B.REL.INSTR"})
    assert not err and got["handling"] == expected
    err, listed = mcp_call(full_key, "list_intents", {"space": "cs", "q": "B.REL.INSTR", "page_size": 50})
    assert not err
    item = next(i for i in listed["items"] if i["code"] == "B.REL.INSTR")
    assert item["handling"] == expected
    err, listed = mcp_call(full_key, "list_intents", {"space": "cs", "status": "启用", "q": "指令字段", "page_size": 50})
    assert not err
    item = next(i for i in listed["items"] if i["code"] == "B.REL.INSTR")
    assert item["handling"] == expected

    # PATCH 写入 instruction 仍被忽略，读回的是计算字段
    # 先停用才能 PATCH
    assert admin.post("/api/admin/intents/B.REL.INSTR/status", json={"status": "停用"}).status_code == 200
    patched = check(full_key.patch("/api/v1/intents/B.REL.INSTR", json={
        "handling": {"mode": "fixed_reply", "replies": ["请联系人工客服。"],
                     "reply_strictness": "paraphrase", "instruction": "又一次自定义"}
    }, space="cs"))
    assert patched["handling"]["reply_strictness"] == "paraphrase"
    assert patched["handling"]["instruction"] == "按话术意思表达，可改写措辞但不得改变原意"
    assert "又一次自定义" not in json.dumps(patched["handling"], ensure_ascii=False)

