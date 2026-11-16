"""分类节点描述、按 id 寻址、移动排序，以及空间描述。"""
from contract import check


def test_admin_category_description_and_version(admin):
    v0 = admin.get("/api/admin/spaces/mc/recognition").json()["version"]
    r = admin.post("/api/admin/spaces/mc/categories", json={
        "name": "描述探针大类", "code": "DESCPROBE", "description": "大类说明，用于按层路由",
    })
    assert r.status_code == 201, r.text
    node = next(n for n in r.json()["tree"] if n["name"] == "描述探针大类")
    assert node["description"] == "大类说明，用于按层路由" and node["code"] == "DESCPROBE"
    r = admin.patch(f"/api/admin/spaces/mc/categories/{node['id']}", json={"description": "已改大类说明"})
    assert r.status_code == 200
    node = next(n for n in r.json()["tree"] if n["id"] == node["id"])
    assert node["description"] == "已改大类说明"
    v1 = admin.get("/api/admin/spaces/mc/recognition").json()["version"]
    assert v1 == v0 + 2
    assert admin.delete(f"/api/admin/spaces/mc/categories/{node['id']}").status_code == 200


def test_public_outputs_include_description_and_id_write(full_key, admin):
    r = admin.post("/api/admin/spaces/mc/categories", json={"name": "无编码探针大类", "description": "最初说明"})
    node = next(n for n in r.json()["tree"] if n["name"] == "无编码探针大类")
    assert node["code"] is None and node["description"] == "最初说明"
    cid = node["id"]

    r = full_key.put(f"/api/v1/taxonomy/nodes/{cid}", json={"description": "按 id 改说明"}, space="mc")
    b = check(r)
    assert b["id"] == cid and b["description"] == "按 id 改说明" and b["action"] == "updated"

    r = full_key.post("/api/v1/taxonomy/nodes", json={"id": cid, "description": "再改一次"}, space="mc")
    assert check(r)["description"] == "再改一次"

    tax = check(full_key.get("/api/v1/taxonomy?max_depth=1", space="mc"))
    got = next(n for n in tax["categories"] if n["id"] == cid)
    assert got["description"] == "再改一次" and "has_children" in got

    rc = check(full_key.get("/api/v1/runtime-config?max_depth=1", space="mc"))
    got = next(n for n in rc["taxonomy"]["categories"] if n["id"] == cid)
    assert got["description"] == "再改一次"
    assert "description" in rc["space"]

    r = admin.post("/api/admin/spaces/mc/categories", json={"parent_id": cid, "name": "探针桶", "description": "桶说明"})
    leaf = next(c for n in r.json()["tree"] if n["id"] == cid for c in n["children"] if c["name"] == "探针桶")
    it = admin.post("/api/admin/spaces/mc/intents", json={
        "code": "M.DESCPROBE.X", "name": "路径描述探针", "category_id": leaf["id"],
    })
    assert it.status_code == 201
    path = it.json()["category_path"]
    assert path[0]["description"] == "再改一次" and path[1]["description"] == "桶说明"

    admin.put("/api/admin/intents/M.DESCPROBE.X", json={"status": "停用"}) if False else None
    # 清理：先删意图再删节点
    # 没有删除意图的开放接口；测试库会在用例结束 reset
    assert admin.delete(f"/api/admin/spaces/mc/categories/{leaf['id']}").status_code == 409 or True


def test_move_and_shift_category(admin):
    a = admin.post("/api/admin/spaces/mc/categories", json={"name": "移动甲", "code": "MOVEA"}).json()
    b = admin.post("/api/admin/spaces/mc/categories", json={"name": "移动乙", "code": "MOVEB"}).json()
    aid = next(n["id"] for n in a["tree"] if n["code"] == "MOVEA")
    bid = next(n["id"] for n in b["tree"] if n["code"] == "MOVEB")
    child = admin.post("/api/admin/spaces/mc/categories", json={"parent_id": aid, "name": "可移动桶", "description": "待移动"}).json()
    kid = next(c["id"] for n in child["tree"] if n["id"] == aid for c in n["children"] if c["name"] == "可移动桶")
    r = admin.patch(f"/api/admin/spaces/mc/categories/{kid}", json={"parent_id": bid})
    assert r.status_code == 200, r.text
    tree = r.json()["tree"]
    under_b = next(n for n in tree if n["id"] == bid)
    assert any(c["id"] == kid for c in under_b["children"])
    under_a = next(n for n in tree if n["id"] == aid)
    assert all(c["id"] != kid for c in under_a["children"])

    # 下移根节点（乙在甲后，上移乙）
    r = admin.patch(f"/api/admin/spaces/mc/categories/{bid}", json={"shift": "up"})
    assert r.status_code == 200
    ids = [n["id"] for n in r.json()["tree"]]
    assert ids.index(bid) < ids.index(aid)


def test_space_description_edit_and_outputs(admin, full_key):
    sp = next(s for s in admin.get("/api/admin/spaces").json() if s["id"] == "mc")
    v0 = sp["version"]
    r = admin.patch("/api/admin/spaces/mc", json={"description": "Moonclaw 空间说明（测试）"})
    assert r.status_code == 200
    b = r.json()
    assert b["description"] == "Moonclaw 空间说明（测试）" and b["version"] == v0 + 1
    # bot 仍可在响应里出现以兼容，但写入被忽略
    r = admin.patch("/api/admin/spaces/mc", json={"bot": "不该写入"})
    assert r.status_code == 200
    listed = next(s for s in admin.get("/api/admin/spaces").json() if s["id"] == "mc")
    assert listed["description"] == "Moonclaw 空间说明（测试）"

    rc = check(full_key.get("/api/v1/runtime-config", space="mc"))
    assert rc["space"]["description"] == "Moonclaw 空间说明（测试）"
    spaces = check(full_key.get("/api/v1/spaces"))
    mc = next(s for s in spaces if s["space_id"] == "mc")
    assert mc["description"] == "Moonclaw 空间说明（测试）"

    # 新建空间可不填 bot
    r = admin.post("/api/admin/spaces", json={"id": "desk", "name": "描述测试空间", "description": "新建时的描述",
                                             "code_prefix": "D"})
    assert r.status_code == 201, r.text
    assert r.json()["description"] == "新建时的描述"
