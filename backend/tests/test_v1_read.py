"""开放接口：鉴权、空间隔离、只读接口（逐个按 openapi 校验响应结构）。"""
from contract import check


def test_error_format_and_auth(client, tokens):
    r = client.get("/api/v1/spaces")
    assert r.status_code == 401
    b = check(r)
    assert set(b) >= {"code", "message", "details", "request_id"}
    assert r.headers["X-Request-Id"] == b["request_id"]
    r = client.get("/api/v1/spaces", headers={"Authorization": "Bearer ipk_wrong"})
    assert r.status_code == 401 and r.json()["code"] == "UNAUTHORIZED"


def test_spaces_only_bound(linxia, full_key):
    b = check(linxia.get("/api/v1/spaces"))
    assert [s["space_id"] for s in b] == ["cs"]
    b = check(full_key.get("/api/v1/spaces"))
    assert {s["space_id"]: s["code_prefix"] for s in b} == {"cs": "B", "mc": "M"}


def test_space_resolution(full_key, linxia):
    # 默认第一个绑定空间
    assert check(full_key.get("/api/v1/intents?page_size=1"))["items"][0]["space"] == "cs"
    # 请求头
    assert check(full_key.get("/api/v1/intents?page_size=1", space="mc"))["items"][0]["space"] == "mc"
    # 查询参数
    assert check(full_key.get("/api/v1/intents?page_size=1&space=mc"))["total"] == 44
    # 未绑定
    r = linxia.get("/api/v1/intents", space="mc")
    assert r.status_code == 403 and check(r)["code"] == "SPACE_FORBIDDEN"


def test_scope_forbidden(linxia):
    r = linxia.get("/api/v1/tools")
    assert r.status_code == 403 and r.json()["code"] == "SCOPE_FORBIDDEN"


def test_taxonomy_counts(full_key):
    cs = check(full_key.get("/api/v1/taxonomy", space="cs"))
    assert len(cs["l1"]) == 12 and sum(len(a["l2"]) for a in cs["l1"]) == 46
    mc = check(full_key.get("/api/v1/taxonomy", space="mc"))
    assert len(mc["l1"]) == 6 and sum(len(a["l2"]) for a in mc["l1"]) == 14


def test_slots_per_space(full_key):
    assert len(check(full_key.get("/api/v1/slots", space="cs"))) == 9
    mc = check(full_key.get("/api/v1/slots", space="mc"))
    assert len(mc) == 24
    plat = next(s for s in mc if s["name"] == "platform")
    assert plat["type"] == "枚举" and "TikTok" in plat["options"]


def test_intents_list_filters_pagination(full_key):
    b = check(full_key.get("/api/v1/intents?page_size=100", space="cs"))
    assert b["total"] == 123 and len(b["items"]) == 100
    b2 = check(full_key.get("/api/v1/intents?page_size=100&page=2", space="cs"))
    assert len(b2["items"]) == 23
    assert not {i["code"] for i in b["items"]} & {i["code"] for i in b2["items"]}
    en = check(full_key.get("/api/v1/intents?status=启用", space="cs"))
    assert en["total"] == 15 and all(i["status"] == "启用" for i in en["items"])
    st = check(full_key.get("/api/v1/intents?l1=STOCK", space="cs"))
    assert st["total"] > 0 and all(i["l1"] == "STOCK" for i in st["items"])
    q = check(full_key.get("/api/v1/intents?q=现货", space="cs"))
    assert q["total"] >= 1
    r = full_key.get("/api/v1/intents?status=xx", space="cs")
    assert r.status_code == 400 and check(r)["code"] == "BAD_REQUEST"
    r = full_key.get("/api/v1/intents?page_size=1000", space="cs")
    assert r.status_code == 400
    ua = check(full_key.get("/api/v1/intents?updated_after=2026-11-09T00:00:00Z", space="cs"))
    assert all(i["updated_at"] > "2026-11-09" for i in ua["items"])


def test_intent_detail_and_isolation(full_key):
    b = check(full_key.get("/api/v1/intents/B.REL.ESCALATE", space="cs"))
    assert b["status"] == "启用" and b["tools"][0]["tool"] == "handoff_human"
    assert b["tools"][0]["param_map"]["session_id"] == {"source": "上下文", "value": "session_id"}
    r = full_key.get("/api/v1/intents/B.REL.ESCALATE", space="mc")
    assert r.status_code == 404 and check(r)["code"] == "INTENT_NOT_FOUND"


