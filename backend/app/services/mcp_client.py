"""最小 MCP 客户端：只做 initialize + tools/list，用于「同步工具」。平台不调用 tools/call（执行在 Bot）。

支持三种传输：
- streamable_http（MCP 2025-03-26 起）：POST JSON-RPC，响应可能是 application/json 或 text/event-stream；带 Mcp-Session-Id。
- sse（2024-11-05 旧版）：GET 建立事件流，收到 endpoint 事件后向该地址 POST，响应从事件流返回。
- stdio：启动本地进程，按行收发 JSON-RPC。默认关闭（IP_MCP_STDIO_ENABLED），因为这等于允许在平台服务器上执行命令。
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
from urllib.parse import urljoin

import httpx

from ..errors import ApiError

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "intent-platform", "version": "0.6.0"}
MAX_PAGES = 50


class McpError(ApiError):
    def __init__(self, message: str, details: dict | None = None):
        super().__init__(502, "MCP_SYNC_FAILED", message, details or {})


def _init_req(i: int) -> dict:
    return {"jsonrpc": "2.0", "id": i, "method": "initialize",
            "params": {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": CLIENT_INFO}}


def _check(msg: dict, what: str) -> dict:
    if "error" in msg:
        e = msg["error"] or {}
        raise McpError(f"{what} 返回错误：{e.get('message') or e}", {"rpc_error": e})
    if not isinstance(msg.get("result"), dict):
        raise McpError(f"{what} 返回格式不正确")
    return msg["result"]


def _sse_events(lines):
    """把 SSE 行流解析成 (event, data) 序列。"""
    event, data = "message", []
    for raw in lines:
        line = raw.rstrip("\r")
        if line == "":
            if data:
                yield event, "\n".join(data)
            event, data = "message", []
            continue
        if line.startswith(":"):
            continue
        k, _, v = line.partition(":")
        v = v[1:] if v.startswith(" ") else v
        if k == "event":
            event = v
        elif k == "data":
            data.append(v)
    if data:
        yield event, "\n".join(data)


def _list_all(call) -> list[dict]:
    tools, cursor = [], None
    for _ in range(MAX_PAGES):
        params = {"cursor": cursor} if cursor else {}
        res = call("tools/list", params)
        tools += res.get("tools") or []
        cursor = res.get("nextCursor")
        if not cursor:
            return tools
    raise McpError(f"tools/list 分页超过 {MAX_PAGES} 页")


# ---------------- streamable HTTP ----------------
def _http_fetch(url: str, headers: dict, timeout: float) -> tuple[dict, list[dict]]:
    sess: dict = {}
    n = [0]
    with httpx.Client(timeout=timeout, follow_redirects=False) as c:
        def post(body: dict) -> dict | None:
            h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", **headers}
            if sess.get("id"):
                h["Mcp-Session-Id"] = sess["id"]
            if sess.get("ver"):
                h["MCP-Protocol-Version"] = sess["ver"]
            with c.stream("POST", url, json=body, headers=h) as r:
                if r.headers.get("mcp-session-id"):
                    sess["id"] = r.headers["mcp-session-id"]
                if "id" not in body:
                    return None
                if r.status_code >= 400:
                    r.read()
                    raise McpError(f"HTTP {r.status_code}：{r.text[:200]}")
                ct = r.headers.get("content-type", "")
                if "text/event-stream" in ct:
                    for _ev, data in _sse_events(r.iter_lines()):
                        try:
                            m = json.loads(data)
                        except ValueError:
                            continue
                        if m.get("id") == body["id"]:
                            return m
                    raise McpError("事件流结束前没有收到响应")
                r.read()
                try:
                    m = r.json()
                except ValueError:
                    raise McpError(f"响应不是 JSON（Content-Type: {ct or '无'}）")
                if isinstance(m, list):
                    m = next((x for x in m if x.get("id") == body["id"]), {})
                return m

        def call(method: str, params: dict) -> dict:
            n[0] += 1
            return _check(post({"jsonrpc": "2.0", "id": n[0], "method": method, "params": params}), method)

        n[0] += 1
        init = _check(post(_init_req(n[0])), "initialize")
        sess["ver"] = init.get("protocolVersion") or PROTOCOL_VERSION
        post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return init, _list_all(call)


# ---------------- 旧版 HTTP + SSE ----------------
def _sse_fetch(url: str, headers: dict, timeout: float) -> tuple[dict, list[dict]]:
    with httpx.Client(timeout=timeout, follow_redirects=False) as c:
        with c.stream("GET", url, headers={"Accept": "text/event-stream", **headers}) as stream:
            if stream.status_code >= 400:
                stream.read()
                raise McpError(f"HTTP {stream.status_code}：{stream.text[:200]}")
            events = _sse_events(stream.iter_lines())
            endpoint = None
            for ev, data in events:
                if ev == "endpoint":
                    endpoint = urljoin(url, data.strip())
                    break
            if not endpoint:
                raise McpError("SSE 流里没有收到 endpoint 事件")
            n = [0]

            def call(method: str, params: dict) -> dict:
                n[0] += 1
                body = {"jsonrpc": "2.0", "id": n[0], "method": method, "params": params}
                r = c.post(endpoint, json=body, headers={"Content-Type": "application/json", **headers})
                if r.status_code >= 400:
                    raise McpError(f"HTTP {r.status_code}：{r.text[:200]}")
                for ev, data in events:
                    if ev != "message":
                        continue
                    try:
                        m = json.loads(data)
                    except ValueError:
                        continue
                    if m.get("id") == body["id"]:
                        return _check(m, method)
                raise McpError("SSE 流结束前没有收到响应")

            init = call("initialize", _init_req(0)["params"])
            c.post(endpoint, json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                   headers={"Content-Type": "application/json", **headers})
            return init, _list_all(call)


# ---------------- stdio ----------------
def _stdio_fetch(command: str, args: list[str], env: dict, timeout: float) -> tuple[dict, list[dict]]:
    try:
        p = subprocess.Popen([command, *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             env={**os.environ, **env}, text=True, bufsize=1)
    except OSError as e:
        raise McpError(f"无法启动命令 {command}：{e}")
    q: queue.Queue = queue.Queue()
    threading.Thread(target=lambda: [q.put(x) for x in p.stdout] + [q.put(None)], daemon=True).start()
    n = [0]

    def send(body: dict) -> None:
        p.stdin.write(json.dumps(body) + "\n")
        p.stdin.flush()

    def call(method: str, params: dict) -> dict:
        n[0] += 1
        send({"jsonrpc": "2.0", "id": n[0], "method": method, "params": params})
        while True:
            try:
                line = q.get(timeout=timeout)
            except queue.Empty:
                raise McpError(f"{method} 超时（{timeout:.0f} 秒）")
            if line is None:
                raise McpError(f"进程已退出：{(p.stderr.read() or '')[:200]}")
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if m.get("id") == n[0]:
                return _check(m, method)

    try:
        init = call("initialize", _init_req(0)["params"])
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return init, _list_all(call)
    finally:
        try:
            p.stdin.close()
            p.terminate()
            p.wait(timeout=3)
        except Exception:  # noqa: BLE001
            p.kill()


def fetch_tools(transport: str, *, url: str | None = None, headers: dict | None = None, command: str | None = None,
                args: list[str] | None = None, env: dict | None = None, timeout: float = 15) -> tuple[dict, list[dict]]:
    """返回 (initialize 结果, tools 列表)。失败抛 McpError（502 MCP_SYNC_FAILED）。"""
    try:
        if transport == "streamable_http":
            return _http_fetch(url or "", headers or {}, timeout)
        if transport == "sse":
            return _sse_fetch(url or "", headers or {}, timeout)
        if transport == "stdio":
            return _stdio_fetch(command or "", args or [], env or {}, timeout)
    except httpx.HTTPError as e:
        raise McpError(f"连接 MCP 服务失败：{type(e).__name__} {e}")
    raise McpError(f"不支持的传输方式 {transport}")
