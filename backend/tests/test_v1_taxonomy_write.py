"""开放接口：分类节点写入、auto_create_parents、启用意图、知识库创建与关联。"""
from contract import check


NODE = {
    "code": "PROBE",
    "name": "写入探针分类",
    "description": "仅用于开放接口测试，验收后停用。",
    "depth": 1,
}
LEAF = {
    "code": "PROBELEAF",
    "name": "探针叶子",
    "parent_code": "PROBE",
    "depth": 2,
    "description": "探针最末层分类。",
}
INTENT = {
    "code": "B.PROBE.TAXTEST",
    "name": "分类写入探针意图",
    "category_path": ["PROBE", "PROBELEAF"],
    "description": "用于验证开放接口创建分类后写入草稿意图，不会用于真实业务。",
    "examples": ["探针问一句甲", "探针问一句乙", "探针问一句丙"],
    "slots": [{"name": "sku", "required": True, "ask": "请问是哪一款？"}],
}


def test_taxonomy_write_requires_scope(bot):
    r = bot.post("/api/v1/taxonomy/nodes", json=NODE, space="cs")
    assert r.status_code == 403 and r.json()["code"] == "SCOPE_FORBIDDEN"


def test_taxonomy_upsert_dry_run_update_disable(full_key, admin):
    r = full_key.post("/api/v1/taxonomy/nodes", json={**NODE, "code": "BAD CODE"}, space="cs", idem=False)
    # invalid code without dry_run still 422; with dry_run too
    r = full_key.post("/api/v1/taxonomy/nodes", json={**NODE, "code": "BAD CODE", "dry_run": True}, space="cs", idem=False)
    assert r.status_code == 422

    r = full_key.post("/api/v1/taxonomy/nodes", json={**NODE, "dry_run": True}, space="cs", idem=False)
    b = check(r)
    assert b["ok"] is True and b["action"] == "created" and b["dry_run"] is True
    # 未写入
    tax = full_key.get("/api/v1/taxonomy", space="cs").json()
    codes = _codes(tax["categories"])
    assert "PROBE" not in codes

    r = full_key.post("/api/v1/taxonomy/nodes", json=NODE, space="cs")
    assert r.status_code == 201
    b = check(r)
    assert b["action"] == "created" and b["code"] == "PROBE" and b["enabled"] is True and b["depth"] == 1

    r = full_key.post("/api/v1/taxonomy/nodes", json=LEAF, space="cs")
    assert r.status_code == 201 and check(r)["parent_code"] == "PROBE"

    r = full_key.put("/api/v1/taxonomy/nodes/PROBE", json={"name": "写入探针分类已改", "description": "已改"}, space="cs")
    assert check(r)["name"] == "写入探针分类已改" and r.json()["action"] == "updated"

    r = full_key.post("/api/v1/taxonomy/nodes/PROBE:disable", json={}, space="cs")
    b = check(r)
    assert b["action"] == "disabled" and b["enabled"] is False

    logs = admin.get("/api/admin/audit?entity_type=category&q=PROBE").json()["items"]
    assert any(x["action"] == "taxonomy.add" and x["actor_type"] == "api_key" for x in logs)
    assert any(x["action"] == "taxonomy.disable" for x in logs)


def test_unknown_category_message_and_auto_create(full_key, linxia):
    r = linxia.post("/api/v1/intents:batchUpsert", json={"mode": "merge", "intents": [{
        "code": "B.NOPE.HINT", "name": "提示测试", "category_path": ["NOPE", "现货"],
        "description": "用于检查分类不存在时的错误提示是否给出修复方法。",
        "examples": ["一", "二", "三"]}]})
    b = check(r)
    c = next(x for x in b["conflicts"] if x["code"] == "B.NOPE.HINT")
    assert c["reason"] == "UNKNOWN_CATEGORY"
    assert "taxonomy/nodes" in c["message"] and "auto_create_parents" in c["message"]

    it = {
        "code": "B.AUTOCR.PARENTS", "name": "自动建分类意图",
        "category_path": [{"code": "AUTOCR", "name": "自动大类"}, {"code": "AUTOBKT", "name": "自动桶"}],
        "description": "验证 auto_create_parents 会先建好缺失分类再写入草稿意图。",
        "examples": ["自动甲", "自动乙", "自动丙"],
    }
    b = check(full_key.post("/api/v1/intents:batchUpsert", json={
        "mode": "merge", "auto_create_parents": True, "intents": [it]}, space="cs"))
    assert b["created"] == 1 and "AUTOCR" in (b.get("created_nodes") or [])
    got = check(full_key.get("/api/v1/intents/B.AUTOCR.PARENTS", space="cs"))
    assert got["status"] == "草稿" and got["category_path"][0]["code"] == "AUTOCR"


