"""v0.15：MCP 凭据列表 / URL 去密钥、技能包上传与版本、工作流（Dify / Coze / 通用）、文件 / 图片槽位、意图绑定校验。"""
import io
import json
import os
import socket
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import mini_mcp_server as mini  # noqa: E402
from contract import check  # noqa: E402


@pytest.fixture(scope="module")
def mini_url():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    srv = mini.serve(port)
    yield f"http://127.0.0.1:{port}"
    srv.shutdown()


# ---------------- MCP 凭据 ----------------
def test_mcp_credentials_multi_and_url_strip(admin, mini_url):
    body = {"name": "ss_test", "display_name": "多凭据", "transport": "streamable_http", "owner": "测试",
            "url": f"{mini_url}/mcp?secret-key=leak123&region=us",
            "credentials": [{"location": "header", "name": "X-App-Id", "env": "MCP_APP_ID", "secret": "app-1"},
                            {"location": "header", "name": "X-App-Key", "env": "MCP_APP_KEY", "secret": "key-1"}]}
    r = admin.post("/api/admin/mcp-servers", json=body)
    assert r.status_code == 201, r.text
    sv = r.json()
    assert "leak123" not in json.dumps(sv) and sv["url"] == f"{mini_url}/mcp?region=us"
    assert sv["warnings"] and "secret-key" in sv["warnings"][0]
    creds = {c["name"]: c for c in sv["credentials"]}
    assert creds["secret-key"] == {"location": "query", "name": "secret-key", "env": "MCP_SS_TEST_SECRET_KEY", "has_secret": True}
    assert creds["X-App-Id"]["has_secret"] and creds["X-App-Key"]["env"] == "MCP_APP_KEY"
    # 平台用保存的密钥在线同步：请求头 + URL 参数都要带上
    os.environ["MINI_MCP_HEADER"] = "X-App-Key=key-1"
    os.environ["MINI_MCP_QUERY"] = "secret-key=leak123"
    try:
        p = admin.post("/api/admin/mcp-servers/ss_test/sync/preview", json={})
        assert p.status_code == 200, p.text
        # 改错密钥 → 401 → 502
        r = admin.put("/api/admin/mcp-servers/ss_test", json={**body, "url": f"{mini_url}/mcp?region=us",
                                                                "credentials": [{"location": "header", "name": "X-App-Key", "env": "MCP_APP_KEY", "secret": "bad"},
                                                                                {"location": "query", "name": "secret-key", "env": "MCP_SS_TEST_SECRET_KEY"}]})
        assert r.status_code == 200, r.text
        assert [c["name"] for c in r.json()["credentials"]] == ["X-App-Key", "secret-key"]
        assert admin.post("/api/admin/mcp-servers/ss_test/sync/preview", json={}).status_code == 502
    finally:
        os.environ.pop("MINI_MCP_HEADER")
        os.environ.pop("MINI_MCP_QUERY")
    # 校验：stdio 不能用请求头；env 名必须大写
    r = admin.post("/api/admin/mcp-servers", json={"name": "bad_cred", "display_name": "x", "owner": "o", "transport": "streamable_http",
                                                   "url": "https://a.example.com/mcp",
                                                   "credentials": [{"location": "header", "name": "X-Key", "env": "lower"}]})
    assert r.status_code == 422 and "环境变量名" in r.text
    r = admin.post("/api/admin/mcp-servers", json={"name": "bad_stdio", "display_name": "x", "owner": "o", "transport": "stdio",
                                                   "command": "npx", "credentials": [{"location": "header", "name": "X-Key", "env": "K"}]})
    assert r.status_code == 422


def test_mcp_sellersprite_example_sync_clears_flag(admin):
    tools = [{"name": "asin_detail", "description": "ASIN 详情", "inputSchema": {"type": "object", "properties": {"asin": {"type": "string"}}}}]
    p = admin.post("/api/admin/mcp-servers/sellersprite/sync/preview", json={"tools_json": json.dumps({"tools": tools})}).json()
    assert p["diff"]["summary"]["removed"] == 3
    r = admin.post("/api/admin/mcp-servers/sellersprite/sync/apply", json={"tools": p["tools"], "source": p["source"]})
    assert r.status_code == 200 and r.json()["server"]["example_data"] is False


