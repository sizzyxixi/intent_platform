"""测试用的最小 MCP 服务（仅标准库）。只实现 initialize / tools/list，用于验证平台的「同步工具 / 差异 / 下钻」。

  python mini_mcp_server.py --port 8765            # streamable HTTP：POST /mcp；旧版 SSE：GET /sse + POST /messages
  python mini_mcp_server.py --stdio                # stdio
  环境变量 MINI_MCP_TOKEN=xxx 时 HTTP 需要 Authorization: Bearer xxx
  测试专用：POST /_set {"tools":[...]} 替换工具集；POST /_reset 恢复默认 3 个工具
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import queue
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

DEFAULT_TOOLS = [
    {"name": "lookup_order", "title": "查询订单",
     "description": "按订单号查询订单状态与物流", "annotations": {"readOnlyHint": True},
     "inputSchema": {"type": "object", "properties": {"order_no": {"type": "string", "description": "订单号"}},
                     "required": ["order_no"]}},
    {"name": "cancel_order", "title": "取消订单",
     "description": "取消未发货的订单", "annotations": {"readOnlyHint": False, "destructiveHint": True},
     "inputSchema": {"type": "object", "properties": {"order_no": {"type": "string"},
                                                      "reason": {"type": "string"}}, "required": ["order_no"]}},
    {"name": "search_faq", "title": "搜索常见问题",
     "description": "在帮助中心搜索常见问题", "annotations": {"readOnlyHint": True},
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string"},
                                                      "limit": {"type": "integer"}}, "required": ["query"]}},
]
STATE = {"tools": copy.deepcopy(DEFAULT_TOOLS)}
PAGE = 2  # 每页 2 个，顺带覆盖 nextCursor 分页


def handle(msg: dict) -> dict | None:
    if "id" not in msg:
        return None
    m, i = msg.get("method"), msg["id"]
    if m == "initialize":
        return {"jsonrpc": "2.0", "id": i, "result": {
            "protocolVersion": (msg.get("params") or {}).get("protocolVersion") or "2025-06-18",
            "capabilities": {"tools": {"listChanged": False}}, "serverInfo": {"name": "mini-mcp", "version": "0.1.0"}}}
    if m == "tools/list":
        start = int((msg.get("params") or {}).get("cursor") or 0)
        tools = STATE["tools"]
        res = {"tools": tools[start:start + PAGE]}
        if start + PAGE < len(tools):
            res["nextCursor"] = str(start + PAGE)
        return {"jsonrpc": "2.0", "id": i, "result": res}
    return {"jsonrpc": "2.0", "id": i, "error": {"code": -32601, "message": f"Method not found: {m}"}}


SSE_SESSIONS: dict[str, queue.Queue] = {}


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # 安静
        pass

    def _send(self, code: int, body: bytes = b"", ctype: str = "application/json", extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _authed(self) -> bool:
        tok = os.environ.get("MINI_MCP_TOKEN")
        bad = bool(tok and self.headers.get("Authorization") != f"Bearer {tok}")
        hk = os.environ.get("MINI_MCP_HEADER")  # 形如 secret-key=abc
        if hk:
            k, _, v = hk.partition("=")
            bad = bad or self.headers.get(k) != v
        qk = os.environ.get("MINI_MCP_QUERY")  # 形如 api_key=xyz
        if qk:
            from urllib.parse import parse_qs
            k, _, v = qk.partition("=")
            bad = bad or (parse_qs(urlparse(self.path).query).get(k) or [None])[0] != v
        if bad:
            self._send(401, b'{"error":"unauthorized"}')
            return False
        return True

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_POST(self):  # noqa: N802
        u = urlparse(self.path)
        if u.path == "/_set":
            STATE["tools"] = self._body()["tools"]
            return self._send(200, b'{"ok":true}')
        if u.path == "/_reset":
            STATE["tools"] = copy.deepcopy(DEFAULT_TOOLS)
            return self._send(200, b'{"ok":true}')
        if not self._authed():
            return
        if u.path == "/mcp":
            reply = handle(self._body())
            if reply is None:
                return self._send(202)
            data = json.dumps(reply).encode()
            sid = {"Mcp-Session-Id": self.headers.get("Mcp-Session-Id") or uuid.uuid4().hex}
            if "sse" in parse_qs(u.query):  # 以事件流形式返回
                return self._send(200, b"event: message\ndata: " + data + b"\n\n", "text/event-stream", sid)
            return self._send(200, data, extra=sid)
        if u.path == "/messages":
            q = SSE_SESSIONS.get((parse_qs(u.query).get("session_id") or [""])[0])
            if not q:
                return self._send(404, b'{"error":"no session"}')
            reply = handle(self._body())
            if reply is not None:
                q.put(reply)
            return self._send(202)
        self._send(404)

    def do_GET(self):  # noqa: N802
        if urlparse(self.path).path != "/sse":
            return self._send(404)
        if not self._authed():
            return
        sid = uuid.uuid4().hex
        q: queue.Queue = queue.Queue()
        SSE_SESSIONS[sid] = q
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(f"event: endpoint\ndata: /messages?session_id={sid}\n\n".encode())
            self.wfile.flush()
            while True:
                try:
                    m = q.get(timeout=30)
                except queue.Empty:
                    break
                self.wfile.write(b"event: message\ndata: " + json.dumps(m).encode() + b"\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            SSE_SESSIONS.pop(sid, None)
            self.close_connection = True


def serve(port: int) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def stdio():
    for line in sys.stdin:
        try:
            reply = handle(json.loads(line))
        except ValueError:
            continue
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--stdio", action="store_true")
    a = ap.parse_args()
    if a.stdio:
        stdio()
    else:
        srv = ThreadingHTTPServer(("127.0.0.1", a.port), H)
        srv.daemon_threads = True
        print(f"mini mcp on http://127.0.0.1:{a.port}/mcp", flush=True)
        srv.serve_forever()
