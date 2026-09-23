"""API Key 认证中间件 — API 使用密钥，网页使用有时效的签名会话 Cookie。

使用纯 ASGI 中间件（非 BaseHTTPMiddleware 子类）以确保与 SSE 流式响应兼容。
"""

import hashlib
import hmac
import time

from starlette.requests import Request
from starlette.responses import JSONResponse

SESSION_COOKIE = "kb_session"
SESSION_MAX_AGE = 12 * 60 * 60


def create_session_token(api_key: str) -> str:
    """用应用密钥签名会话；Cookie 不包含原始 API Key，换密钥后立即失效。"""
    issued = str(int(time.time()))
    signature = hmac.new(api_key.encode(), f"kb-session:{issued}".encode(), hashlib.sha256).hexdigest()
    return f"{issued}.{signature}"


def valid_session_token(token: str, api_key: str) -> bool:
    try:
        issued, signature = token.split(".", 1)
        if len(issued) > 12 or not issued.isascii() or not issued.isdecimal() or len(signature) != 64:
            return False
        age = time.time() - int(issued)
        if not 0 <= age < SESSION_MAX_AGE:
            return False
        expected = hmac.new(api_key.encode(), f"kb-session:{issued}".encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature.encode(), expected.encode())
    except (ValueError, TypeError):
        return False


class AuthMiddleware:
    """纯 ASGI 中间件 — 检查 API key，与 EventSourceResponse 兼容。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        skip_prefixes = (
            "/docs", "/openapi.json", "/redoc",
            "/favicon.ico", "/static",
        )
        if path in ("/api/v1/health",) or any(path.startswith(p) for p in skip_prefixes):
            await self.app(scope, receive, send)
            return

        if not path.startswith("/api/"):
            await self.app(scope, receive, send)
            return

        from server.config import AppConfig
        cfg = AppConfig().get_all()
        expected = cfg.get("api_key", "").strip()

        if not expected:
            # 未配置 API key，放行所有请求（保持向后兼容）
            await self.app(scope, receive, send)
            return

        # 从请求头或查询参数提取 API key
        provided = ""
        for key, val in scope.get("headers", []):
            if key.lower() == b"x-api-key":
                provided = val.decode("utf-8", errors="ignore")
                break
        if not provided:
            query_string = scope.get("query_string", b"").decode("utf-8", errors="ignore")
            import urllib.parse
            params = urllib.parse.parse_qs(query_string)
            api_keys = params.get("api_key", [])
            if api_keys:
                provided = api_keys[0]

        authorized = hmac.compare_digest(provided.encode(), expected.encode())
        if not provided:
            request = Request(scope)
            authorized = valid_session_token(request.cookies.get(SESSION_COOKIE, ""), expected)
            # Cookie 会被浏览器自动发送，必须限制来源，不能依赖 CORS 阻止执行写操作。
            origin = request.headers.get("origin")
            same_origin = f"{request.url.scheme}://{request.url.netloc}"
            if authorized and origin and origin != same_origin:
                response = JSONResponse(status_code=403, content={"code": "ERROR", "message": "不允许跨来源使用浏览器会话"})
                await response(scope, receive, send)
                return

        if not authorized:
            response = JSONResponse(
                status_code=401,
                content={"code": "ERROR", "message": "API key 无效或缺失"},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)