def test_batch_apply_all_or_nothing(full_key):
    good_nodes = [
        {"code": "ROLLA", "name": "回滚大类", "depth": 1},
        {"code": "ROLLB", "name": "回滚桶", "parent_code": "ROLLA", "depth": 2},
    ]
    bad_intent = {"code": "B.ROLLA.BAD", "name": "会失败", "category_path": ["ROLLA", "ROLLB"],
                  "description": "短", "examples": ["一", "二", "三"]}
    r = full_key.post("/api/v1/batchUpsert", json={"nodes": good_nodes, "intents": [bad_intent]}, space="cs")
    assert r.status_code == 422 and r.json()["code"] == "BATCH_FAILED"
    tax = full_key.get("/api/v1/taxonomy", space="cs").json()
    assert "ROLLA" not in _codes(tax["categories"])

    r = full_key.post("/api/v1/batchUpsert", json={
        "dry_run": True,
        "nodes": good_nodes,
        "intents": [{**INTENT, "code": "B.ROLLA.OK", "category_path": ["ROLLA", "ROLLB"],
                     "description": "用于事务批量写入的校验预览，描述足够长。",
                     "examples": ["一", "二", "三"]}],
    }, space="cs", idem=False)
    b = check(r)
    assert b["ok"] is True and b["dry_run"] is True
    tax = full_key.get("/api/v1/taxonomy", space="cs").json()
    assert "ROLLA" not in _codes(tax["categories"])

    r = full_key.post("/api/v1/batchUpsert", json={
        "nodes": good_nodes,
        "intents": [{**INTENT, "code": "B.ROLLA.OK", "category_path": ["ROLLA", "ROLLB"],
                     "description": "用于事务批量写入的正式提交，描述足够长。",
                     "examples": ["一", "二", "三"]}],
    }, space="cs")
    b = check(r)
    assert b["ok"] is True and b["intents"]["created"] == 1
    assert "ROLLA" in _codes(full_key.get("/api/v1/taxonomy", space="cs").json()["categories"])


def test_disable_node_refuses_enabled_intents(full_key):
    full_key.post("/api/v1/taxonomy/nodes", json=NODE, space="cs")
    full_key.post("/api/v1/taxonomy/nodes", json=LEAF, space="cs")
    b = check(full_key.post("/api/v1/intents:batchUpsert", json={"mode": "merge", "intents": [INTENT]}, space="cs"))
    assert b["created"] >= 1
    # 还缺工具或知识，启用应 422
    r = full_key.post("/api/v1/intents/B.PROBE.TAXTEST:enable", json={}, space="cs")
    assert r.status_code == 422
    # 注册一个无必填入参的占位工具再绑定，避免误绑业务工具
    probe_tool = {
        "name": "ai_probe_tax_echo",
        "type": "http",
        "display_name": "分类探针 HTTP",
        "description": "仅用于验证启用意图与停用分类的占位工具，不连接真实业务。",
        "owner": "测试",
        "params": [],
        "config": {"url": "https://example.com/tax-probe", "method": "GET", "auth": "无"},
    }
    r = full_key.post("/api/v1/tools", json=probe_tool)
    assert r.status_code in (200, 201), r.text
    r = full_key.post("/api/v1/intents/B.PROBE.TAXTEST/tools",
                      json={"tool": "ai_probe_tax_echo", "param_map": {}}, space="cs")
    assert r.status_code == 200, r.text
    r = full_key.post("/api/v1/intents/B.PROBE.TAXTEST:enable", json={}, space="cs")
    assert r.status_code == 200 and check(r)["status"] == "启用", r.text
    r = full_key.post("/api/v1/taxonomy/nodes/PROBE:disable", json={}, space="cs")
    assert r.status_code == 409 and r.json()["code"] == "CATEGORY_IN_USE"
    r = full_key.post("/api/v1/intents/B.PROBE.TAXTEST:disable", json={}, space="cs")
    assert check(r)["status"] == "停用"
    r = full_key.post("/api/v1/taxonomy/nodes/PROBE:disable", json={}, space="cs")
    assert check(r)["enabled"] is False


