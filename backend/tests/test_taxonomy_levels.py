"""动态分类层级：层级插入 / 删除 / 改名、预览不落库、迁移规则、对外输出。"""
from contract import check


def _tax(admin, sid="cs"):
    return admin.get(f"/api/admin/spaces/{sid}/taxonomy").json()


def _ver(admin, sid="cs"):
    return admin.get(f"/api/admin/spaces/{sid}/recognition").json()["version"]


def _paths(admin, sid="cs"):
    items = admin.get(f"/api/admin/spaces/{sid}/intents?page_size=500").json()["items"]
    return {i["code"]: [p["name"] for p in i["category_path"]] for i in items}


def test_insert_level_preview_then_apply(admin, full_key):
    v0, before = _ver(admin), _paths(admin)
    body = {"levels": ["大类", "子类", "问题桶", "意图"], "change": {"op": "insert", "index": 1}}
    p = admin.post("/api/admin/spaces/cs/levels/preview", json=body).json()
    assert p["needs_confirm"] and p["created_uncategorized"] == 12 and p["intents_moved"] == 123
    assert p["enabled_intents_moved"] == 15 and p["categories_after"] == p["categories_before"] + 12
    assert p["samples"][0]["after"].count(" / ") == 2
    # 预览不落库
    assert _tax(admin)["levels"] == ["大类", "问题桶", "意图"] and _ver(admin) == v0 and _paths(admin) == before
    r = admin.put("/api/admin/spaces/cs/levels", json=body)
    assert r.status_code == 422 and r.json()["code"] == "CONFIRM_REQUIRED"
    r = admin.put("/api/admin/spaces/cs/levels", json={**body, "confirm": True})
    assert r.status_code == 200 and r.json()["version"] == v0 + 1
    after = _paths(admin)
    assert all(after[c] == [b[0], "未分类", b[1]] for c, b in before.items())
    t = r.json()["taxonomy"]
    assert t["levels"] == ["大类", "子类", "问题桶", "意图"] and all(a["children"][0]["name"] == "未分类" for a in t["tree"])
    # 对外：taxonomy / intents / runtime-config 都是 3 层分类
    tx = check(full_key.get("/api/v1/taxonomy", space="cs"))
    assert [x["name"] for x in tx["levels"]] == ["大类", "子类", "问题桶"]
    rc = check(full_key.get("/api/v1/runtime-config", space="cs"))
    assert rc["taxonomy"]["levels"][1]["name"] == "子类" and all(len(i["category_path"]) == 3 for i in rc["intents"])
    # 审计 + 意图操作记录
    items = admin.get("/api/admin/audit?q=taxonomy.levels").json()["items"]
    assert items and "插入层级" in items[0]["summary"] and items[0]["details"]["intents_moved"] == 123
    d = admin.get("/api/admin/intents/B.STOCK.AVAIL").json()
    assert d["logs"][0]["message"].startswith("分类层级调整：")
    # 写入时必须给满 3 层
    new = {"code": "B.STOCK.NEW3", "name": "三层写入", "description": "用于验证三层分类下的写入路径校验",
           "examples": ["一", "二", "三"]}
    b = check(full_key.post("/api/v1/intents:batchUpsert", space="cs", json={"mode": "merge", "intents": [
        {**new, "category_path": ["STOCK", "现货"]}]}))
    assert b["conflicts"][0]["reason"] == "UNKNOWN_CATEGORY"
    b = check(full_key.post("/api/v1/intents:batchUpsert", space="cs", json={"mode": "merge", "intents": [
        {**new, "category_path": ["STOCK", "未分类", "现货"]}]}))
    assert b["created"] == 1

    # 再删掉这一层 → 回到原结构
    body = {"change": {"op": "remove", "index": 1}, "confirm": True}
    p = admin.post("/api/admin/spaces/cs/levels/preview", json=body).json()
    assert p["intents_moved"] == 124 and p["categories_after"] == p["categories_before"] - 12 and not p["merged"]
    r = admin.put("/api/admin/spaces/cs/levels", json=body)
    assert r.status_code == 200 and r.json()["levels"] == ["大类", "问题桶", "意图"]
    after = _paths(admin)
    assert all(after[c] == before[c] for c in before) and after["B.STOCK.NEW3"] == ["库存与货期", "现货"]
    t = _tax(admin)
    assert len(t["tree"]) == 12 and sum(len(a["children"]) for a in t["tree"]) == 46
    assert _ver(admin) == v0 + 2  # 插入 + 删除；新建草稿不加版本


