"""API Key 认证中间件 — 前端同源请求放行，外部 API 调用需 key。"""

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


class AuthMiddleware(BaseHTTPMiddleware):
    """检查 API key（如配置了 api_key），放行静态文件和前端页面。"""

    async def dispatch(self, request: Request, call_next):
        # 始终放行静态资源、前端页面、健康检查和 OpenAPI 文档
        path = request.url.path
        skip_prefixes = (
            "/docs", "/openapi.json", "/redoc",
            "/favicon.ico", "/static",
        )
        if path in ("/api/v1/health",) or any(path.startswith(p) for p in skip_prefixes):
            return await call_next(request)

        # 非 API 路径（前端页面）放行
        if not path.startswith("/api/"):
            return await call_next(request)

        # 检查 API key
        from server.config import AppConfig
        cfg = AppConfig().get_all()
        expected = cfg.get("api_key", "").strip()

        if not expected:
            # 未配置 API key，放行所有请求（保持向后兼容）
            return await call_next(request)

        provided = request.headers.get("X-API-Key", "") or request.query_params.get("api_key", "")
        if provided != expected:
            return JSONResponse(
                status_code=401,
                content={"code": "ERROR", "message": "API key 无效或缺失"},
            )

        return await call_next(request)
