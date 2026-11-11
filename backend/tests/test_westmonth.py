"""v0.16：西之月 xclaw-cli 官方命令目录 + Skills 接入；删除示例工具；工具多时的列表与校验。"""
import copy

from sqlalchemy import func, select

from app import seed as seed_mod
from app import westmonth as wm
from app.db import SessionLocal
from app.models import Intent, McpServer, SkillPackage, Tool
from app.services import tools as tool_svc

CAT, SKILLS = wm.load_fixture()
BUILTIN = {"pf_read_knowledge", "pf_read_intents", "pf_list_tools", "pf_runtime_config", "pf_write_intents",
           "pf_write_slots", "pf_import_knowledge", "intent_platform.search_knowledge"}


def _cmd(op):
    return next(c for c in CAT["commands"] if c["operationId"] == op)


def test_fixture_is_official_snapshot():
    assert CAT["cli_version"] == "2.2.10" and CAT["source"].startswith("xclaw-cli catalog index")
    assert len(CAT["commands"]) == 7037
    assert sorted(SKILLS) == ["xclaw-cli-assistant", "xclaw-mall", "xclaw-operate", "xclaw-platform", "xclaw-supply"]
    names = wm.tool_names(CAT["commands"])
    assert len(set(names.values())) == len(names)  # 撞名的追加短哈希后唯一
    assert all(len(n) <= 120 and n.startswith("xclaw_") for n in names.values())
    # 转换后同名的一对（'.' 与 '-'）都带哈希后缀，彼此不同
    a, b = names["platform.emag.orders.order.count.get"], names["platform.emag.orders.order-count.get"]
    assert a != b and a.startswith("xclaw_platform_emag_orders_order_count_get_")


def test_cli_payload_matches_official_schema():
    c = _cmd("mall.mall-product.rank-list.query")
    p = wm.cli_payload(c, "xclaw_mall_mall_product_rank_list_query", "2.2.10")
    v = tool_svc.validate_payload(p, True)
    cfg = v["config"]
    assert cfg["command_template"].startswith("xclaw-cli mall mall-product rank-list query --delivery-region-id {delivery_region_id}")
    assert cfg["command_template"].endswith("--json") and cfg["auth"] == "CLI 内置登录（OAuth）"
    assert cfg["auth_env"] == "XCLAW_UMC_TOKEN" and cfg["login_command"] == "xclaw-cli auth login"
    assert cfg["pack"] == "mall" and cfg["group"] == "mall-product" and cfg["source_ref"] == c["operationId"]
    req = [x["name"] for x in v["params"] if x["required"]]
    assert req == ["delivery_region_id"] and v["params"][0]["type"] == "number"
    argv = tool_svc.render_cli(cfg["command_template"], {"delivery_region_id": "100", "nums": "5"})
    assert argv == ["xclaw-cli", "mall", "mall-product", "rank-list", "query", "--delivery-region-id", "100",
                    "--nums", "5", "--json"]
    # 需确认的写操作按官方 confirmRequired 标注
    w = next(c for c in CAT["commands"] if c["confirmRequired"])
    assert wm.cli_payload(w, "x", "2.2.10")["config"]["confirm_required"] is True
    # object / array 参数按 JSON 字符串传
    o = next(c for c in CAT["commands"] if any(a.get("type") == "object" for a in c["requiredArgs"]))
    po = wm.cli_payload(o, "x", "2.2.10")
    arg = next(a for a in o["requiredArgs"] if a.get("type") == "object")
    pp = next(x for x in po["params"] if x["name"] == arg["name"].replace("-", "_"))
    assert pp["type"] == "string" and "JSON" in pp["description"]


