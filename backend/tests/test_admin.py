"""控制台接口：登录与角色、意图编辑、导入、槽位、知识库、工具、Key、审计。"""
import json

from conftest import login


def _user(admin, username, role):
    r = admin.post("/api/admin/users", json={"username": username, "password": "password123", "role": role})
    assert r.status_code == 201, r.text


def test_login_logout_me(client, tokens):
    assert client.get("/api/admin/auth/me").status_code == 401
    r = client.post("/api/admin/auth/login", json={"username": "admin", "password": "bad"})
    assert r.status_code == 401 and r.json()["code"] == "LOGIN_FAILED"
    login(client)
    me = client.get("/api/admin/auth/me").json()
    assert me["role"] == "admin"
    client.post("/api/admin/auth/logout")
    client.cookies.clear()
    assert client.get("/api/admin/auth/me").status_code == 401


def test_roles(admin):
    _user(admin, "ed", "editor")
    _user(admin, "vi", "viewer")
    admin.cookies.clear()
    login(admin, "vi", "password123")
    assert admin.get("/api/admin/spaces/cs/intents?page_size=1").status_code == 200
    r = admin.put("/api/admin/intents/B.REL.GREETING", json={"name": "问候"})
    assert r.status_code == 403 and r.json()["code"] == "ROLE_FORBIDDEN"
    admin.cookies.clear()
    login(admin, "ed", "password123")
    assert admin.put("/api/admin/intents/B.REL.GREETING", json={"name": "问候/开场"}).status_code == 200
    assert admin.get("/api/admin/api-keys").status_code == 403
    assert admin.get("/api/admin/audit").status_code == 403
    assert admin.post("/api/admin/spaces", json={}).status_code == 403


def test_disable_user_invalidates_session(admin, client):
    _user(admin, "ed2", "editor")
    uid = next(u["id"] for u in admin.get("/api/admin/users").json() if u["username"] == "ed2")
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as other:
        login(other, "ed2", "password123")
        assert other.get("/api/admin/auth/me").status_code == 200
        admin.patch(f"/api/admin/users/{uid}", json={"is_active": False})
        assert other.get("/api/admin/auth/me").status_code == 401


def test_intent_list_counts(admin):
    b = admin.get("/api/admin/spaces/cs/intents?page_size=500").json()
    assert b["total"] == 123 and b["all_count"] == 123
    tax = admin.get("/api/admin/spaces/cs/taxonomy").json()
    assert tax["levels"] == ["大类", "问题桶", "意图"] and len(tax["tree"]) == 12
    assert sum(len(a["children"]) for a in tax["tree"]) == 46 and sum(a["count"] for a in tax["tree"]) == 123
    stock = next(a for a in tax["tree"] if a["code"] == "STOCK")
    f = admin.get(f"/api/admin/spaces/cs/intents?category={stock['id']}&page_size=500").json()
    assert f["total"] == stock["count"] and all(i["category_path"][0]["code"] == "STOCK" for i in f["items"])
    assert b["status_counts"] == {"草稿": 108, "启用": 15}
    row = next(i for i in b["items"] if i["code"] == "B.STOCK.AVAIL")
    assert row["kb_count"] >= 2 and row["tool_count"] >= 1
    sp = admin.get("/api/admin/spaces").json()
    assert [(s["id"], s["intent_count"]) for s in sp] == [("cs", 123), ("mc", 44)]


