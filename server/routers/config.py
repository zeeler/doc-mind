"""配置路由。"""

from fastapi import APIRouter, HTTPException
from server.config import AppConfig, DEFAULTS

router = APIRouter(prefix="/api/v1/config", tags=["config"])


@router.get("")
def get_config():
    cfg = AppConfig()
    return {"code": "OK", "message": "success", "data": cfg.get_all()}


@router.put("")
def update_config(body: dict):
    cfg = AppConfig()
    unknown_keys = [k for k in body if k not in DEFAULTS]
    if unknown_keys:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的配置项: {', '.join(unknown_keys)}",
        )
    for key, value in body.items():
        cfg.set(key, str(value))
    return {"code": "OK", "message": "success", "data": cfg.get_all()}


@router.get("/models")
def get_models():
    cfg = AppConfig()
    config = cfg.get_all()
    models = {"chat": [], "embedding": []}
    provider = config.get("llm_provider", "mlx")
    if provider == "mlx":
        models["chat"].append({"id": config.get("mlx_chat_model", ""), "name": config.get("mlx_chat_model", "未配置"), "source": "mlx"})
        models["embedding"].append({"id": config.get("mlx_embedding_model", ""), "name": config.get("mlx_embedding_model", "未配置"), "source": "mlx"})
    elif provider == "openai":
        models["chat"].append({"id": config.get("openai_chat_model", ""), "name": config.get("openai_chat_model", ""), "source": "openai"})
        models["embedding"].append({"id": config.get("openai_embedding_model", ""), "name": config.get("openai_embedding_model", ""), "source": "openai"})
    elif provider == "claude":
        models["chat"].append({"id": config.get("claude_chat_model", ""), "name": config.get("claude_chat_model", ""), "source": "claude"})
    elif provider == "custom":
        api_type = config.get("custom_api_type", "openai")
        label = f"自定义 ({'Anthropic格式' if api_type == 'anthropic' else 'OpenAI格式'})"
        models["chat"].append({"id": config.get("custom_chat_model", ""), "name": config.get("custom_chat_model", "未配置"), "source": label})
        models["embedding"].append({"id": config.get("custom_embedding_model", ""), "name": config.get("custom_embedding_model", "未配置"), "source": label})
    return {"code": "OK", "message": "success", "data": {"models": models, "provider": provider}}