def test_purge_examples_then_import(tokens, admin):
    with SessionLocal() as db:
        res = wm.purge_examples(db)
        db.commit()
        assert "sellersprite.asin_detail" in res["deleted_tools"] and "query_stock" in res["deleted_tools"]
        assert set(res["deleted_servers"]) == {"compliance", "image", "pod", "sellersprite"}
        assert {t.name for t in db.scalars(select(Tool))} == BUILTIN
        assert [s.name for s in db.scalars(select(McpServer))] == ["intent_platform"]
        assert db.scalar(select(func.count()).select_from(SkillPackage)) == 0
        assert res["backup"]["intent_bindings"] and res["backup"]["tools"]
        left = {b["tool"] for it in db.scalars(select(Intent)) for b in it.tools}
        assert left <= BUILTIN
        for code in res["downgraded"]:
            assert db.scalar(select(Intent).where(Intent.code == code)).status == "草稿"

        r = wm.import_westmonth(db, CAT, SKILLS)
        db.commit()
        assert r["created"] == 7037 + 5 and r["cli_commands"] == 7037
        assert db.scalar(select(func.count()).select_from(Tool).where(Tool.owner == wm.OWNER)) == 7042
        sk = db.get(Tool, "xclaw_mall")
        assert sk.type == "skill" and sk.config["version"] == 1 and sk.description.startswith("选品网/西之月商城")
        assert any(f["path"] == "references/routing.md" for f in sk.config["files"])
        # 幂等：再导入一次不新建、不改动、技能不升版本
        r2 = wm.import_westmonth(db, CAT, SKILLS)
        db.commit()
        assert (r2["created"], r2["updated"], r2["removed"]) == (0, 0, [])
        assert db.get(Tool, "xclaw_mall").config["version"] == 1

    # 意图关联一个西之月命令后，快照里删掉该命令：被使用的保留，未使用的删除
    name = "xclaw_mall_mall_product_rank_list_query"
    it = admin.get("/api/admin/intents/M.DESIGN.MAIN").json()
    body = {k: it[k] for k in ("name", "category_id", "description", "examples", "slots", "kb_refs", "status")}
    body["tools"] = [{"tool": name, "param_map": {"delivery_region_id": {"source": "常量", "value": "100"}}}]
    assert admin.put("/api/admin/intents/M.DESIGN.MAIN", json=body).status_code == 200
    lst = admin.get("/api/admin/tools").json()
    t = next(x for x in lst if x["name"] == name)
    assert "input_schema" not in t and t["cli"]["pack_name"] == "商城解决方案" and t["cli"]["auth_env"] == "XCLAW_UMC_TOKEN"
    assert [u["code"] for u in t["used_by"]] == ["M.DESIGN.MAIN"]
    small = copy.deepcopy(CAT)
    small["commands"] = [c for c in small["commands"] if c["packCode"] == "supply"]
    with SessionLocal() as db:
        r3 = wm.import_westmonth(db, small, SKILLS)
        db.commit()
        assert r3["kept_in_use"] == [name] and len(r3["removed"]) == 7037 - 18 - 1
        assert db.get(Tool, name) is not None


def test_edit_keeps_cli_source_meta(tokens, admin):
    with SessionLocal() as db:
        wm.import_westmonth(db, {**CAT, "commands": [_cmd("mall.mall-product.rank-list.query")]}, {})
        db.commit()
    name = "xclaw_mall_mall_product_rank_list_query"
    t = admin.get("/api/admin/tools/" + name).json()
    body = {"type": "cli", "display_name": t["display_name"] + "（改）", "description": t["description"], "owner": "测试",
            "params": t["params"], "config": t["cli"]}
    r = admin.put("/api/admin/tools/" + name, json=body)
    assert r.status_code == 200, r.text
    assert r.json()["cli"]["source_ref"] == "mall.mall-product.rank-list.query" and r.json()["cli"]["group"] == "mall-product"
    bad = admin.put("/api/admin/tools/" + name, json={**body, "config": {**t["cli"], "auth_env": "bad-env"}})
    assert bad.status_code == 422


def test_seed_without_demo_tools():
    with SessionLocal() as db:
        seed_mod.reset(db)
        seed_mod.seed(db, seed_mod_data(), demo_tools=False)
        assert {t.name for t in db.scalars(select(Tool))} == BUILTIN
        assert {b["tool"] for it in db.scalars(select(Intent)) for b in it.tools} <= BUILTIN


def seed_mod_data():
    import json
    return json.loads(seed_mod.SEED_FILE.read_text(encoding="utf-8"))