def test_create_edit_enable(admin):
    r = admin.post("/api/admin/spaces/cs/intents", json={"code": "B.STOCK.PREORDER", "name": "预售/订货",
                                                         "category_path": ["STOCK", "现货"]})
    assert r.status_code == 201 and r.json()["status"] == "草稿"
    assert r.json()["category_path"] == [{"code": "STOCK", "name": "库存与货期"}, {"code": None, "name": "现货"}]
    r = admin.post("/api/admin/spaces/cs/intents", json={"code": "M.STOCK.X", "name": "x", "l1": "STOCK", "l2": "现货"})
    assert r.status_code == 422
    r = admin.post("/api/admin/spaces/cs/intents", json={"code": "B.STOCK.PREORDER", "name": "y", "l1": "STOCK", "l2": "现货"})
    assert r.status_code == 409
    v0 = admin.get("/api/admin/spaces/cs/recognition").json()["version"]
    r = admin.put("/api/admin/intents/B.STOCK.PREORDER", json={"status": "启用"})
    assert r.status_code == 422 and r.json()["code"] == "ENABLE_CHECK_FAILED" and r.json()["details"]["problems"]
    body = {"description": "用户想在缺货时先下单预订；问能否预定算；问何时补货不算",
            "examples": ["没货能先拍吗", "可以预定吗", "先下单等补货可以吗"],
            "slots": [{"name": "sku", "required": True, "ask": ""}],
            "kb_refs": [{"kb_id": "kb_aq", "doc_id": "A1", "chunk_id": "Q4", "usage": "reference"}],
            "tools": [{"tool": "query_stock", "param_map": {"sku": {"source": "槽位", "value": "sku"}}}],
            "status": "启用"}
    r = admin.put("/api/admin/intents/B.STOCK.PREORDER", json=body)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["status"] == "启用" and d["kb_refs"][0]["valid"] and d["logs"]
    assert admin.get("/api/admin/spaces/cs/recognition").json()["version"] == v0 + 1
    # 维护类工具不可被意图选用
    r = admin.put("/api/admin/intents/B.STOCK.PREORDER", json={"tools": [{"tool": "pf_write_intents", "param_map": {}}]})
    assert r.status_code == 422
    # 未知槽位
    r = admin.put("/api/admin/intents/B.STOCK.PREORDER", json={"slots": [{"name": "nope", "required": False}]})
    assert r.status_code == 422
    rc = admin.get("/api/admin/spaces/cs/runtime-config").json()
    assert any(i["code"] == "B.STOCK.PREORDER" for i in rc["intents"])
    r = admin.post("/api/admin/intents/B.STOCK.PREORDER/status", json={"status": "停用"})
    assert r.json()["status"] == "停用"


def test_single_sentence(admin):
    r = admin.post("/api/admin/spaces/cs/test", json={"text": "有现货吗"}).json()
    assert r["verdict"] == "hit" and r["hit"]["code"] == "B.STOCK.AVAIL" and r["simulated"]
    assert r["threshold"] == 0.6
    r = admin.post("/api/admin/spaces/cs/test", json={"text": "火星天气怎么样"}).json()
    assert r["verdict"] == "final" and r["final_action"] == "handoff_human"
    it = admin.get("/api/admin/intents/B.REL.GREETING").json()
    draft = {**it, "examples": ["老板早上好呀", "早上好"]}
    r = admin.post("/api/admin/spaces/cs/test", json={"text": "老板早上好呀", "draft": draft}).json()
    assert r["verdict"] == "hit" and r["hit"]["is_current"]


