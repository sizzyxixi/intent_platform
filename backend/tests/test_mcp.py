"""MCP（JSON-RPC）端点。"""
import base64
import json


def rpc(api, method, params=None, id_=1, space=None):
    h = api.h(space, idem=False)
    return api.c.post("/mcp", json={"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}}, headers=h)


def call(api, name, args, space=None):
    r = rpc(api, "tools/call", {"name": name, "arguments": args}, space=space)
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    return res["isError"], json.loads(res["content"][0]["text"])


def test_initialize_and_list(bot):
    r = rpc(bot, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t"}})
    assert r.json()["result"]["serverInfo"]["name"] == "intent-platform"
    tools = rpc(bot, "tools/list").json()["result"]["tools"]
    assert [t["name"] for t in tools] == ["list_intents", "get_intent", "upsert_intents", "search_knowledge",
                                          "import_knowledge", "list_tools", "register_tool", "update_tool",
                                          "disable_tool", "intent_bind_tool", "intent_unbind_tool",
                                          "taxonomy_upsert_node", "taxonomy_disable_node", "intent_enable",
                                          "intent_disable", "create_knowledge_base", "intent_bind_knowledge",
                                          "intent_unbind_knowledge"]
    assert rpc(bot, "nope").json()["error"]["code"] == -32601
    assert bot.c.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                      headers=bot.h(idem=False)).status_code == 202


def test_requires_key(client, tokens):
    assert client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).status_code == 401


def test_list_views(full_key):
    err, b = call(full_key, "list_intents", {"space": "mc", "status": "启用"})
    assert not err and b["total"] == 38
    err, b = call(full_key, "list_intents", {"view": "taxonomy"}, space="cs")
    assert len(b["levels"]) == 2 and len(b["categories"]) == 12
    err, b = call(full_key, "list_intents", {"space": "cs", "category": "STOCK/现货"})
    assert b["total"] > 0 and all(i["category_path"][1]["name"] == "现货" for i in b["items"])
    err, b = call(full_key, "list_intents", {"view": "slots", "space": "mc"})
    assert len(b["slots"]) == 24
    err, b = call(full_key, "list_intents", {"view": "recognition", "space": "cs"})
    assert b["fallback"]["mode"] == "llm_free" and b.get("deprecated") is True
    err, b = call(full_key, "list_intents", {"view": "spaces"})
    assert {s["space_id"] for s in b["spaces"]} == {"cs", "mc"}
    err, b = call(full_key, "get_intent", {"code": "B.REL.ESCALATE"})
    assert b["status"] == "启用"
    err, b = call(full_key, "get_intent", {"code": "bad"})
    assert err and b["code"] == "BAD_REQUEST"


def test_space_forbidden_and_scope(bot, linxia):
    err, b = call(bot, "list_intents", {"space": "mc"})
    assert err and b["code"] == "SPACE_FORBIDDEN"
    err, b = call(bot, "upsert_intents", {"mode": "merge", "idempotency_key": "k1", "intents": [
        {"code": "B.STOCK.ZZ", "name": "z", "l1": "STOCK", "l2": "现货", "description": "x" * 20,
         "examples": ["a", "b", "c"]}]})
    assert err and b["code"] == "SCOPE_FORBIDDEN"
    err, b = call(linxia, "list_tools", {})
    assert err and b["code"] == "SCOPE_FORBIDDEN"


def test_upsert_idempotent(linxia):
    args = {"mode": "merge", "idempotency_key": "mcp-1", "intents": [
        {"code": "B.STOCK.MCP_NEW", "name": "MCP 新建", "category_path": ["STOCK", "现货"],
         "description": "通过 MCP 新建的测试意图，用于验证幂等", "examples": ["一", "二", "三"]}]}
    err, b1 = call(linxia, "upsert_intents", args)
    assert not err and b1["created"] == 1
    err, b2 = call(linxia, "upsert_intents", args)
    assert b2 == b1
    args2 = {**args, "intents": [{**args["intents"][0], "name": "改名"}]}
    err, b3 = call(linxia, "upsert_intents", args2)
    assert err and b3["code"] == "IDEMPOTENCY_CONFLICT"


def test_search_and_import(full_key):
    err, b = call(full_key, "search_knowledge", {"query": "有现货吗", "kb_id": "kb_aq", "top_k": 2})
    assert not err and b["items"][0]["chunk"]["chunk_id"] == "Q1"
    csv = "问题,答案,意图编码,适用范围\n能开票吗,能,,全店\n".encode()
    err, t = call(full_key, "import_knowledge", {"kb_id": "kb_aq", "idempotency_key": "imp-1", "files": [
        {"name": "mcp.csv", "content_base64": base64.b64encode(csv).decode()}]})
    assert not err and t["status"] == "构建中"
    r = full_key.get(f"/api/v1/imports/{t['import_id']}")
    assert r.json()["status"] == "构建成功"
    err, b = call(full_key, "list_tools", {"type": "cli"})
    assert len(b["items"]) == 2


def test_ai_guide_mcp_block_matches_tools():
    """AI接入说明.md §9 内嵌的工具定义与 MCP 实际返回的一致。"""
    import json as _json
    from pathlib import Path as _P
    doc = (_P(__file__).resolve().parents[2] / "docs" / "AI接入说明.md").read_text(encoding="utf-8")
    sec = doc[doc.index("## 9. MCP 工具"):]
    block = sec[sec.index("```json") + 7:sec.index("```", sec.index("```json") + 7)]
    tools = _json.loads((_P(__file__).resolve().parents[1] / "app" / "mcp_tools.json").read_text(encoding="utf-8"))
    assert _json.loads(block) == tools
