import json

from fastapi import Request

from ...errors import bad_request


async def json_body(request: Request) -> dict:
    raw = await request.body()
    if not raw:
        return {}
    try:
        d = json.loads(raw)
    except ValueError:
        raise bad_request("请求体不是合法 JSON")
    if not isinstance(d, dict):
        raise bad_request("请求体应为对象")
    return d