def test_import_preview_apply(admin):
    data = {"source": "运营 AI 助手", "intents": [
        {"code": "B.STOCK.PREORDER", "name": "预售/订货", "category_path": ["库存与货期", "不存在的桶"],
         "description": "商品缺货时买家询问能否先下单预订", "examples": ["没货能先拍吗", "可以预定吗"],
         "slots": [{"name": "sku", "required": True, "ask": "请问是哪一款？"}, {"name": "color", "required": False}],
         "fallback": "转人工"},
        {"code": "B.STOCK.AVAIL", "name": "问现货", "l1": "STOCK", "l2": "现货", "description": "",
         "examples": ["有现货吗", "现在有货吗"]},
        {"code": "B.NOPE.X", "name": "错", "l1": "NOPE", "l2": "x", "examples": []}]}
    files = {"files": ("ai.json", json.dumps(data, ensure_ascii=False).encode(), "application/json")}
    r = admin.post("/api/admin/spaces/cs/imports/preview", files=files, data={"source": "AI 工具产出"})
    assert r.status_code == 200, r.text
    p = r.json()
    types = [x["type"] for x in p["rows"]]
    assert types[0] == "新增" and types[2] == "冲突"
    assert p["rows"][0]["path_invalid"] and "color" in p["rows"][0]["new_slots"]
    opt = p["rows"][0]["path_options"][1]
    assert opt["label"].startswith("库存与货期 / ")
    choices = {"0": {"action": "导入", "category_id": opt["id"]}, "1": {"action": "合并"}}
    r = admin.post("/api/admin/spaces/cs/imports/apply", json={"file": p["file"], "source": p["source"],
                                                               "items": p["items"], "choices": choices})
    res = r.json()
    assert res["counts"]["新增"] == 1 and res["new_slots"] == ["color"]
    assert admin.get("/api/admin/intents/B.STOCK.PREORDER").json()["category_id"] == opt["id"]
    recs = admin.get("/api/admin/spaces/cs/imports").json()
    assert recs[0]["id"] == res["import_id"] and recs[0]["source"] == "AI 工具产出"
    assert int(res["import_id"].split("-")[1]) > 1002
    # CSV
    csv = "编码,名称,分类路径,意图描述,例句,槽位\nB.STOCK.CSV1,CSV 导入,STOCK/现货,描述,一｜二｜三,sku(必填:哪款？)\n"
    r = admin.post("/api/admin/spaces/cs/imports/preview", files={"files": ("t.csv", csv.encode(), "text/csv")})
    row = r.json()["rows"][0]
    assert row["data"]["examples"] == ["一", "二", "三"] and row["type"] == "新增" and row["data"]["category_id"]
    # 0.5 版 L1/L2 两列模板继续兼容
    csv = "编码,名称,L1,L2,意图描述,例句,槽位\nB.STOCK.CSV1,CSV 导入,STOCK,现货,描述,一｜二｜三,\n"
    r = admin.post("/api/admin/spaces/cs/imports/preview", files={"files": ("t.csv", csv.encode(), "text/csv")})
    assert r.json()["rows"][0]["type"] == "新增"
    md = "## B.STOCK.MD1 MD 导入\n- 归属：库存与货期 / 现货\n- 意图描述：从 Markdown 导入的测试意图描述文字\n- 例句：一｜二｜三\n"
    r = admin.post("/api/admin/spaces/cs/imports/preview", files={"files": ("t.md", md.encode(), "text/markdown")})
    assert r.json()["rows"][0]["type"] == "新增" and r.json()["rows"][0]["data"]["category_path"] == ["库存与货期", "现货"]
    r = admin.post("/api/admin/spaces/cs/imports/preview", files={"files": ("t.pdf", b"x", "application/pdf")})
    assert r.status_code == 400


def test_slots_admin(admin):
    s = admin.get("/api/admin/spaces/cs/slots").json()
    assert len(s) == 9 and any(x["used_by_detail"] for x in s)
    r = admin.delete("/api/admin/spaces/cs/slots/sku")
    assert r.status_code == 409 and r.json()["code"] == "SLOT_IN_USE"
    r = admin.post("/api/admin/spaces/cs/slots", json={"name": "color", "display_name": "颜色", "type": "文本",
                                                       "default_ask": "要什么颜色？"})
    assert r.status_code == 201
    assert admin.delete("/api/admin/spaces/cs/slots/color").status_code == 200


