"""MCP 服务：注册 → 同步（在线 / 手动导入）→ 差异 → 子工具启停 → 意图下钻绑定 → 运行时配置导出。"""
import copy
import json
import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import mini_mcp_server as mini  # noqa: E402
from contract import check  # noqa: E402

PY = sys.executable


@pytest.fixture(scope="module")
def mini_url():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    srv = mini.serve(port)
    yield f"http://127.0.0.1:{port}"
    srv.shutdown()


@pytest.fixture(autouse=True)
def _reset_mini():
    mini.STATE["tools"] = copy.deepcopy(mini.DEFAULT_TOOLS)
    yield


def _sync(admin, name, **body):
    p = admin.post(f"/api/admin/mcp-servers/{name}/sync/preview", json=body)
    assert p.status_code == 200, p.text
    p = p.json()
    r = admin.post(f"/api/admin/mcp-servers/{name}/sync/apply",
                   json={"tools": p["tools"], "source": p["source"], "server_info": p["server_info"]})
    assert r.status_code == 200, r.text
    return p, r.json()


def _register(admin, base, name="shop", **kw):
    body = {"name": name, "display_name": "测试商城 MCP", "transport": "streamable_http", "url": f"{base}/mcp",
            "auth": "Bearer Token", "secret": "s3cr3t-token", "owner": "测试"}
    body.update(kw)
    r = admin.post("/api/admin/mcp-servers", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _bind_intent(admin, tools):
    r = admin.post("/api/admin/spaces/cs/intents", json={"code": "B.STOCK.MCPTEST", "name": "MCP 测试",
                                                         "category_path": ["STOCK", "现货"]})
    assert r.status_code == 201, r.text
    body = {"description": "用户想查订单状态；问物流算；问退款不算",
            "examples": ["我的订单到哪了", "帮我查下订单", "订单发货没"],
            "slots": [{"name": "sku", "required": True, "ask": ""}],
            "kb_refs": [{"kb_id": "kb_aq", "doc_id": "A1", "chunk_id": "Q4", "usage": "reference"}],
            "tools": []}
    assert admin.put("/api/admin/intents/B.STOCK.MCPTEST", json=body).status_code == 200
    return admin.put("/api/admin/intents/B.STOCK.MCPTEST", json={"tools": tools, "status": "启用"})


def test_seed_migrated_servers(admin):
    ss = {s["name"]: s for s in admin.get("/api/admin/mcp-servers").json()}
    assert set(ss) == {"compliance", "image", "pod", "intent_platform", "sellersprite"}
    sp = ss["sellersprite"]
    assert sp["example_data"] is True and sp["url"] == "https://mcp.sellersprite.com/mcp"
    assert sp["credentials"] == [{"location": "header", "name": "secret-key", "env": "SELLERSPRITE_SECRET_KEY", "has_secret": False}]
    assert sorted(t["mcp_tool"] for t in sp["tools"]) == ["asin_detail", "competitor_lookup", "keyword_miner", "product_research"]
    assert [t["name"] for t in ss["compliance"]["tools"]] == ["compliance.check_banned_words", "compliance.check_ip_risk"]
    assert ss["compliance"]["tool_count"] == 2 and ss["intent_platform"]["source"] == "builtin"
    t = next(t for t in admin.get("/api/admin/tools").json() if t["name"] == "compliance.check_ip_risk")
    assert t["server"] == "compliance" and t["mcp_tool"] == "check_ip_risk" and t["used_by"]
    it = admin.get("/api/admin/intents/M.POD.PATTERN").json()
    assert any(b["tool"] == "compliance.check_ip_risk" for b in it["tools"])
    # 内置服务不可同步；MCP 子工具不能单独新建 / 编辑 / 删除
    r = admin.post("/api/admin/mcp-servers/intent_platform/sync/preview", json={})
    assert r.status_code == 403
    r = admin.post("/api/admin/tools", json={"name": "x_mcp", "type": "mcp", "display_name": "x", "description": "d",
                                            "owner": "o", "config": {"mcp_server": "https://a/b", "mcp_tool": "x"}})
    assert r.status_code == 422
    assert admin.put("/api/admin/tools/compliance.check_ip_risk", json={"display_name": "x"}).status_code == 422
    assert admin.delete("/api/admin/tools/compliance.check_ip_risk").status_code == 422


def test_sync_http_diff_and_removed_in_use(admin, mini_url, full_key):
    sv = _register(admin, mini_url)
    assert sv["tool_count"] == 0 and "secret_encrypted" not in sv and "s3cr3t" not in json.dumps(sv)
    assert sv["has_secret"] is True
    # 认证：服务端要求 Bearer，平台用托管的密钥去连
    import os
    os.environ["MINI_MCP_TOKEN"] = "s3cr3t-token"
    try:
        p, res = _sync(admin, "shop")
    finally:
        os.environ.pop("MINI_MCP_TOKEN")
    assert p["source"] == "在线同步" and p["server_info"]["server_info"]["name"] == "mini-mcp"
    assert p["diff"]["summary"] == {"added": 3, "changed": 0, "removed": 0, "unchanged": 0}  # 分页取全 3 个
    sv = admin.get("/api/admin/mcp-servers/shop").json()
    tools = {t["mcp_tool"]: t for t in sv["tools"]}
    assert sv["tool_count"] == 3 and sv["last_sync_source"] == "在线同步"
    assert tools["cancel_order"]["confirm_required"] is True and tools["lookup_order"]["confirm_required"] is False
    assert [x["name"] for x in tools["lookup_order"]["params"]] == ["order_no"]

    # 下钻绑定：不能绑整个服务；绑具体子工具，入参映射按子工具 inputSchema 校验
    r = _bind_intent(admin, [{"tool": "shop", "param_map": {}}])
    assert r.status_code == 422 and "下钻" in r.text
    r = admin.put("/api/admin/intents/B.STOCK.MCPTEST",
                  json={"tools": [{"tool": "shop.lookup_order", "param_map": {"nope": {"source": "常量", "value": "1"}}}],
                        "status": "启用"})
    assert r.status_code == 422 and "没有入参「nope」" in r.text and "order_no" in r.text
    r = admin.put("/api/admin/intents/B.STOCK.MCPTEST", json={
        "tools": [{"tool": "shop.lookup_order", "param_map": {"order_no": {"source": "槽位", "value": "sku"}}},
                  {"tool": "shop.cancel_order", "param_map": {"order_no": {"source": "槽位", "value": "sku"}}}],
        "status": "启用"})
    assert r.status_code == 200 and r.json()["status"] == "启用", r.text

    # 运行时配置：服务连接信息 + 只导出被用到的子工具；密钥只出现环境变量名
    rc = admin.get("/api/admin/spaces/cs/runtime-config").json()
    shop = next(s for s in rc["mcp_servers"] if s["name"] == "shop")
    assert shop["url"].endswith("/mcp") and shop["auth"] == {"type": "Bearer Token", "header": "Authorization",
                                                             "scheme": "Bearer", "token_env": "MCP_SHOP_TOKEN"}
    assert shop["credentials"] == [{"location": "header", "name": "Authorization", "env": "MCP_SHOP_TOKEN", "scheme": "Bearer"}]
    names = [t["name"] for t in rc["tools"]]
    assert "shop.lookup_order" in names and "shop.cancel_order" in names and "shop.search_faq" not in names
    lk = next(t for t in rc["tools"] if t["name"] == "shop.lookup_order")
    assert lk["input_schema"]["required"] == ["order_no"] and lk["mcp_tool"] == "lookup_order"
    it = next(i for i in rc["intents"] if i["code"] == "B.STOCK.MCPTEST")
    assert it["tools"][0]["server"] == "shop" and it["tools"][0]["mcp_tool"] == "lookup_order"
    assert "s3cr3t" not in json.dumps(rc)
    v1 = check(full_key.get("/api/v1/runtime-config", space="cs"))  # 对外接口按 openapi 契约校验
    assert any(s["name"] == "shop" for s in v1["mcp_servers"]) and "s3cr3t" not in json.dumps(v1)

    # 远端变化：cancel_order 消失（被启用意图使用）、search_faq 消失（未使用）、lookup_order 加必填参数、新增 track
    v0 = admin.get("/api/admin/spaces/cs/recognition").json()["version"]
    new = copy.deepcopy(mini.DEFAULT_TOOLS[:1])
    new[0]["inputSchema"]["properties"]["lang"] = {"type": "string"}
    new.append({"name": "track_parcel", "description": "查询包裹轨迹",
                "inputSchema": {"type": "object", "properties": {"no": {"type": "string"}}}})
    mini.STATE["tools"] = new
    p, res = _sync(admin, "shop")
    d = p["diff"]
    assert d["summary"] == {"added": 1, "changed": 1, "removed": 2, "unchanged": 0}
    assert d["changed"][0]["changes"] == ["新增入参 lang"]
    rm = {x["name"]: x for x in d["removed"]}
    assert rm["cancel_order"]["action"] == "保留并标红" and rm["cancel_order"]["used_by"][0]["code"] == "B.STOCK.MCPTEST"
    assert rm["search_faq"]["action"] == "删除" and not rm["search_faq"]["used_by"]
    assert res["deleted"] == ["shop.search_faq"] and res["marked_missing"] == ["shop.cancel_order"]
    assert res["downgraded"] == ["B.STOCK.MCPTEST"]  # 用到已消失工具的启用意图转草稿
    sv = admin.get("/api/admin/mcp-servers/shop").json()
    tools = {t["mcp_tool"]: t for t in sv["tools"]}
    assert tools["cancel_order"]["remote_missing"] is True and sv["missing_count"] == 1
    assert set(tools) == {"lookup_order", "cancel_order", "track_parcel"}
    assert admin.get("/api/admin/spaces/cs/recognition").json()["version"] == v0 + 1
    it = admin.get("/api/admin/intents/B.STOCK.MCPTEST").json()
    assert it["status"] == "草稿" and any("已不存在" in lg["message"] for lg in it["logs"])
    r = admin.put("/api/admin/intents/B.STOCK.MCPTEST", json={"status": "启用"})
    assert r.status_code == 422 and "已不存在" in r.text
    # 被使用的服务不能删除
    r = admin.delete("/api/admin/mcp-servers/shop")
    assert r.status_code == 409 and r.json()["code"] == "TOOL_IN_USE"
    # 远端恢复后重新同步，标红解除
    mini.STATE["tools"] = copy.deepcopy(mini.DEFAULT_TOOLS)
    p, res = _sync(admin, "shop")
    assert any("恢复可用" in c for x in p["diff"]["changed"] for c in x["changes"])
    assert not admin.get("/api/admin/mcp-servers/shop").json()["missing_count"]
    audit = admin.get("/api/admin/audit?q=shop").json()["items"]
    assert any(a["action"] == "mcp.sync" for a in audit)


def test_sync_sse_and_manual_and_tool_toggle(admin, mini_url):
    _register(admin, mini_url, name="shop_sse", transport="sse", url=f"{mini_url}/sse", auth="无", secret=None)
    p, _ = _sync(admin, "shop_sse")
    assert p["diff"]["summary"]["added"] == 3
    # streamable HTTP 以事件流形式返回
    _register(admin, mini_url, name="shop_ev", url=f"{mini_url}/mcp?sse=1", auth="无", secret=None)
    p, _ = _sync(admin, "shop_ev")
    assert p["diff"]["summary"]["added"] == 3
    # 手动导入：粘贴 tools/list 的 JSON-RPC 返回
    _register(admin, "http://127.0.0.1:9", name="manual", auth="无", secret=None)
    raw = json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"tools": mini.DEFAULT_TOOLS[:2]}})
    p, _ = _sync(admin, "manual", tools_json=raw)
    assert p["source"] == "手动导入" and p["diff"]["summary"]["added"] == 2
    r = admin.post("/api/admin/mcp-servers/manual/sync/preview", json={"tools_json": "{not json"})
    assert r.status_code == 400 and r.json()["code"] == "BAD_TOOLS_JSON"
    # 在线同步失败：服务不可达
    r = admin.post("/api/admin/mcp-servers/manual/sync/preview", json={})
    assert r.status_code == 502 and r.json()["code"] == "MCP_SYNC_FAILED"
    # 子工具启停 + 写操作需确认
    r = admin.patch("/api/admin/tools/manual.lookup_order", json={"enabled": False, "confirm_required": True})
    assert r.status_code == 200 and r.json()["enabled"] is False and r.json()["confirm_required"] is True
    r = _bind_intent(admin, [{"tool": "manual.lookup_order", "param_map": {}}])
    assert r.status_code == 422 and "已停用" in r.text
    assert admin.patch("/api/admin/tools/query_stock", json={"enabled": False}).status_code == 422


