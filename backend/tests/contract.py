"""按 docs/openapi.yaml 校验响应结构（OpenAPI 3.0 → JSON Schema）。"""
import copy
import re
from functools import lru_cache
from pathlib import Path

import jsonschema
import yaml

SPEC_PATH = Path(__file__).resolve().parents[2] / "docs" / "openapi.yaml"


@lru_cache
def spec() -> dict:
    return yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))


def _resolve(node, seen=()):
    if isinstance(node, dict):
        if "$ref" in node:
            ref = node["$ref"]
            if ref in seen:
                return {}
            parts = ref.lstrip("#/").split("/")
            tgt = spec()
            for p in parts:
                tgt = tgt[p]
            return _resolve(copy.deepcopy(tgt), seen + (ref,))
        out = {k: _resolve(v, seen) for k, v in node.items() if k not in ("example", "examples")}
        if out.pop("nullable", False) and "type" in out:
            out["type"] = [out["type"], "null"]
        return out
    if isinstance(node, list):
        return [_resolve(x, seen) for x in node]
    return node


def _match_path(path: str) -> str:
    for p in spec()["paths"]:
        rx = "^" + re.sub(r"\{[^}]+\}", "[^/]+", re.escape(p).replace(r"\{", "{").replace(r"\}", "}")) + "$"
        if re.match(rx, path):
            return p
    raise AssertionError(f"openapi 中没有路径 {path}")


def response_schema(method: str, path: str, status: int) -> dict | None:
    op = spec()["paths"][_match_path(path)][method.lower()]
    resp = op["responses"].get(str(status))
    assert resp is not None, f"{method} {path} 未在 openapi 中声明状态码 {status}"
    resp = _resolve(resp)
    content = resp.get("content") or {}
    js = content.get("application/json")
    return js.get("schema") if js else None


def check(resp, method: str | None = None, path: str | None = None):
    """断言响应状态码在 openapi 中声明，且 JSON 结构符合 schema。返回 json。"""
    req = resp.request
    method = method or req.method
    path = path or req.url.path
    sch = response_schema(method, path, resp.status_code)
    if resp.status_code in (204, 304):
        return None
    body = resp.json()
    if sch:
        jsonschema.validate(body, sch)
    return body
