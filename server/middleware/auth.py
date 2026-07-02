"""API Key 认证中间件 — 前端同源请求放行，外部 API 调用需 key。

使用纯 ASGI 中间件（非 BaseHTTPMiddleware 子类）以确保与 SSE 流式响应兼容。
"""

from starlette.responses import JSONResponse


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
        headers = dict(scope.get("headers", []))
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

        if provided != expected:
            response = JSONResponse(
                status_code=401,
                content={"code": "ERROR", "message": "API key 无效或缺失"},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)