def test_stdio_gated(admin, monkeypatch):
    script = str(Path(__file__).parent / "mini_mcp_server.py")
    r = admin.post("/api/admin/mcp-servers", json={"name": "local_cli", "display_name": "本地 stdio", "transport": "stdio",
                                                   "command": PY, "args": [script, "--stdio"], "auth": "无", "owner": "测试"})
    assert r.status_code == 201, r.text
    r = admin.post("/api/admin/mcp-servers/local_cli/sync/preview", json={})
    assert r.status_code == 400 and r.json()["code"] == "STDIO_SYNC_DISABLED"
    from app.config import get_settings
    monkeypatch.setenv("IP_MCP_STDIO_ENABLED", "true")
    get_settings.cache_clear()
    try:
        p, _ = _sync(admin, "local_cli")
        assert p["diff"]["summary"]["added"] == 3 and p["server_info"]["server_info"]["version"] == "0.1.0"
    finally:
        monkeypatch.delenv("IP_MCP_STDIO_ENABLED")
        get_settings.cache_clear()


def test_server_validation(admin):
    bad = [{"name": "Bad-Name", "display_name": "x", "transport": "streamable_http", "url": "https://a/mcp"},
           {"name": "ok_name", "display_name": "x", "transport": "streamable_http", "url": "ftp://a"},
           {"name": "ok_name", "display_name": "x", "transport": "stdio"},
           {"name": "ok_name", "display_name": "x", "transport": "carrier_pigeon", "url": "https://a"},
           {"name": "ok_name", "display_name": "x", "transport": "streamable_http", "url": "https://a/mcp"}]  # 缺负责人
    for b in bad:
        assert admin.post("/api/admin/mcp-servers", json=b).status_code == 422, b
    r = admin.post("/api/admin/mcp-servers", json={"name": "compliance", "display_name": "x", "transport": "streamable_http",
                                                   "url": "https://a/mcp", "owner": "o"})
    assert r.status_code == 409
