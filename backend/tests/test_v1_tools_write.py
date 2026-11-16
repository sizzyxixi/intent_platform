"""开放接口工具写入：注册 / 更新 / 停用、绑定意图、权限与幂等。"""
from contract import check


HTTP = {
    "name": "ai_probe_echo",
    "type": "http",
    "display_name": "探针 HTTP",
    "description": "用于开放接口写入测试的占位 HTTP 工具，不连接真实业务。",
    "owner": "测试",
    "params": [{"name": "q", "type": "string", "required": True, "description": "查询词"}],
    "config": {"url": "https://example.com/echo", "method": "GET", "auth": "无"},
}

CLI = {
    "name": "ai_probe_cli",
    "type": "cli",
    "display_name": "探针 CLI",
    "description": "用于开放接口写入测试的占位 CLI 工具。",
    "owner": "测试",
    "params": [{"name": "q", "type": "string", "required": True, "description": "查询词"}],
    "config": {
        "bin": "echo",
        "command_template": "echo {q}",
        "auth": "无",
        "output_format": "text",
        "confirm_required": False,
        "output_description": "标准输出文本",
    },
}

DRAFT = {
    "code": "B.STOCK.TOOLPROBE",
    "name": "工具写入探针意图",
    "category_path": ["STOCK", "现货"],
    "description": "仅用于测试把工具绑定到草稿意图，不会启用。",
    "examples": ["探针问一句甲", "探针问一句乙", "探针问一句丙"],
    "slots": [{"name": "sku", "required": True, "ask": "请问是哪一款？"}],
}


def test_write_requires_tool_scope(linxia, bot):
    r = linxia.post("/api/v1/tools", json=HTTP)
    assert r.status_code == 403 and check(r)["code"] == "SCOPE_FORBIDDEN"
    r = bot.post("/api/v1/tools", json=HTTP)
    assert r.status_code == 403 and r.json()["code"] == "SCOPE_FORBIDDEN"


def test_upsert_dry_run_create_update_idempotency_disable(full_key, admin):
    bad = {**HTTP, "config": {"url": "not-a-url", "method": "GET", "auth": "无"}}
    r = full_key.post("/api/v1/tools", json={**bad, "dry_run": True}, idem=False)
    assert r.status_code == 422
    check(r)

    r = full_key.post("/api/v1/tools", json={**HTTP, "dry_run": True}, idem=False)
    b = check(r)
    assert b["ok"] is True and b["action"] == "created" and b["dry_run"] is True
    assert full_key.get("/api/v1/tools/ai_probe_echo").status_code == 404

    r = full_key.post("/api/v1/tools", json=HTTP)
    assert r.status_code == 201
    b = check(r)
    assert b["action"] == "created" and b["name"] == "ai_probe_echo" and b["enabled"] is True
    assert "secret" not in b and "secret_encrypted" not in b

    got = check(full_key.get("/api/v1/tools/ai_probe_echo"))
    assert got["type"] == "http" and got["config"]["url"] == "https://example.com/echo" if "config" in got else got["url"] == "https://example.com/echo"

    # 幂等重放
    h = full_key.h()
    body = {**HTTP, "display_name": "探针 HTTP 幂等"}
    r1 = full_key.c.post("/api/v1/tools", json=body, headers=h)
    assert r1.status_code in (200, 201)
    r2 = full_key.c.post("/api/v1/tools", json=body, headers=h)
    assert r2.status_code == r1.status_code and r2.json() == r1.json() and r2.headers.get("Idempotent-Replayed") == "true"
    r3 = full_key.c.post("/api/v1/tools", json={**body, "display_name": "另一名字"}, headers=h)
    assert r3.status_code == 409 and r3.json()["code"] == "IDEMPOTENCY_CONFLICT"

    r = full_key.put("/api/v1/tools/ai_probe_echo", json={**HTTP, "display_name": "探针 HTTP 已改"})
    assert check(r)["display_name"] == "探针 HTTP 已改" and r.json()["action"] == "updated"

    r = full_key.post("/api/v1/tools", json=CLI)
    assert r.status_code == 201 and check(r)["type"] == "cli"

    # 不能单独注册 MCP 子工具
    r = full_key.post("/api/v1/tools", json={
        "name": "ai_probe_mcp", "type": "mcp", "display_name": "x", "description": "d" * 15, "owner": "测试",
        "config": {"mcp_tool": "x"}})
    assert r.status_code == 422

    r = full_key.post("/api/v1/tools/ai_probe_echo:disable", json={})
    b = check(r)
    assert b["action"] == "disabled" and b["enabled"] is False
    assert check(full_key.get("/api/v1/tools/ai_probe_echo"))["enabled"] is False

    # 开放接口不能删除
    r = full_key.delete("/api/v1/tools/ai_probe_echo")
    assert r.status_code in (404, 405, 422)

    logs = admin.get("/api/admin/audit?entity_type=tool&q=ai_probe_echo").json()["items"]
    actions = [x["action"] for x in logs]
    assert "tool.create" in actions and "tool.disable" in actions
    assert any(x["actor_type"] == "api_key" for x in logs)