def test_kbs_docs_chunks(bot):
    kbs = check(bot.get("/api/v1/kbs"))
    assert len(kbs) == 11
    aq = next(k for k in kbs if k["kb_id"] == "kb_aq")
    assert aq["headers"] == ["问题", "答案", "意图编码", "适用范围"]
    docs = check(bot.get("/api/v1/kbs/kb_aq/documents"))
    assert docs and docs[0]["data_size"].endswith("条")
    ch = check(bot.get("/api/v1/kbs/kb_aq/chunks?page_size=5"))
    assert ch["total"] == 12 and len(ch["items"]) == 5 and "cells" in ch["items"][0]
    r = bot.get("/api/v1/kbs/kb_none/chunks")
    assert r.status_code == 404 and check(r)["code"] == "KB_NOT_FOUND"
    # 知识库是全局的：传 X-Space-Id 也忽略
    assert len(check(bot.get("/api/v1/kbs", space="mc"))) == 11


def test_search(bot):
    hits = check(bot.post("/api/v1/kbs/kb_aq/search", json={"query": "有现货吗", "top_k": 3}, idem=False))
    assert hits and hits[0]["chunk"]["chunk_id"] == "Q1" and hits[0]["score"] >= 0.9
    assert len(hits) <= 3
    r = bot.post("/api/v1/kbs/kb_aq/search", json={"top_k": 3}, idem=False)
    assert r.status_code == 400
    r = bot.post("/api/v1/kbs/kb_aq/search", json={"query": "有现货吗", "top_k": 50}, idem=False)
    assert r.status_code == 400


def test_search_tag_filter(full_key):
    allhits = check(full_key.post("/api/v1/kbs/kb_ban/search", json={"query": "违禁词 最好", "threshold": 0},
                                  idem=False))
    tt = check(full_key.post("/api/v1/kbs/kb_ban/search",
                             json={"query": "违禁词 最好", "platform": "TikTok", "threshold": 0}, idem=False))
    for h in tt:
        p = h["chunk"]["effective_tags"]["platform"]
        assert not p or "TikTok" in p
    assert len(tt) <= len(allhits)


def test_tools(bot):
    ts = check(bot.get("/api/v1/tools"))
    assert len(ts) == 29
    b = check(bot.get("/api/v1/tools?source=builtin"))
    assert len(b) == 8 and sum(1 for t in b if t["maintenance"]) == 3
    cli = [t for t in ts if t["type"] == "cli"]
    assert {t["cli"]["bin"] for t in cli} == {"xclaw-cli"}
    assert len(cli) == 2
    r = bot.get("/api/v1/tools?source=x")
    assert r.status_code == 400


def test_recognition_get(full_key):
    cs = check(full_key.get("/api/v1/recognition", space="cs"))
    assert cs["final_action"] == "handoff_human" and cs["version"] == 42 and cs["candidate_min"] == 0.3
    mc = check(full_key.get("/api/v1/recognition", space="mc"))
    assert mc["final_action"] == "general_assistant" and mc["version"] == 7


def test_runtime_config(bot):
    r = bot.get("/api/v1/runtime-config")
    b = check(r)
    assert b["version"] == 42 and len(b["intents"]) == 15
    assert all(i["code"].startswith("B.") for i in b["intents"])
    used = {t["tool"] for i in b["intents"] for t in i["tools"]}
    assert {t["name"] for t in b["tools"]} == used
    assert b["recognition"]["final_action"] == "handoff_human"
    r = bot.get("/api/v1/runtime-config?since_version=42")
    assert r.status_code == 304
    r = bot.get("/api/v1/runtime-config?since_version=41")
    assert r.status_code == 200


def test_imports_get(bot):
    b = check(bot.get("/api/v1/imports/IMP-1002"))
    assert b["import_id"] == "IMP-1002"
    r = bot.get("/api/v1/imports/IMP-9")
    assert r.status_code == 404


def test_every_openapi_operation_is_routed(client):
    from contract import spec
    from app.main import app
    routes = {(m.upper(), p) for p, ops in app.openapi()["paths"].items() for m in ops}
    missing = []
    for p, ops in spec()["paths"].items():
        for m in ops:
            if m in ("get", "post", "put", "patch", "delete") and (m.upper(), p) not in routes:
                missing.append(f"{m.upper()} {p}")
    assert not missing, missing