# ---------------- 技能包 ----------------
SKILL_MD = "---\nname: Poster-Maker\ndescription: 根据商品信息生成促销海报文案与版式说明\n---\n\n# 海报\n按步骤生成。\n"


def _upload_folder(admin, md=SKILL_MD, extra=None):
    files = [("files", ("SKILL.md", md.encode(), "text/markdown")),
             ("files", ("generate.py", b"print('hi')", "text/plain"))]
    data = {"paths": ["poster/SKILL.md", "poster/scripts/generate.py"]}
    for p, c in (extra or {}).items():
        files.append(("files", (p.split("/")[-1], c, "application/octet-stream")))
        data["paths"].append(p)
    return admin.post("/api/admin/skill-packages", files=files, data=data)


def test_skill_upload_create_version_download(admin, full_key):
    r = _upload_folder(admin)
    assert r.status_code == 201, r.text
    pk = r.json()
    assert pk["name"] == "Poster-Maker" and pk["suggested_tool_name"] == "poster_maker"
    assert [f["path"] for f in pk["files"]] == ["SKILL.md", "scripts/generate.py"]
    # 无前置元数据 / 无 SKILL.md / 路径穿越
    assert _upload_folder(admin, md="# 没有 frontmatter").status_code == 422
    r = admin.post("/api/admin/skill-packages", files=[("files", ("a.txt", b"x", "text/plain"))])
    assert r.status_code == 422 and r.json()["code"] == "SKILL_NO_MD"
    assert _upload_folder(admin, extra={"../evil.sh": b"rm"}).status_code == 400
    # 注册技能：描述取 SKILL.md（传入的描述被忽略），没有入参
    body = {"name": "poster_maker", "type": "skill", "display_name": "海报生成", "description": "随便写", "owner": "测试",
            "params": [{"name": "x", "type": "string"}], "config": {"package_id": pk["package_id"]}}
    r = admin.post("/api/admin/tools", json=body)
    assert r.status_code == 201, r.text
    t = r.json()
    assert t["description"] == "根据商品信息生成促销海报文案与版式说明" and t["params"] == [] and "input_schema" not in t
    assert t["skill"]["version"] == 1 and t["skill"]["download_path"] == "/api/v1/tools/poster_maker/skill-package"
    # 同一个暂存包不能用两次
    assert admin.post("/api/admin/tools", json={**body, "name": "poster_2"}).status_code == 422
    # 不传包时编辑只改显示名；重新上传 = 新版本
    assert admin.put("/api/admin/tools/poster_maker", json={**body, "display_name": "海报 2", "config": {}}).json()["skill"]["version"] == 1
    md2 = SKILL_MD.replace("促销海报", "节日海报")
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w") as z:
        z.writestr("poster/SKILL.md", md2)
        z.writestr("poster/assets/a.png", b"\x89PNG")
        z.writestr("__MACOSX/poster/._SKILL.md", b"junk")
    r = admin.post("/api/admin/skill-packages", files=[("files", ("poster.zip", zbuf.getvalue(), "application/zip"))])
    assert r.status_code == 201, r.text
    assert r.json()["source"] == "zip" and [f["path"] for f in r.json()["files"]] == ["SKILL.md", "assets/a.png"]
    t = admin.put("/api/admin/tools/poster_maker", json={**body, "config": {"package_id": r.json()["package_id"]}}).json()
    assert t["skill"]["version"] == 2 and "节日海报" in t["description"]
    d = admin.get("/api/admin/tools/poster_maker").json()
    assert [v["version"] for v in d["skill_versions"]] == [2, 1] and "节日海报" in d["skill_md"]
    z = admin.get("/api/admin/tools/poster_maker/skill-package?version=1")
    assert z.status_code == 200 and z.headers["X-Skill-Version"] == "1"
    assert sorted(zipfile.ZipFile(io.BytesIO(z.content)).namelist()) == ["SKILL.md", "scripts/generate.py"]
    # 开放接口：工具目录 + 下载
    tl = check(full_key.get("/api/v1/tools"))
    sk = next(x for x in tl if x["name"] == "poster_maker")
    assert sk["type"] == "skill" and sk["skill"]["version"] == 2
    z = full_key.get("/api/v1/tools/poster_maker/skill-package")
    assert z.status_code == 200 and z.headers["X-Skill-Version"] == "2"
    assert full_key.get("/api/v1/tools/query_stock/skill-package").status_code == 400
    # 测试调用：技能只给预览，没有入参
    pv = admin.post("/api/admin/tools/poster_maker/test", json={"values": {}}).json()
    assert pv["request"]["version"] == 2 and "SKILL.md" in pv["request"]["files"]


