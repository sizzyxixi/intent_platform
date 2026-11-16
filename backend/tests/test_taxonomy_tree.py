"""分类树按层 / 按节点拉取（max_depth、parent），以及 has_children / child_count。"""
from contract import check

from test_mcp import call


def _assert_node_flags(n, *, children_loaded: bool):
    assert "has_children" in n and "child_count" in n and "id" in n
    assert n["child_count"] >= 0
    assert n["has_children"] == (n["child_count"] > 0)
    if children_loaded:
        assert len(n["children"]) == n["child_count"]
        for c in n["children"]:
            _assert_node_flags(c, children_loaded=True)
    else:
        assert n["children"] == []


def test_full_tree_is_default_and_has_flags(full_key):
    cs = check(full_key.get("/api/v1/taxonomy", space="cs"))
    assert "parent" not in cs
    assert len(cs["categories"]) == 12
    assert sum(len(a["children"]) for a in cs["categories"]) == 46
    for n in cs["categories"]:
        _assert_node_flags(n, children_loaded=True)
    # 叶子的 has_children 为 false
    leaves = [c for a in cs["categories"] for c in a["children"]]
    assert leaves and all(not x["has_children"] and x["child_count"] == 0 for x in leaves)


def test_max_depth_one_truncates_but_flags_remain(full_key):
    cs = check(full_key.get("/api/v1/taxonomy?max_depth=1", space="cs"))
    assert len(cs["categories"]) == 12
    for n in cs["categories"]:
        _assert_node_flags(n, children_loaded=False)
    assert sum(n["child_count"] for n in cs["categories"]) == 46
    assert any(n["has_children"] for n in cs["categories"])


def test_parent_by_code_and_id_drill_down(full_key):
    roots = check(full_key.get("/api/v1/taxonomy?max_depth=1", space="cs"))["categories"]
    stock = next(n for n in roots if n["code"] == "STOCK")
    assert stock["has_children"] and stock["child_count"] > 0

    by_code = check(full_key.get(f"/api/v1/taxonomy?parent={stock['code']}&max_depth=1", space="cs"))
    assert by_code["parent"]["code"] == "STOCK"
    assert by_code["parent"]["id"] == stock["id"]
    assert by_code["parent"]["has_children"] is True
    assert by_code["parent"]["child_count"] == stock["child_count"]
    assert len(by_code["categories"]) == stock["child_count"]
    assert all(n["depth"] == 2 for n in by_code["categories"])
    for n in by_code["categories"]:
        _assert_node_flags(n, children_loaded=False)

    by_id = check(full_key.get(f"/api/v1/taxonomy?parent={stock['id']}&max_depth=1", space="cs"))
    assert [n["name"] for n in by_id["categories"]] == [n["name"] for n in by_code["categories"]]
    assert by_id["parent"]["id"] == stock["id"]

    # 不传 max_depth：从该节点起的完整子树（本空间分类只有两层，与 max_depth=1 形状相同）
    full = check(full_key.get(f"/api/v1/taxonomy?parent={stock['code']}", space="cs"))
    assert len(full["categories"]) == stock["child_count"]
    assert all(n["children"] == [] for n in full["categories"])


def test_parent_not_found_and_bad_max_depth(full_key):
    r = full_key.get("/api/v1/taxonomy?parent=NO_SUCH_NODE", space="cs")
    assert r.status_code == 404 and check(r)["code"] == "CATEGORY_NOT_FOUND"
    r = full_key.get("/api/v1/taxonomy?parent=0", space="cs")
    assert r.status_code == 404 and check(r)["code"] == "CATEGORY_NOT_FOUND"
    r = full_key.get("/api/v1/taxonomy?max_depth=0", space="cs")
    assert r.status_code == 400 and check(r)["code"] == "BAD_REQUEST"
    r = full_key.get("/api/v1/taxonomy?max_depth=6", space="cs")
    assert r.status_code == 400 and check(r)["code"] == "BAD_REQUEST"


def test_runtime_config_same_params(bot):
    full = check(bot.get("/api/v1/runtime-config"))
    tax = full["taxonomy"]
    assert "parent" not in tax
    for n in tax["categories"]:
        _assert_node_flags(n, children_loaded=True)

    sliced = check(bot.get("/api/v1/runtime-config?max_depth=1"))
    assert sliced["version"] == full["version"]
    assert len(sliced["intents"]) == len(full["intents"])
    assert all(n["children"] == [] for n in sliced["taxonomy"]["categories"])
    assert any(n["has_children"] for n in sliced["taxonomy"]["categories"])

    # 完整配置在版本未变时仍 304；带切片参数时不 304
    r = bot.get(f"/api/v1/runtime-config?since_version={full['version']}")
    assert r.status_code == 304
    r = bot.get(f"/api/v1/runtime-config?since_version={full['version']}&max_depth=1")
    assert r.status_code == 200
    assert check(r)["taxonomy"]["categories"][0]["children"] == []

    root = next(n for n in sliced["taxonomy"]["categories"] if n["has_children"])
    drilled = check(bot.get(f"/api/v1/runtime-config?parent={root['code']}&max_depth=1"))
    assert drilled["taxonomy"]["parent"]["code"] == root["code"]
    assert len(drilled["taxonomy"]["categories"]) == root["child_count"]


def test_mcp_list_intents_taxonomy_params(full_key):
    err, full = call(full_key, "list_intents", {"view": "taxonomy"}, space="cs")
    assert not err and len(full["categories"]) == 12
    assert sum(len(a["children"]) for a in full["categories"]) == 46
    _assert_node_flags(full["categories"][0], children_loaded=True)

    err, one = call(full_key, "list_intents", {"view": "taxonomy", "max_depth": 1, "space": "cs"})
    assert not err and len(one["categories"]) == 12
    assert all(n["children"] == [] for n in one["categories"])
    stock = next(n for n in one["categories"] if n["code"] == "STOCK")

    err, kids = call(full_key, "list_intents",
                     {"view": "taxonomy", "parent": stock["code"], "max_depth": 1, "space": "cs"})
    assert not err and kids["parent"]["code"] == "STOCK"
    assert len(kids["categories"]) == stock["child_count"]

    err, b = call(full_key, "list_intents", {"view": "taxonomy", "parent": "NO_SUCH", "space": "cs"})
    assert err and b["code"] == "CATEGORY_NOT_FOUND"
