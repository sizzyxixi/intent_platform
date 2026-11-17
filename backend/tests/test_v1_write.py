"""开放接口写操作：幂等、槽位、batchUpsert、PATCH、空间设置。"""
from contract import check

NEW = {"code": "B.STOCK.PREORDER", "name": "预售/订货", "category_path": ["STOCK", "现货"],
       "description": "用户想在缺货时先下单预订；问能否预定、先拍后发算；问何时补货不算",
       "examples": ["没货能先拍吗", "可以预定吗", "先下单等补货可以吗"],
       "slots": [{"name": "sku", "required": True, "ask": "请问是哪一款？"}]}


def test_idempotency_required_and_replay(linxia, client):
    r = linxia.post("/api/v1/slots", json={"name": "color", "display_name": "颜色", "type": "文本"}, idem=False)
    assert r.status_code == 400 and r.json()["code"] == "IDEMPOTENCY_KEY_REQUIRED"
    h = linxia.h()
    body = {"name": "color", "display_name": "颜色", "type": "文本", "default_ask": "要什么颜色？"}
    r1 = client.post("/api/v1/slots", json=body, headers=h)
    assert r1.status_code == 201
    check(r1)
    r2 = client.post("/api/v1/slots", json=body, headers=h)
    assert r2.status_code == 201 and r2.json() == r1.json() and r2.headers.get("Idempotent-Replayed") == "true"
    r3 = client.post("/api/v1/slots", json={**body, "display_name": "颜色2"}, headers=h)
    assert r3.status_code == 409 and r3.json()["code"] == "IDEMPOTENCY_CONFLICT"


def test_slot_crud_and_in_use(linxia):
    r = linxia.post("/api/v1/slots", json={"name": "sku", "display_name": "商品", "type": "文本", "default_ask": "哪款？"})
    assert r.status_code == 409 and check(r)["code"] == "SLOT_EXISTS"
    r = linxia.post("/api/v1/slots", json={"name": "size", "display_name": "尺码", "type": "枚举", "default_ask": "哪个尺码？"})
    assert r.status_code == 400
    check(r)
    r = linxia.post("/api/v1/slots", json={"name": "size", "display_name": "尺码", "type": "枚举",
                                          "options": ["S", "M", "L"], "default_ask": "要哪个尺码？"})
    assert check(r)["options"] == ["S", "M", "L"]
    r = linxia.put("/api/v1/slots/size", json={"display_name": "尺码/规格", "type": "枚举", "options": ["S", "M"]})
    assert check(r)["display_name"] == "尺码/规格"
    r = linxia.delete("/api/v1/slots/size")
    assert r.status_code == 204
    r = linxia.delete("/api/v1/slots/sku")
    b = check(r)
    assert r.status_code == 409 and b["code"] == "SLOT_IN_USE" and b["details"]["used_by"]
    assert linxia.delete("/api/v1/slots/nope").status_code == 404


def test_slots_isolated_between_spaces(full_key):
    r = full_key.post("/api/v1/slots", json={"name": "only_mc", "display_name": "仅 MC", "type": "文本", "default_ask": "请补充"}, space="mc")
    assert r.status_code == 201
    assert "only_mc" not in [s["name"] for s in full_key.get("/api/v1/slots", space="cs").json()]