def test_taxonomy_admin(admin):
    v0 = admin.get("/api/admin/spaces/cs/recognition").json()["version"]
    r = admin.post("/api/admin/spaces/cs/categories", json={"code": "TEST", "name": "测试大类"})
    assert r.status_code == 201
    tid = r.json()["id"]
    r = admin.post("/api/admin/spaces/cs/categories", json={"parent_id": tid, "name": "桶一"})
    bid = r.json()["id"]
    assert any(a["code"] == "TEST" and a["children"][0]["name"] == "桶一" for a in r.json()["tree"])
    r = admin.post("/api/admin/spaces/cs/categories", json={"parent_id": bid, "name": "太深"})
    assert r.status_code == 422 and r.json()["code"] == "CATEGORY_TOO_DEEP"
    assert admin.post("/api/admin/spaces/cs/categories", json={"parent_id": tid, "name": "桶一"}).status_code == 409
    assert admin.post("/api/admin/spaces/cs/categories", json={"code": "STOCK", "name": "重复编码"}).status_code == 409
    r = admin.patch(f"/api/admin/spaces/cs/categories/{bid}", json={"name": "桶二"})
    assert r.status_code == 200
    stock = next(a for a in r.json()["tree"] if a["code"] == "STOCK")
    r = admin.delete(f"/api/admin/spaces/cs/categories/{stock['id']}")
    assert r.status_code == 409 and r.json()["code"] == "CATEGORY_NOT_EMPTY"
    r = admin.delete(f"/api/admin/spaces/cs/categories/{stock['children'][0]['id']}")
    assert r.status_code == 409 and r.json()["code"] == "CATEGORY_IN_USE"
    assert admin.patch(f"/api/admin/spaces/cs/categories/{stock['id']}", json={"code": "STK"}).status_code == 409
    # 意图只能挂在叶子上
    r = admin.post("/api/admin/spaces/cs/intents", json={"code": "B.TEST.X", "name": "挂非叶子", "category_id": tid})
    assert r.status_code == 422
    assert admin.delete(f"/api/admin/spaces/cs/categories/{bid}").status_code == 200
    assert admin.delete(f"/api/admin/spaces/cs/categories/{tid}").status_code == 200
    assert admin.get("/api/admin/spaces/cs/recognition").json()["version"] == v0 + 5


def test_new_space(admin):
    r = admin.post("/api/admin/spaces", json={"id": "ops", "name": "运营助手", "bot": "运营 Bot", "code_prefix": "O"})
    assert r.status_code == 201 and r.json()["version"] == 1
    t = admin.get("/api/admin/spaces/ops/taxonomy").json()
    assert t["levels"] == ["大类", "问题桶", "意图"] and t["tree"][0]["code"] == "GEN"
    assert t["tree"][0]["children"][0]["name"] == "默认"
    assert admin.post("/api/admin/spaces", json={"id": "ops2", "name": "x", "bot": "b", "code_prefix": "B"}).status_code == 409


def test_recognition_admin(admin):
    r = admin.put("/api/admin/spaces/mc/recognition", json={"threshold": 0.7, "max_clarify": 3})
    assert r.status_code == 200 and r.json()["version"] == 8 and r.json()["changed"]
    r = admin.put("/api/admin/spaces/mc/recognition", json={"threshold": 0.72})
    assert r.status_code == 422


def test_kb_admin(admin):
    kbs = admin.get("/api/admin/kbs").json()
    assert len(kbs) == 11 and sum(k["chunk_count"] for k in kbs) > 0
    r = admin.post("/api/admin/kbs", json={"name": "新表格库", "type": "表格知识库",
                                           "description": "用于验收的表格库",
                                           "tags": {"platform": ["TikTok"]}})  # tags 应被忽略
    assert r.status_code == 201
    assert r.json()["description"] == "用于验收的表格库" and "tags" not in r.json()
    kid = r.json()["kb_id"]
    aq = next(k for k in kbs if k["kb_id"] == "kb_aq")
    assert "tags" not in aq
    assert "平台：1688" in aq["description"] and "语言：zh" in aq["description"]
    r2 = admin.patch(f"/api/admin/kbs/{kid}", json={"description": "已改描述", "tags": {"platform": ["Shopee"]}})
    assert r2.status_code == 200 and r2.json()["description"] == "已改描述" and "tags" not in r2.json()
    r3 = admin.post("/api/admin/kbs", json={"name": "超长描述库", "type": "表格知识库", "description": "字" * 2001})
    assert r3.status_code == 422
    csv = "问题,答案\nA,B\n".encode()
    r = admin.post(f"/api/admin/kbs/{kid}/documents:import", files={"files": ("a.csv", csv, "text/csv")})
    assert r.status_code == 202
    d = admin.get(f"/api/admin/kbs/{kid}").json()
    assert d["headers"] == ["问题", "答案"] and d["documents"][0]["status"] == "构建成功"
    assert admin.post("/api/admin/kbs", json={"name": "新表格库", "type": "表格知识库"}).status_code == 409
    imp = admin.get("/api/admin/kbs/kb_aq/impact").json()["affected"]
    assert len(imp) >= 10
    r = admin.delete("/api/admin/kbs/kb_aq").json()
    assert "B.STOCK.AVAIL" in r["affected_intents"]
    it = admin.get("/api/admin/intents/B.STOCK.AVAIL").json()
    assert not any(x["kb_id"] == "kb_aq" for x in it["kb_refs"])
    s = admin.put("/api/admin/kb-settings", json={"top_k": 8, "threshold": 0.3}).json()
    assert s == {"top_k": 8, "threshold": 0.3}
    hits = admin.post("/api/admin/kb-search", json={"query": "违禁词"}).json()
    assert len(hits) <= 8


