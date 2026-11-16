"""工具中心列表：服务端分页与筛选；无分页参数时保持数组兼容。"""
from app.models import Tool
from app.db import SessionLocal


def test_admin_tools_unpaged_still_array(tokens, admin):
    r = admin.get("/api/admin/tools")
    assert r.status_code == 200 and isinstance(r.json(), list) and len(r.json()) > 0
    r2 = admin.get("/api/admin/tools?source=builtin")
    assert r2.status_code == 200 and isinstance(r2.json(), list)
    assert all(t["source"] == "builtin" for t in r2.json())


def test_admin_tools_paginated_shape_and_defaults(tokens, admin):
    r = admin.get("/api/admin/tools?page=1")
    assert r.status_code == 200
    d = r.json()
    assert set(d) >= {"items", "total", "page", "page_size", "facets"}
    assert d["page"] == 1 and d["page_size"] == 20
    assert d["total"] >= len(d["items"]) and len(d["items"]) <= 20
    assert "input_schema" not in d["items"][0]
    assert "used_by" in d["items"][0]
    f = d["facets"]
    assert f["tool_count"] + f["mcp_servers"] == f["total"]
    assert "by_type" in f and "by_source" in f


def test_admin_tools_page_size_and_filters(tokens, admin):
    all_rows = admin.get("/api/admin/tools").json()
    n_http = sum(1 for t in all_rows if t["type"] == "http")
    r = admin.get("/api/admin/tools?page=1&page_size=5&type=http")
    d = r.json()
    assert r.status_code == 200 and d["page_size"] == 5 and d["total"] == n_http
    assert all(t["type"] == "http" for t in d["items"]) and len(d["items"]) <= 5

    r = admin.get("/api/admin/tools?page=1&page_size=20&exclude_type=mcp")
    d = r.json()
    assert all(t["type"] != "mcp" for t in d["items"])
    assert d["total"] == sum(1 for t in all_rows if t["type"] != "mcp")

    name = all_rows[0]["name"]
    r = admin.get(f"/api/admin/tools?page=1&page_size=20&q={name}")
    assert r.status_code == 200 and any(t["name"] == name for t in r.json()["items"])

    r = admin.get(f"/api/admin/tools?names={name}")
    d = r.json()
    assert d["total"] == 1 and d["items"][0]["name"] == name

    r = admin.get("/api/admin/tools?page=1&page_size=20&status=启用&exclude_type=mcp")
    assert r.status_code == 200 and r.json()["total"] >= 1

    r = admin.get("/api/admin/tools?page=1&page_size=101")
    assert r.status_code in (400, 422)


def test_admin_tools_cli_pack_filter(tokens, admin):
    with SessionLocal() as db:
        db.add(Tool(name="page_cli_demo", type="cli", display_name="分页命令", description="分页筛选用",
                    params=[], source="external", config={"bin": "xclaw-cli", "command_template": "xclaw-cli demo",
                                                          "auth": "无", "pack": "demo_pack", "pack_name": "演示包",
                                                          "group": "demo-group"}))
        db.commit()
    try:
        r = admin.get("/api/admin/tools?page=1&page_size=20&pack=demo_pack")
        d = r.json()
        assert r.status_code == 200 and d["total"] >= 1
        assert all(t.get("cli", {}).get("pack") == "demo_pack" for t in d["items"])
        packs = [p["pack"] for p in d["facets"]["packs"]]
        assert "demo_pack" in packs
        r2 = admin.get("/api/admin/tools?page=1&page_size=20&type=cli&group=demo-group")
        assert any(t["name"] == "page_cli_demo" for t in r2.json()["items"])
        r3 = admin.get("/api/admin/tools?page=1&page_size=20&q=分页筛选")
        assert any(t["name"] == "page_cli_demo" for t in r3.json()["items"])
    finally:
        with SessionLocal() as db:
            t = db.get(Tool, "page_cli_demo")
            if t:
                db.delete(t)
                db.commit()


def test_admin_tools_group_all_types(tokens, admin):
    body = {
        "type": "http", "name": "page_http_group", "display_name": "分组接口",
        "description": "验证非 CLI 也可以设业务分组", "owner": "测试",
        "params": [{"name": "q", "type": "string", "required": True, "description": "查询"}],
        "config": {"url": "https://example.com/g", "method": "GET", "auth": "无"},
        "group": "商品",
    }
    r = admin.post("/api/admin/tools", json=body)
    assert r.status_code == 201, r.text
    assert r.json()["group"] == "商品"
    try:
        d = admin.get("/api/admin/tools?page=1&page_size=20&group=商品").json()
        assert any(t["name"] == "page_http_group" for t in d["items"])
        names = [g["group"] for g in d["facets"]["groups"]]
        assert "商品" in names
        r = admin.put("/api/admin/tools/page_http_group", json={**body, "group": "选品"})
        assert r.json()["group"] == "选品"
        d2 = admin.get("/api/admin/tools?page=1&page_size=20&group=选品").json()
        assert any(t["name"] == "page_http_group" for t in d2["items"])
    finally:
        admin.delete("/api/admin/tools/page_http_group")