def test_remove_top_level_merges_same_name(admin):
    sid = "mc"
    a = admin.post(f"/api/admin/spaces/{sid}/categories", json={"code": "AA", "name": "甲"}).json()["id"]
    b = admin.post(f"/api/admin/spaces/{sid}/categories", json={"code": "BB", "name": "乙"}).json()["id"]
    a1 = admin.post(f"/api/admin/spaces/{sid}/categories", json={"parent_id": a, "name": "同名桶"}).json()["id"]
    b1 = admin.post(f"/api/admin/spaces/{sid}/categories", json={"parent_id": b, "name": "同名桶"}).json()["id"]
    assert admin.post(f"/api/admin/spaces/{sid}/intents", json={"code": "M.AA.ONE", "name": "甲一", "category_id": a1}).status_code == 201
    assert admin.post(f"/api/admin/spaces/{sid}/intents", json={"code": "M.BB.TWO", "name": "乙二", "category_id": b1}).status_code == 201
    total = len(_paths(admin, sid))
    body = {"change": {"op": "remove", "index": 0}}
    p = admin.post(f"/api/admin/spaces/{sid}/levels/preview", json=body).json()
    assert {"name": "同名桶", "into": "同名桶"} in p["merged"]
    assert {d["code"] for d in p["dropped_codes"]} >= {"AA", "BB", "DESIGN"}
    r = admin.put(f"/api/admin/spaces/{sid}/levels", json={**body, "confirm": True})
    assert r.status_code == 200 and r.json()["levels"] == ["问题桶", "意图"]
    paths = _paths(admin, sid)
    assert len(paths) == total and paths["M.AA.ONE"] == paths["M.BB.TWO"] == ["同名桶"]
    t = _tax(admin, sid)
    assert all(not n["children"] for n in t["tree"]) and sum(n["count"] for n in t["tree"]) == total
    assert len([n for n in t["tree"] if n["name"] == "同名桶"]) == 1
    # 第一层没有编码后，新意图编码只要求空间前缀
    leaf = t["tree"][0]["id"]
    assert admin.post(f"/api/admin/spaces/{sid}/intents", json={"code": "M.ANY.NEW", "name": "新", "category_id": leaf}).status_code == 201


def test_zero_category_levels_and_limits(admin):
    admin.post("/api/admin/spaces", json={"id": "ops", "name": "运营助手", "bot": "运营 Bot", "code_prefix": "O"})
    r = admin.post("/api/admin/spaces/ops/intents", json={"code": "O.GEN.HELLO", "name": "问好", "category_path": ["GEN", "默认"]})
    assert r.status_code == 201
    for _ in range(2):
        r = admin.put("/api/admin/spaces/ops/levels", json={"change": {"op": "remove", "index": 0}, "confirm": True})
        assert r.status_code == 200
    t = _tax(admin, "ops")
    assert t["levels"] == ["意图"] and t["tree"] == [] and t["unassigned"] == 1
    assert admin.get("/api/admin/intents/O.GEN.HELLO").json()["category_path"] == []
    r = admin.post("/api/admin/spaces/ops/intents", json={"code": "O.BYE", "name": "再见"})
    assert r.status_code == 201
    r = admin.put("/api/admin/spaces/ops/levels", json={"change": {"op": "remove", "index": 0}, "confirm": True})
    assert r.status_code == 422  # 意图层不能删
    # 从 0 层加回 1 层：全部意图落到「未分类」
    r = admin.put("/api/admin/spaces/ops/levels", json={"levels": ["场景", "意图"], "change": {"op": "insert", "index": 0},
                                                         "confirm": True})
    assert r.status_code == 200
    t = _tax(admin, "ops")
    assert t["tree"][0]["name"] == "未分类" and t["tree"][0]["count"] == 2 and t["unassigned"] == 0
    # 改名不需要确认，但同样加版本
    v = _ver(admin, "ops")
    r = admin.put("/api/admin/spaces/ops/levels", json={"levels": ["业务场景", "技能"]})
    assert r.status_code == 200 and r.json()["changed"] and _ver(admin, "ops") == v + 1
    # 最多 5 层分类
    for i in range(4):
        assert admin.put("/api/admin/spaces/ops/levels", json={"change": {"op": "insert", "index": 1, "name": f"L{i}"},
                                                               "confirm": True}).status_code == 200
    r = admin.put("/api/admin/spaces/ops/levels", json={"change": {"op": "insert", "index": 1}, "confirm": True})
    assert r.status_code == 422 and r.json()["code"] == "TOO_MANY_LEVELS"
    r = admin.put("/api/admin/spaces/ops/levels", json={"levels": ["a", "a", "b", "c", "d", "e"]})
    assert r.status_code == 422