def test_bind_unbind_requires_intent_write_not_tool_write(full_key, linxia, bot):
    assert full_key.post("/api/v1/tools", json=HTTP).status_code in (200, 201)
    b = check(full_key.post("/api/v1/intents:batchUpsert", json={"mode": "merge", "intents": [DRAFT]}, space="cs"))
    assert b["created"] >= 1 or b["updated"] >= 1 or any(i.get("code") == "B.STOCK.TOOLPROBE" for i in b.get("items") or [])

    bind = {"tool": "ai_probe_echo", "param_map": {"q": {"source": "槽位", "value": "sku"}}, "dry_run": True}
    r = full_key.post("/api/v1/intents/B.STOCK.TOOLPROBE/tools", json=bind, space="cs")
    assert check(r)["ok"] is True and r.json()["bind"]["tool"] == "ai_probe_echo"

    # 只读 Key 不能绑
    r = bot.post("/api/v1/intents/B.STOCK.TOOLPROBE/tools",
                 json={"tool": "ai_probe_echo", "param_map": {"q": {"source": "槽位", "value": "sku"}}}, space="cs")
    assert r.status_code == 403 and r.json()["code"] == "SCOPE_FORBIDDEN"

    # 林夏有「意图:写」、没有「工具:写」，可以绑定已有工具
    r = linxia.post("/api/v1/intents/B.STOCK.TOOLPROBE/tools",
                    json={"tool": "ai_probe_echo", "param_map": {"q": {"source": "槽位", "value": "sku"}}}, space="cs")
    it = check(r)
    names = [b["tool"] for b in it["tools"]]
    assert "ai_probe_echo" in names
    pm = next(b["param_map"] for b in it["tools"] if b["tool"] == "ai_probe_echo")
    assert pm["q"]["source"] == "槽位" and pm["q"]["value"] == "sku"

    it = check(linxia.delete("/api/v1/intents/B.STOCK.TOOLPROBE/tools/ai_probe_echo", space="cs"))
    assert "ai_probe_echo" not in [b["tool"] for b in it["tools"]]

    full_key.post("/api/v1/tools/ai_probe_echo:disable", json={})


def test_intent_write_only_key_can_bind_existing_tool(full_key, client):
    """只有「意图:写」、没有「工具:写」的 Key，也能给意图绑定已启用的现有工具。"""
    from app.services import keys
    from app.services.common import SYSTEM
    from app.db import SessionLocal
    from conftest import Api
    assert full_key.post("/api/v1/tools", json={**HTTP, "name": "ai_probe_bind_only"}).status_code in (200, 201)
    check(full_key.post("/api/v1/intents:batchUpsert", json={"mode": "merge", "intents": [{
        **DRAFT, "code": "B.STOCK.BINDONLY", "name": "仅意图写绑工具探针",
    }]}, space="cs"))
    with SessionLocal() as db:
        _, tok = keys.create(db, {"name": "仅意图写", "spaces": ["cs"], "scopes": ["意图:写"]}, SYSTEM)
        db.commit()
    only = Api(client, tok)
    r = only.post("/api/v1/intents/B.STOCK.BINDONLY/tools",
                  json={"tool": "ai_probe_bind_only", "param_map": {"q": {"source": "槽位", "value": "sku"}}},
                  space="cs")
    it = check(r)
    assert "ai_probe_bind_only" in [b["tool"] for b in it["tools"]]
    # 没有「工具:写」则不能注册工具
    r = only.post("/api/v1/tools", json={**HTTP, "name": "should_not_register"})
    assert r.status_code == 403 and r.json()["code"] == "SCOPE_FORBIDDEN"
    only.delete("/api/v1/intents/B.STOCK.BINDONLY/tools/ai_probe_bind_only", space="cs")
    full_key.post("/api/v1/tools/ai_probe_bind_only:disable", json={})


def test_mcp_register_and_disable(full_key):
    from test_mcp import call
    err, b = call(full_key, "register_tool", {**HTTP, "name": "ai_probe_mcp_http", "dry_run": True})
    assert not err and b["ok"] is True
    err, b = call(full_key, "register_tool", {**HTTP, "name": "ai_probe_mcp_http", "idempotency_key": "mcp-reg-1"})
    assert not err and b["action"] in ("created", "updated") and b["name"] == "ai_probe_mcp_http"
    err, b = call(full_key, "disable_tool", {"name": "ai_probe_mcp_http", "idempotency_key": "mcp-dis-1"})
    assert not err and b["action"] == "disabled" and b["enabled"] is False


def test_meta_includes_tool_write(admin):
    scopes = admin.get("/api/admin/meta").json()["scopes"]
    assert "工具:写" in scopes and scopes[-1] == "工具:写"


def test_upsert_and_update_group(full_key):
    body = {**HTTP, "name": "ai_probe_group", "group": "探测分组"}
    r = full_key.post("/api/v1/tools", json=body)
    assert r.status_code == 201
    b = check(r)
    assert b["group"] == "探测分组" and b["name"] == "ai_probe_group"
    got = check(full_key.get("/api/v1/tools/ai_probe_group"))
    assert got["group"] == "探测分组"
    r = full_key.put("/api/v1/tools/ai_probe_group", json={**body, "group": "改后分组"})
    assert check(r)["group"] == "改后分组"
    r = full_key.put("/api/v1/tools/ai_probe_group", json={**body, "group": ""})
    assert check(r)["group"] is None
    full_key.post("/api/v1/tools/ai_probe_group:disable", json={})