def test_tools_admin(admin):
    ts = admin.get("/api/admin/tools").json()
    assert len(ts) == 33  # 含卖家精灵 4 个示例子工具
    qs = next(t for t in ts if t["name"] == "query_stock")
    assert qs["used_by"] and qs["type_label"] == "API 接口"
    r = admin.put("/api/admin/tools/pf_read_intents", json={"display_name": "x"})
    assert r.status_code == 403 and r.json()["code"] == "BUILTIN_READONLY"
    base = {"name": "my_cli", "type": "cli", "display_name": "我的 CLI", "description": "测试", "owner": "我",
            "params": [{"name": "sku", "type": "string", "required": True}],
            "config": {"bin": "xclaw-cli", "command_template": "xclaw-cli mall mall-product detail get --sku {sku} --lang {lang} --json"}}
    r = admin.post("/api/admin/tools", json=base)
    assert r.status_code == 422 and "lang" in json.dumps(r.json(), ensure_ascii=False)
    base["config"]["command_template"] = "xclaw-cli mall mall-product detail get --sku {sku} --json"
    r = admin.post("/api/admin/tools", json=base)
    assert r.status_code == 201, r.text
    p = admin.post("/api/admin/tools/my_cli/test", json={"values": {"sku": "SKU-1"}, "dry_run": True}).json()
    assert p["executed"] is False and p["request"]["argv"][:2] == ["xclaw-cli", "mall"]
    assert admin.post("/api/admin/tools/my_cli/test", json={"values": {}, "dry_run": True}).status_code == 422
    r = admin.delete("/api/admin/tools/query_stock")
    assert r.status_code == 409 and r.json()["code"] == "TOOL_IN_USE"
    assert admin.delete("/api/admin/tools/my_cli").status_code == 200
    r = admin.post("/api/admin/tools", json={"name": "bad_http", "type": "http", "display_name": "x", "description": "d",
                                            "owner": "o", "config": {"url": "ftp://x"}})
    assert r.status_code == 422


def test_cli_render_optional_pair():
    from app.services.tools import render_cli
    tpl = "xclaw-cli mall mall-product detail get --sku {sku} --locale {locale} --json"
    assert render_cli(tpl, {"sku": "A 1"}) == ["xclaw-cli", "mall", "mall-product", "detail", "get", "--sku", "A 1", "--json"]
    assert "--locale" in render_cli(tpl, {"sku": "A", "locale": "en"})


def test_keys_admin(admin, client):
    r = admin.post("/api/admin/api-keys", json={"name": "两空间 Key", "spaces": ["cs", "mc"], "scopes": ["意图:读"]})
    k = r.json()
    assert r.status_code == 201 and k["token"].startswith("ipk_")
    lst = admin.get("/api/admin/api-keys").json()
    assert all("token" not in x for x in lst) and len(lst) == 4
    r = client.get("/api/v1/spaces", headers={"Authorization": f"Bearer {k['token']}"})
    assert len(r.json()) == 2
    assert admin.post("/api/admin/api-keys", json={"name": "x", "spaces": [], "scopes": ["意图:读"]}).status_code == 422
    from app.db import SessionLocal
    from app.models import ApiKey
    with SessionLocal() as db:
        row = db.get(ApiKey, k["id"])
        assert row.token_hash != k["token"] and len(row.token_hash) == 64