def test_batch_upsert_create_merge_conflicts(linxia):
    bad = [
        {**NEW, "code": "B.STOCK.X1", "name": "短描述", "description": "太短"},
        {**NEW, "code": "M.STOCK.X2", "name": "错前缀"},
        {**NEW, "code": "B.NOPE.X3", "name": "未知大类", "category_path": ["NOPE", "现货"]},
        {**NEW, "code": "B.STOCK.X4", "name": "未知桶", "category_path": ["STOCK", "不存在"]},
        {**NEW, "code": "B.STOCK.X7", "name": "路径太短", "category_path": ["STOCK"]},
        {**NEW, "code": "B.PRICE.X8", "name": "编码与大类不符"},
        {**NEW, "code": "B.STOCK.X5", "name": "例句少", "examples": ["a"]},
        {**NEW, "code": "B.STOCK.X6", "name": "新槽位继承默认话术", "slots": [{"name": "new_slot_x", "required": True}]},
        {**NEW, "code": "B.STOCK.PREORDER"},
    ]
    r = linxia.post("/api/v1/intents:batchUpsert", json={"mode": "merge", "intents": [NEW] + bad})
    b = check(r)
    assert b["created"] == 3 and b["new_slots"] == ["new_slot_x"]
    reasons = {c["code"]: c["reason"] for c in b["conflicts"]}
    assert "B.STOCK.X1" not in reasons  # 短描述可以创建草稿
    assert reasons["M.STOCK.X2"] in ("INVALID_CODE", "CODE_IN_OTHER_SPACE")
    assert reasons["B.NOPE.X3"] == "UNKNOWN_CATEGORY"
    assert reasons["B.STOCK.X4"] == "UNKNOWN_CATEGORY"
    assert reasons["B.STOCK.X7"] == "UNKNOWN_CATEGORY"
    assert reasons["B.PRICE.X8"] == "INVALID_CODE"
    assert reasons["B.STOCK.X5"] == "TOO_FEW_EXAMPLES"
    assert "B.STOCK.X6" not in reasons  # 自动新建的槽位默认追问「请补充{name}」
    assert reasons["B.STOCK.PREORDER"] == "DUPLICATE_IN_BATCH"
    it = check(linxia.get("/api/v1/intents/B.STOCK.PREORDER"))
    assert it["status"] == "草稿"
    # merge：只补充新例句
    r = linxia.post("/api/v1/intents:batchUpsert", json={"mode": "merge", "intents": [
        {**NEW, "examples": NEW["examples"] + ["能预订吗"], "fallback": {"x": 1}}]})
    b = check(r)
    assert b["updated"] == 1 and b["items"][0]["result"] == "merged"
    assert "fallback" in str(b.get("ignored_fields"))
    assert "能预订吗" in linxia.get("/api/v1/intents/B.STOCK.PREORDER").json()["examples"]
    # 再提交一次完全相同 → 跳过
    b = check(linxia.post("/api/v1/intents:batchUpsert", json={"mode": "merge", "intents": [
        {**NEW, "examples": NEW["examples"] + ["能预订吗"]}]}))
    assert b["skipped"] == 1


def test_batch_upsert_new_slot_autocreated(linxia):
    it = {**NEW, "code": "B.STOCK.COLOR_ASK", "name": "问颜色",
          "slots": [{"name": "color", "required": True, "ask": "要什么颜色？"}]}
    b = check(linxia.post("/api/v1/intents:batchUpsert", json={"mode": "merge", "intents": [it]}))
    assert b["created"] == 1 and "color" in b["new_slots"]
    s = next(x for x in linxia.get("/api/v1/slots").json() if x["name"] == "color")
    assert s["type"] == "文本"


def test_batch_upsert_overwrite_enabled_protected(linxia):
    cur = linxia.get("/api/v1/intents/B.REL.ESCALATE").json()
    it = {"code": cur["code"], "name": cur["name"], "category_path": [p["name"] for p in cur["category_path"]],
          "description": cur["description"] + "（覆盖）", "examples": cur["examples"]}
    b = check(linxia.post("/api/v1/intents:batchUpsert", json={"mode": "overwrite", "intents": [it]}))
    assert b["conflicts"][0]["reason"] == "ENABLED_PROTECTED"


def test_batch_upsert_limits(linxia):
    r = linxia.post("/api/v1/intents:batchUpsert", json={"mode": "merge", "intents": [NEW] * 101})
    assert r.status_code == 413 and check(r)["code"] == "PAYLOAD_TOO_LARGE"
    r = linxia.post("/api/v1/intents:batchUpsert", json={"mode": "x", "intents": [NEW]})
    assert r.status_code == 400


def test_batch_upsert_bumps_version_on_enabled_merge(linxia):
    v0 = linxia.get("/api/v1/recognition").json()["version"]
    cur = linxia.get("/api/v1/intents/B.REL.ESCALATE").json()
    it = {"code": cur["code"], "name": cur["name"], "l1": cur["category_path"][0]["code"],  # 0.5 版字段兼容
          "l2": cur["category_path"][1]["name"], "description": cur["description"], "examples": cur["examples"] + ["我要举报你们"]}
    b = check(linxia.post("/api/v1/intents:batchUpsert", json={"mode": "merge", "intents": [it]}))
    assert b["updated"] == 1
    assert linxia.get("/api/v1/recognition").json()["version"] == v0 + 1
    assert linxia.get("/api/v1/intents/B.REL.ESCALATE").json()["status"] == "启用"


