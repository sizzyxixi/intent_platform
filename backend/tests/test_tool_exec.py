"""工具中心测试调用：真实执行（仅管理员，记审计）。"""
import os
import stat

import pytest

from app.config import get_settings

from conftest import login


@pytest.fixture()
def fake_bin(tmp_path, monkeypatch):
    d = tmp_path / "bin"
    d.mkdir()
    f = d / "fakecli"
    f.write_text("#!/bin/sh\n"
                 "if [ \"$1\" = \"auth\" ]; then echo '{\"ok\":false,\"error\":{\"code\":\"AUTH_ENV_MISSING\",\"message\":\"no token\"}}'; exit 1; fi\n"
                 "if [ \"$1\" = \"slow\" ]; then sleep 5; fi\n"
                 "printf '{\"ok\":true,\"args\":\"%s\"}' \"$*\"; echo warn >&2\n")
    f.chmod(f.stat().st_mode | stat.S_IEXEC)
    s = get_settings()
    monkeypatch.setattr(s, "tool_test_extra_path", str(d))
    return d


def _cli(admin, name, tpl, confirm=False, params=None):
    body = {"name": name, "type": "cli", "display_name": name, "description": "测试", "owner": "我",
            "params": params if params is not None else [{"name": "sku", "type": "string", "required": True}],
            "config": {"bin": tpl.split()[0], "command_template": tpl, "confirm_required": confirm,
                       "login_command": "fakecli auth login", "auth_env": "XCLAW_UMC_TOKEN"}}
    r = admin.post("/api/admin/tools", json=body)
    assert r.status_code == 201, r.text


def test_cli_executes_and_audits(admin, fake_bin):
    _cli(admin, "fk_ok", "fakecli get --sku {sku} --json")
    r = admin.post("/api/admin/tools/fk_ok/test", json={"values": {"sku": "A 1;rm -rf /"}}).json()
    assert r["executed"] is True and r["ok"] is True and r["exit_code"] == 0
    assert r["stdout_json"]["args"] == "get --sku A 1;rm -rf / --json"  # 参数数组，不经 shell
    assert "warn" in r["stderr"] and r["duration_ms"] >= 0
    a = admin.get("/api/admin/audit?entity_type=tool").json()["items"][0]
    assert a["action"] == "tool.test" and "成功" in a["summary"]


def test_cli_auth_hint_timeout_and_missing(admin, fake_bin, monkeypatch):
    _cli(admin, "fk_auth", "fakecli auth --sku {sku}")
    r = admin.post("/api/admin/tools/fk_auth/test", json={"values": {"sku": "x"}}).json()
    assert r["ok"] is False and r["error_code"] == "AUTH_ENV_MISSING" and "fakecli auth login" in r["hint"]
    assert "XCLAW_UMC_TOKEN" in r["hint"]
    monkeypatch.setattr(get_settings(), "tool_test_timeout_seconds", 1)
    _cli(admin, "fk_slow", "fakecli slow --sku {sku}")
    r = admin.post("/api/admin/tools/fk_slow/test", json={"values": {"sku": "x"}}).json()
    assert r["ok"] is False and r["error_code"] == "CLI_TIMEOUT"
    _cli(admin, "fk_none", "no-such-cli-xyz get --sku {sku}")
    r = admin.post("/api/admin/tools/fk_none/test", json={"values": {"sku": "x"}}).json()
    assert r["error_code"] == "CLI_NOT_INSTALLED" and "hint" in r


def test_confirm_required(admin, fake_bin):
    _cli(admin, "fk_w", "fakecli set --sku {sku}", confirm=True)
    r = admin.post("/api/admin/tools/fk_w/test", json={"values": {"sku": "x"}})
    assert r.status_code == 422 and r.json()["code"] == "CONFIRM_REQUIRED"
    r = admin.post("/api/admin/tools/fk_w/test", json={"values": {"sku": "x"}, "confirm": True}).json()
    assert r["ok"] is True


def test_builtin_http_and_mcp(admin):
    keys = len(admin.get("/api/admin/api-keys").json())
    r = admin.post("/api/admin/tools/pf_read_intents/test", json={"values": {}, "space": "cs"}).json()
    assert r["ok"] is True and r["status"] == 200, r
    assert r["response_json"]
    r = admin.post("/api/admin/tools/pf_list_tools/test", json={"values": {}}).json()
    assert r["ok"] is True, r
    assert len(admin.get("/api/admin/api-keys").json()) == keys  # 临时 Key 已删除


def test_viewer_forbidden_and_dry_run(admin):
    admin.post("/api/admin/users", json={"username": "vt", "display_name": "vt", "password": "password123", "role": "viewer"})
    admin.cookies.clear()
    login(admin, "vt", "password123")
    r = admin.post("/api/admin/tools/pf_list_tools/test", json={"values": {}})
    assert r.status_code == 403
    r = admin.post("/api/admin/tools/pf_list_tools/test", json={"values": {}, "dry_run": True})
    assert r.status_code == 200 and r.json()["executed"] is False


def test_http_unreachable(admin):
    body = {"name": "dead_api", "type": "http", "display_name": "dead", "description": "x", "owner": "我", "params": [],
            "config": {"method": "GET", "url": "http://127.0.0.1:9/x", "auth": "无"}}
    assert admin.post("/api/admin/tools", json=body).status_code == 201
    r = admin.post("/api/admin/tools/dead_api/test", json={"values": {}}).json()
    assert r["executed"] is True and r["ok"] is False and r["error_code"] == "HTTP_ERROR"


def test_builtin_mcp_skill_and_write_confirm(admin):
    r = admin.post("/api/admin/tools/intent_platform.search_knowledge/test", json={"values": {"query": "违禁词"}}).json()
    assert r["executed"] is True and r["ok"] is True, r
    assert "result" in r["response_json"]
    r = admin.post("/api/admin/tools/gen_image_skill/test", json={"values": {}}).json()
    assert r["executed"] is False and r["skill"]["skill_md"] and r["skill"]["files"]
    r = admin.post("/api/admin/tools/pf_write_slots/test", json={"values": {}, "space": "cs"})
    assert r.status_code == 422
