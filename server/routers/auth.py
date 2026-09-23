"""网页登录：验证应用 API Key 后签发 HttpOnly 会话，兼容 iframe 原文预览。"""

import hmac

from fastapi import APIRouter, HTTPException, Request, Response

from server.config import AppConfig
from server.middleware.auth import SESSION_COOKIE, SESSION_MAX_AGE, create_session_token

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/login")
def login(request: Request, response: Response) -> dict:
    expected = AppConfig().get("api_key").strip()
    provided = request.headers.get("x-api-key", "")
    if expected and not hmac.compare_digest(provided.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="API Key 无效")
    if expected:
        response.set_cookie(
            SESSION_COOKIE, create_session_token(expected),
            max_age=SESSION_MAX_AGE, httponly=True, samesite="strict",
            secure=request.url.scheme == "https", path="/api/",
        )
    response.headers["Cache-Control"] = "no-store"
    return {"code": "OK"}