def test_skill_binding_ignores_param_map(admin):
    it = admin.get("/api/admin/intents/M.DESIGN.MAIN").json()
    sk = [b for b in it["tools"] if b["tool"] == "gen_image_skill"]
    assert sk and sk[0]["param_map"] == {}
    r = admin.put("/api/admin/intents/M.DESIGN.MAIN", json={"tools": [{"tool": "gen_image_skill", "param_map": {"task": {"source": "常量", "value": "main"}}}]})
    assert r.status_code == 200, r.text
    assert r.json()["tools"][0]["param_map"] == {}


# ---------------- 工作流 ----------------
DIFY_PARAMS = {"user_input_form": [
    {"text-input": {"label": "用户问题", "variable": "query", "required": True, "default": ""}},
    {"select": {"label": "风格", "variable": "style", "required": False, "options": ["简约", "复古"], "default": "简约"}},
    {"file": {"label": "参考图", "variable": "image", "required": True}},
    {"file-list": {"label": "附件", "variable": "attachments", "required": False}},
    {"number": {"label": "数量", "variable": "n", "required": True, "default": 2}}]}

COZE_INFO = {"code": 0, "msg": "", "data": {
    "workflow_detail": {"workflow_id": "7400000000000000001"},
    "input": {"parameters": {"input": {"type": "string", "required": True, "description": "工作流输入"},
                             "img": {"type": "string", "assist_type": 2, "required": False},
                             "lang": {"type": "string", "required": False, "default_value": "en"}}},
    "output": {"parameters": {"output": {"type": "string"}, "url": {"type": "string"}}}}}


def test_workflow_sync_inputs_and_create(admin, full_key):
    dify = {"platform": "dify", "base_url": "https://api.dify.ai/v1", "auth_env": "DIFY_APP_KEY", "response_mode": "blocking",
            "user": {"mode": "session"}}
    r = admin.post("/api/admin/workflows/sync-inputs", json={"config": dify, "raw_json": json.dumps(DIFY_PARAMS)})
    assert r.status_code == 200, r.text
    ps = {p["name"]: p for p in r.json()["params"]}
    assert ps["image"]["type"] == "file" and ps["attachments"]["type"] == "file_list" and ps["n"]["type"] == "number"
    assert ps["style"]["default"] == "简约" and ps["n"]["default"] == "2" and ps["query"]["required"]
    # 在线同步必须临时给密钥
    r = admin.post("/api/admin/workflows/sync-inputs", json={"config": dify})
    assert r.status_code == 400 and r.json()["code"] == "WORKFLOW_SECRET_REQUIRED"
    coze = {"platform": "coze", "region": "coze.cn", "workflow_id": "7400000000000000001", "auth_env": "COZE_PAT"}
    r = admin.post("/api/admin/workflows/sync-inputs", json={"config": coze, "raw_json": json.dumps(COZE_INFO)}).json()
    assert {p["name"]: p["type"] for p in r["params"]} == {"input": "string", "img": "file", "lang": "string"}
    assert r["output_mapping"] == [{"from": "data.output", "as": "output"}, {"from": "data.url", "as": "url"}]

    body = {"name": "dify_poster", "type": "workflow", "display_name": "Dify 海报", "description": "Dify 工作流生成海报", "owner": "测试",
            "params": list(ps.values()), "config": {**dify, "timeout_sec": "60", "confirm_required": True,
                                                    "output_mapping": [{"from": "data.outputs.result", "as": "result"}]}}
    r = admin.post("/api/admin/tools", json=body)
    assert r.status_code == 201, r.text
    wf = r.json()["workflow"]
    assert wf["user"] == {"mode": "session"} and wf["timeout_sec"] == 60 and wf["confirm_required"] is True
    pv = admin.post("/api/admin/tools/dify_poster/test", json={"values": {"query": "做海报", "image": "https://x/1.png"}}).json()
    assert pv["request"]["url"] == "https://api.dify.ai/v1/workflows/run"
    assert pv["request"]["json"]["inputs"]["n"] == 2 and pv["request"]["json"]["user"] == "$session.user"
    assert pv["request"]["headers"]["Authorization"] == "Bearer $DIFY_APP_KEY"
    # Coze：流式 + 高级字段
    cb = {"name": "coze_tr", "type": "workflow", "display_name": "Coze 翻译", "description": "Coze 工作流翻译", "owner": "测试",
          "params": [{"name": "input", "type": "string", "required": True}],
          "config": {**coze, "response_mode": "streaming", "workflow_version": "v0.0.3", "is_async": True, "ext": '{"a": 1}'}}
    r = admin.post("/api/admin/tools", json=cb)
    assert r.status_code == 201, r.text
    pv = admin.post("/api/admin/tools/coze_tr/test", json={"values": {"input": "你好"}}).json()["request"]
    assert pv["url"] == "https://api.coze.cn/v1/workflow/stream_run"
    assert pv["json"] == {"workflow_id": "7400000000000000001", "parameters": {"input": "你好"}, "workflow_version": "v0.0.3",
                          "ext": {"a": 1}, "is_async": True}
    # 校验
    bad = {**cb, "name": "coze_bad", "config": {**coze, "workflow_id": "", "auth_env": "coze pat", "ext": "[1]"}}
    r = admin.post("/api/admin/tools", json=bad)
    assert r.status_code == 422
    msgs = json.dumps(r.json(), ensure_ascii=False)
    assert "工作流 ID" in msgs and "环境变量名" in msgs and "ext" in msgs
    tl = check(full_key.get("/api/v1/tools"))
    assert next(x for x in tl if x["name"] == "coze_tr")["workflow"]["region"] == "coze.cn"