def test_patch_intent(linxia):
    code = "B.REL.GREETING"
    r = linxia.patch(f"/api/v1/intents/{code}", json={"examples": ["你好", "老板你好", "在吗老板", "哈喽"]})
    assert check(r)["examples"][-1] == "哈喽"
    r = linxia.patch(f"/api/v1/intents/{code}", json={"status": "启用"})
    assert r.status_code == 403 and check(r)["code"] == "ENABLE_FORBIDDEN"
    r = linxia.patch("/api/v1/intents/B.REL.ESCALATE", json={"examples": ["a", "b", "c"]})
    assert r.status_code == 403 and r.json()["code"] == "ENABLED_PROTECTED"
    r = linxia.patch(f"/api/v1/intents/{code}", json={"slots": [{"name": "nope_slot", "required": False}]})
    assert r.status_code == 422
    check(r)
    r = linxia.patch(f"/api/v1/intents/{code}", json={"l1": "X"})
    assert r.status_code == 400
    r = linxia.patch("/api/v1/intents/B.REL.NOPE", json={"name": "x"})
    assert r.status_code == 404


def test_recognition_put(full_key):
    v0 = full_key.get("/api/v1/recognition", space="mc").json()["version"]
    r = full_key.put("/api/v1/recognition", json={"threshold": 0.55, "final_action": "decline",
                                                  "final_message": "抱歉，暂不支持"}, space="mc")
    b = check(r)
    assert b["threshold"] == 0.6 and b["fallback"]["mode"] == "llm_free" and b["version"] == v0
    assert b.get("deprecated") is True
    b = check(full_key.put("/api/v1/recognition", json={"threshold": 1.5}, space="mc"))
    assert b["version"] == v0 and b["fallback"]["mode"] == "llm_free"
    assert full_key.get("/api/v1/recognition", space="cs").json()["threshold"] == 0.6


def test_write_scope_required(bot):
    r = bot.post("/api/v1/intents:batchUpsert", json={"mode": "merge", "intents": [NEW]})
    assert r.status_code == 403 and r.json()["code"] == "SCOPE_FORBIDDEN"


def test_revoked_key(client, linxia):
    from conftest import login
    login(client)
    r = client.post("/api/admin/api-keys/ak_7f3a/revoke")
    assert r.status_code == 200 and r.json()["status"] == "已吊销"
    client.cookies.clear()
    r = linxia.get("/api/v1/spaces")
    assert r.status_code == 401 and r.json()["code"] == "KEY_REVOKED"


def test_rate_limit(linxia, monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "rate_limit_per_minute", 3)
    codes = [linxia.get("/api/v1/spaces").status_code for _ in range(4)]
    assert codes[:3] == [200, 200, 200] and codes[3] == 429
    r = linxia.get("/api/v1/spaces")
    assert r.headers.get("Retry-After") and check(r)["code"] == "RATE_LIMITED"


def test_patch_short_description_ok(linxia):
    r = linxia.patch("/api/v1/intents/B.REL.GREETING", json={"description": "问候开场"})
    assert r.status_code == 200 and check(r)["description"] == "问候开场"
    r = linxia.patch("/api/v1/intents/B.REL.GREETING", json={"description": "  "})
    assert r.status_code == 422 and "意图描述不能为空" in str(r.json())


def test_v1_delete_only_disabled(linxia):
    body = {**NEW, "code": "B.STOCK.DELV1", "name": "待删开放接口", "description": "短"}
    b = check(linxia.post("/api/v1/intents:batchUpsert", json={"mode": "merge", "intents": [body]}))
    assert b["created"] == 1
    r = linxia.delete("/api/v1/intents/B.STOCK.DELV1")
    assert r.status_code == 409 and check(r)["code"] == "INTENT_NOT_DISABLED"
    assert check(linxia.patch("/api/v1/intents/B.STOCK.DELV1", json={"status": "停用"}))["status"] == "停用"
    r = linxia.delete("/api/v1/intents/B.STOCK.DELV1")
    assert r.status_code == 204
    assert linxia.get("/api/v1/intents/B.STOCK.DELV1").status_code == 404
    r = linxia.delete("/api/v1/intents/B.REL.GREETING")
    assert r.status_code == 409 and check(r)["code"] == "INTENT_NOT_DISABLED"

