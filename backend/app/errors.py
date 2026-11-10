"""统一错误格式：{code, message, details, request_id}。"""
import logging
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("intent.errors")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict | None = None,
                 headers: dict | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}
        self.headers = headers or {}

    def body(self, request_id: str | None = None) -> dict[str, Any]:
        b: dict[str, Any] = {"code": self.code, "message": self.message, "details": self.details}
        if request_id:
            b["request_id"] = request_id
        return b


def _d(details, kw) -> dict:
    if isinstance(details, list):
        details = {"errors": details}
    return {**(details or {}), **kw}


def bad_request(msg: str, code: str = "BAD_REQUEST", details: dict | list | None = None, **kw):
    return ApiError(400, code, msg, _d(details, kw))


def not_found(msg: str = "对象不存在", code: str = "NOT_FOUND", details: dict | list | None = None, **kw):
    return ApiError(404, code, msg, _d(details, kw))


def forbidden(msg: str, code: str = "FORBIDDEN", details: dict | list | None = None, **kw):
    return ApiError(403, code, msg, _d(details, kw))


def conflict(msg: str, code: str = "CONFLICT", details: dict | list | None = None, **kw):
    return ApiError(409, code, msg, _d(details, kw))


def unprocessable(msg: str, code: str = "VALIDATION_FAILED", details: dict | list | None = None, **kw):
    return ApiError(422, code, msg, _d(details, kw))


def request_id(request: Request) -> str:
    rid = getattr(request.state, "request_id", None)
    if not rid:
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        request.state.request_id = rid
    return rid


_STATUS_CODES = {400: "BAD_REQUEST", 401: "UNAUTHORIZED", 403: "FORBIDDEN", 404: "NOT_FOUND",
                 405: "METHOD_NOT_ALLOWED", 409: "CONFLICT", 413: "PAYLOAD_TOO_LARGE",
                 422: "VALIDATION_FAILED", 429: "RATE_LIMITED"}


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def _rid(request: Request, call_next):
        rid = request_id(request)
        resp = await call_next(request)
        resp.headers["X-Request-Id"] = rid
        return resp

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):
        return JSONResponse(exc.body(request_id(request)), status_code=exc.status, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        errs = []
        for e in exc.errors():
            loc = ".".join(str(x) for x in e.get("loc", ()) if x not in ("body",))
            errs.append({"field": loc, "message": e.get("msg", "")})
        body = {"code": "BAD_REQUEST", "message": "请求参数格式错误", "details": {"errors": errs},
                "request_id": request_id(request)}
        return JSONResponse(body, status_code=400)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        code = _STATUS_CODES.get(exc.status_code, "ERROR")
        msg = exc.detail if isinstance(exc.detail, str) else code
        if exc.status_code == 404 and msg == "Not Found":
            msg = "接口不存在"
        return JSONResponse({"code": code, "message": msg, "details": {}, "request_id": request_id(request)},
                            status_code=exc.status_code, headers=getattr(exc, "headers", None))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.exception("unhandled error")
        return JSONResponse({"code": "INTERNAL", "message": "服务器内部错误", "details": {},
                             "request_id": request_id(request)}, status_code=500)
