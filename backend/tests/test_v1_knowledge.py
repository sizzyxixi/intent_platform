"""开放接口：知识导入（异步构建、表头校验、覆盖确认、切片保持）、切片修改。"""
from contract import check

HDR = "问题,答案,意图编码,适用范围\n"


def _import(api, kb, files, overwrite=False, key=None):
    data = {"overwrite": "true"} if overwrite else {}
    h = api.h()
    if key:
        h["Idempotency-Key"] = key
    return api.c.post(f"/api/v1/kbs/{kb}/documents:import", files=[("files", f) for f in files], data=data, headers=h)


def test_import_new_table_doc(linxia):
    csv = (HDR + "支持开发票吗,可以开增值税专票,B.PRICE.QUOTE,全店\n能定制logo吗,500 件起可定制,,全店\n").encode()
    r = _import(linxia, "kb_aq", [("开票与定制.csv", csv, "text/csv")])
    assert r.status_code == 202
    t = check(r)
    assert t["status"] == "构建中" and t["documents"][0]["name"] == "开票与定制.csv"
    # TestClient 在响应后同步执行后台构建
    t2 = check(linxia.get(f"/api/v1/imports/{t['import_id']}"))
    assert t2["status"] == "构建成功"
    docs = linxia.get("/api/v1/kbs/kb_aq/documents").json()
    d = next(x for x in docs if x["name"] == "开票与定制.csv")
    assert d["status"] == "构建成功" and d["data_size"] == "2 条"
    hits = check(linxia.post("/api/v1/kbs/kb_aq/search", json={"query": "支持开发票吗"}, idem=False))
    assert hits[0]["doc_name"] == "开票与定制.csv"


def test_header_mismatch(linxia):
    r = _import(linxia, "kb_aq", [("x.csv", "问题,回答\na,b\n".encode(), "text/csv")])
    b = check(r)
    assert r.status_code == 400 and b["code"] == "HEADER_MISMATCH"
    assert b["details"]["expected"] == ["问题", "答案", "意图编码", "适用范围"]


def test_extension_rules(linxia):
    r = _import(linxia, "kb_aq", [("x.md", b"# hi", "text/markdown")])
    assert r.status_code == 400
    r = _import(linxia, "kb_rule", [("x.csv", b"a,b\n1,2\n", "text/csv")])
    assert r.status_code == 400


def test_overwrite_requires_confirm_and_keeps_chunk_ids(linxia):
    v0 = linxia.get("/api/v1/recognition").json()["version"]
    csv = (HDR + "有现货吗,现货约 3000 件,B.STOCK.AVAIL,全店\n今天能发多少,16 点前下单当天发,B.STOCK.AVAIL,全店\n"
           "库存够不够3000件,够,B.STOCK.QTY,全店\n").encode()
    f = ("现货与库存.xlsx".replace(".xlsx", ".csv"), csv, "text/csv")
    # 同名判定按文件名；这里用 xlsx 原名的 csv 版本不同名，改为同名 csv 文档 A3 测试
    r = _import(linxia, "kb_aq", [("物流售后.csv", (HDR + "包邮吗,满 500 包邮,B.PRICE.FREIGHT,全店\n"
                                                          "怎么还没发货,今天发,B.SHIP.URGE,全店\n").encode(), "text/csv")])
    b = check(r)
    assert r.status_code == 409 and b["code"] == "OVERWRITE_REQUIRED"
    assert "B.AS.REFUND" in b["details"]["affected_intents"]
    r = _import(linxia, "kb_aq", [("物流售后.csv", (HDR + "包邮吗,满 500 包邮,B.PRICE.FREIGHT,全店\n"
                                                          "怎么还没发货,今天发,B.SHIP.URGE,全店\n").encode(), "text/csv")],
                overwrite=True)
    assert r.status_code == 202
    ch = linxia.get("/api/v1/kbs/kb_aq/chunks?doc_id=A3").json()["items"]
    ids = {c["chunk_id"]: c for c in ch}
    assert set(ids) == {"Q9", "Q10"}  # 首列相同的行保持原切片 ID
    assert ids["Q9"]["cells"]["答案"] == "满 500 包邮"
    refund = linxia.get("/api/v1/intents/B.AS.REFUND").json()
    assert not any(r["chunk_id"] == "Q11" for r in refund["kb_refs"])
    assert linxia.get("/api/v1/recognition").json()["version"] > v0
    _ = f


def test_text_kb_import_and_failure(linxia):
    md = "# 发票规则\n\n可开增值税普通发票和专用发票。\n\n专票需提供税号与开户行。\n".encode()
    r = _import(linxia, "kb_rule", [("发票规则.md", md, "text/markdown"), ("截图.png", b"\x89PNG....", "image/png")])
    t = check(r)
    assert r.status_code == 202
    t2 = linxia.get(f"/api/v1/imports/{t['import_id']}").json()
    assert t2["status"] in ("构建成功", "部分失败", "构建失败")
    docs = {d["name"]: d for d in linxia.get("/api/v1/kbs/kb_rule/documents").json()}
    assert docs["发票规则.md"]["status"] == "构建成功"
    assert docs["截图.png"]["status"] == "构建失败"


def test_idempotent_multipart(linxia):
    csv = (HDR + "a1,b1,,全店\n").encode()
    r1 = _import(linxia, "kb_aq", [("idem.csv", csv, "text/csv")], key="same-key-1")
    r2 = _import(linxia, "kb_aq", [("idem.csv", csv, "text/csv")], key="same-key-1")
    assert r1.status_code == r2.status_code == 202 and r1.json()["import_id"] == r2.json()["import_id"]
    r3 = _import(linxia, "kb_aq", [("idem.csv", csv + b"x", "text/csv")], key="same-key-1")
    assert r3.status_code == 409


def test_update_chunk(linxia):
    r = linxia.put("/api/v1/kbs/kb_aq/chunks/Q1", json={"cells": {"问题": "有现货吗", "答案": "现货 2,500 件",
                                                                  "意图编码": "B.STOCK.AVAIL", "适用范围": "全店"}})
    b = check(r)
    assert b["cells"]["答案"] == "现货 2,500 件" and "现货 2,500 件" in b["content"]
    assert "B.STOCK.AVAIL" in b["linked_intents"]
    r = linxia.put("/api/v1/kbs/kb_aq/chunks/Q1", json={"cells": {"问题": "x"}})
    assert r.status_code == 400 and r.json()["code"] == "HEADER_MISMATCH"
    r = linxia.put("/api/v1/kbs/kb_rule/chunks/D1-1", json={"content": "新的口径说明，现货以仓库系统为准。"})
    assert check(r)["content"].startswith("新的口径")
    r = linxia.put("/api/v1/kbs/kb_rule/chunks/NOPE", json={"content": "x"})
    assert r.status_code == 404
    # 改切片后检索能命中新内容
    hits = linxia.post("/api/v1/kbs/kb_rule/search", json={"query": "仓库系统为准"}, idem=False).json()
    assert hits and hits[0]["chunk"]["chunk_id"] == "D1-1"


def test_knowledge_write_scope(bot):
    r = bot.put("/api/v1/kbs/kb_aq/chunks/Q1", json={"content": "x"})
    assert r.status_code == 403