def test_patch_still_forbids_enable_via_patch(linxia, full_key):
    # 保持 PATCH 行为
    r = linxia.patch("/api/v1/intents/B.REL.GREETING", json={"status": "启用"})
    assert r.status_code == 403 and r.json()["code"] == "ENABLE_FORBIDDEN"


def test_create_kb_and_bind_knowledge(full_key):
    r = full_key.post("/api/v1/kbs", json={"name": "探针知识库-请勿用于业务", "type": "表格知识库",
                                          "description": "开放接口探针库", "tags": {"platform": ["TikTok"]}})
    assert r.status_code == 201
    b = check(r)
    kid = b["kb_id"]
    assert b["action"] == "created" and b["description"] == "开放接口探针库" and "tags" not in b
    got = check(full_key.get(f"/api/v1/kbs/{kid}"))
    assert got["name"] == "探针知识库-请勿用于业务" and got["description"] == "开放接口探针库" and "tags" not in got
    r = full_key.patch(f"/api/v1/kbs/{kid}", json={"description": "已改为描述"})
    assert check(r)["description"] == "已改为描述"

    # 用已有库绑到草稿意图
    full_key.post("/api/v1/taxonomy/nodes", json=NODE, space="cs")
    full_key.post("/api/v1/taxonomy/nodes", json=LEAF, space="cs")
    full_key.post("/api/v1/intents:batchUpsert", json={"mode": "merge", "intents": [INTENT]}, space="cs")
    chunks = full_key.get("/api/v1/kbs/kb_aq/chunks").json()["items"]
    ch = chunks[0]
    ref = {"kb_id": "kb_aq", "doc_id": ch["doc_id"], "chunk_id": ch["chunk_id"], "usage": "reference", "dry_run": True}
    r = full_key.post("/api/v1/intents/B.PROBE.TAXTEST/knowledge", json=ref, space="cs")
    assert check(r)["ok"] is True
    ref.pop("dry_run")
    r = full_key.post("/api/v1/intents/B.PROBE.TAXTEST/knowledge", json=ref, space="cs")
    it = check(r)
    assert any(x["kb_id"] == "kb_aq" for x in it["kb_refs"])
    r = full_key.delete(
        f"/api/v1/intents/B.PROBE.TAXTEST/knowledge?kb_id=kb_aq&doc_id={ch['doc_id']}&chunk_id={ch['chunk_id']}",
        space="cs")
    it = check(r)
    assert not any(x["kb_id"] == "kb_aq" and x["chunk_id"] == ch["chunk_id"] for x in it["kb_refs"])


def test_mcp_taxonomy_and_enable_tools_listed(full_key):
    from test_mcp import call, rpc
    tools = rpc(full_key, "tools/list").json()["result"]["tools"]
    names = [t["name"] for t in tools]
    for n in ("taxonomy_upsert_node", "taxonomy_disable_node", "intent_enable",
              "intent_disable", "create_knowledge_base", "intent_bind_knowledge", "intent_unbind_knowledge"):
        assert n in names
    err, b = call(full_key, "taxonomy_upsert_node", {**NODE, "code": "MCPNODE", "dry_run": True, "space": "cs"})
    assert not err and b["ok"] is True


def _codes(tree):
    out = []
    def walk(nodes):
        for n in nodes or []:
            if n.get("code"):
                out.append(n["code"])
            walk(n.get("children"))
    walk(tree)
    return out