def test_revoked_key_can_be_hard_deleted(admin, client):
    r = admin.post("/api/admin/api-keys", json={"name": "待删除探针", "spaces": ["cs"], "scopes": ["意图:读"]})
    assert r.status_code == 201
    kid, tok = r.json()["id"], r.json()["token"]
    # 有效 Key 不能直接删除
    r = admin.delete(f"/api/admin/api-keys/{kid}")
    assert r.status_code == 409 and r.json()["code"] == "KEY_NOT_REVOKED"
    assert any(x["id"] == kid for x in admin.get("/api/admin/api-keys").json())

    r = admin.post(f"/api/admin/api-keys/{kid}/revoke")
    assert r.status_code == 200 and r.json()["status"] == "已吊销"
    # 吊销后调用 401
    assert client.get("/api/v1/spaces", headers={"Authorization": f"Bearer {tok}"}).status_code == 401

    r = admin.delete(f"/api/admin/api-keys/{kid}")
    assert r.status_code == 200 and r.json()["id"] == kid and r.json()["name"] == "待删除探针"
    lst = admin.get("/api/admin/api-keys").json()
    assert all(x["id"] != kid for x in lst)
    assert all("token" not in x and "token_hash" not in x for x in lst)

    # 行已不在表里；审计保留（创建、吊销、删除）
    from app.db import SessionLocal
    from app.models import ApiKey
    with SessionLocal() as db:
        assert db.get(ApiKey, kid) is None
    logs = admin.get(f"/api/admin/audit?entity_type=api_key&q={kid}").json()["items"]
    acts = [x["action"] for x in logs]
    assert "api_key.create" in acts and "api_key.revoke" in acts and "api_key.delete" in acts
    assert any(x["action"] == "api_key.delete" and x["actor_type"] == "user" and x["actor"] == "管理员" for x in logs)
    # 删除不存在
    r = admin.delete(f"/api/admin/api-keys/{kid}")
    assert r.status_code == 404 and r.json()["code"] == "KEY_NOT_FOUND"


def test_audit(admin):
    admin.put("/api/admin/spaces/cs/recognition", json={"max_clarify": 3})
    a = admin.get("/api/admin/audit?entity_type=space").json()
    assert a["items"][0]["action"] == "recognition.update" and a["items"][0]["actor"] == "管理员"


def test_docs_and_meta(admin):
    assert "openapi: 3.0.3" in admin.get("/api/admin/docs/openapi").text
    assert admin.get("/api/admin/meta").json()["scopes"][0] == "意图:读"


def test_admin_intent_detail_shape_both_spaces(admin):
    """抽屉打开时会拉详情及槽位、分类、已绑工具；各字段须是前端可直接使用的列表。"""
    for sid in ("cs", "mc"):
        assert admin.get(f"/api/admin/spaces/{sid}/slots").status_code == 200
        tax = admin.get(f"/api/admin/spaces/{sid}/taxonomy")
        assert tax.status_code == 200 and isinstance(tax.json().get("tree"), list)
        listed = admin.get(f"/api/admin/spaces/{sid}/intents?page_size=500").json()
        assert listed["items"], sid
        for row in listed["items"]:
            r = admin.get(f"/api/admin/intents/{row['code']}")
            assert r.status_code == 200, f"{row['code']} {r.text}"
            d = r.json()
            for key in ("examples", "slots", "tools", "kb_refs", "logs"):
                assert isinstance(d.get(key), list), f"{row['code']} {key}={type(d.get(key))}"
            h = d.get("handling") or {}
            assert h.get("mode")
            if h.get("mode") == "fixed_reply":
                assert h.get("replies") and h.get("instruction") and h.get("reply_strictness")
            # 与前端 IntentEditor 组装表单相同的访问路径，不能抛错
            "\n".join(d["examples"])
            [{"name": s["name"], "required": s["required"], "ask": s["ask"]} for s in d["slots"]]
            names = [b["tool"] for b in d["tools"]]
            if names:
                tr = admin.get("/api/admin/tools", params={"names": ",".join(names)})
                assert tr.status_code == 200, f"{row['code']} tools {tr.text}"
                items = {t["name"]: t for t in (tr.json().get("items") or [])}
                for name in names:
                    tl = items.get(name)
                    if not tl:
                        continue
                    assert isinstance(tl.get("params") or [], list)