def test_workflow_binding_rules_and_file_slots(admin):
    # 文件 / 图片槽位
    r = admin.post("/api/admin/spaces/mc/slots", json={"name": "ref_file", "display_name": "参考文件", "type": "文件", "default_ask": "请上传参考文件"})
    assert r.status_code == 201, r.text
    slots = {s["name"]: s for s in admin.get("/api/admin/spaces/mc/slots").json()}
    assert slots["product_image"]["type"] == "图片"
    body = {"name": "wf_img", "type": "workflow", "display_name": "图片工作流", "description": "按图片生成", "owner": "测试",
            "params": [{"name": "image", "type": "file", "required": True}, {"name": "style", "type": "string", "required": True, "default": "简约"},
                       {"name": "prompt", "type": "string", "required": True}],
            "config": {"platform": "generic", "url": "https://wf.example.com/run", "method": "POST", "auth": "无"}}
    assert admin.post("/api/admin/tools", json=body).status_code == 201
    it = admin.get("/api/admin/intents/M.DESIGN.MAIN").json()
    base = [{"tool": b["tool"], "param_map": b["param_map"]} for b in it["tools"]]
    slot_list = it["slots"] + [{"name": "ref_file", "required": False, "ask": ""}]
    # 必填无默认值未映射 → 不通过；文件入参映射到文本槽位 → 不通过
    v = admin.post("/api/admin/intents/M.DESIGN.MAIN/validate", json={"slots": slot_list, "tools": base + [
        {"tool": "wf_img", "param_map": {"image": {"source": "槽位", "value": "platform"}}}]}).json()
    msgs = "\n".join(v["problems"])
    assert "必填入参「prompt」需配置映射或默认值" in msgs and "「style」" not in msgs
    assert "文件入参「image」只能映射到「文件 / 图片」" in msgs
    v = admin.post("/api/admin/intents/M.DESIGN.MAIN/validate", json={"slots": slot_list, "tools": base + [
        {"tool": "wf_img", "param_map": {"image": {"source": "槽位", "value": "product_image"},
                                         "prompt": {"source": "常量", "value": "白底"}}}]}).json()
    assert not [p for p in v["problems"] if "wf_img" in p], v["problems"]
