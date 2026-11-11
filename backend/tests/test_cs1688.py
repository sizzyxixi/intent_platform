"""1688 客服映射表数据：结构自检 + 在测试库上执行一遍（幂等、mc 不动、只绑真实工具）。"""
import json

from sqlalchemy import select

from app import cs1688
from app.db import SessionLocal
from app.models import Intent, KnowledgeBase, Tool


def test_spec_is_grounded_in_doc():
    spec = cs1688.load()
    doc = spec["doc"]
    buckets = [r["intent_bucket"] for r in doc["mapping"]]
    assert len(buckets) == 17 and [i["bucket"] for i in spec["intents"]] == buckets
    assert {t["tool_id"] for t in doc["tools"]} == set(spec["tool_map"])
    assert [r["规则ID"] for r in doc["rules"]] == ["R1", "R2", "R3", "R4", "R5", "R6"]
    slots = {s["name"] for s in spec["slots"]}
    for it in spec["intents"]:
        assert len(it["examples"]) >= 3 and len(it["description"]) >= 15
        have = {s["name"] for s in it["slots"]}
        assert have <= slots
        for b in it["tools"]:
            assert b["tool"].startswith("xclaw_")  # 只绑西之月目录里的真实命令，不注册规划中的工具
            for v in b["param_map"].values():
                assert v["source"] != "槽位" or v["value"] in have
    assert not spec["tool_map"]["lock_or_hold_stock"]["tools"]  # 目录里没有占仓接口 → 缺口


def _fake_tools(db, spec):
    names = {b["tool"]: b for it in spec["intents"] for b in it["tools"]}
    for n, b in names.items():
        if not db.get(Tool, n):
            db.add(Tool(name=n, type="cli", display_name=n, description="测试", owner="西之月开放平台",
                        params=[{"name": k, "type": "string", "required": True} for k in b["param_map"]],
                        config={"bin": "xclaw-cli", "command_template": "xclaw-cli x " + " ".join(f"--{k} {{{k}}}" for k in b["param_map"]),
                                "confirm_required": False}))
    for n in spec["confirm_tools"]:
        if not db.get(Tool, n):
            db.add(Tool(name=n, type="cli", display_name=n, description="测试", owner="西之月开放平台", params=[],
                        config={"bin": "xclaw-cli", "command_template": "xclaw-cli y", "confirm_required": False}))
    db.commit()


def test_apply_twice_keeps_mc(client):
    spec = cs1688.load()
    with SessionLocal() as db:
        _fake_tools(db, spec)
        mc_before = sorted((i.code, i.status, json.dumps(i.kb_refs)) for i in db.scalars(select(Intent).where(Intent.space_id == "mc")))
        for _ in range(2):
            rep = cs1688.apply(db, spec)
            db.commit()
        cs = list(db.scalars(select(Intent).where(Intent.space_id == "cs")))
        assert len(cs) == 17 and all(i.status == "启用" for i in cs), [x for x in rep["intents"] if x["status"] != "启用"]
        assert all(i.code.split(".")[1] in ("PRE", "MID", "POST", "NONE") for i in cs)
        kbs = {k.id for k in db.scalars(select(KnowledgeBase))}
        assert {"kb_cs_reply", "kb_cs_tools", "kb_cs_conf", "kb_cs_close", "kb_cs_ledger"} <= kbs
        assert not kbs & {"kb_aq", "kb_rule"}
        price = db.get(Tool, "xclaw_platform_alibaba_trade_order_modifyorderprice_call")
        assert price.config["confirm_required"] is True
        mc_after = sorted((i.code, i.status, json.dumps(i.kb_refs)) for i in db.scalars(select(Intent).where(Intent.space_id == "mc")))
        assert mc_before == mc_after
