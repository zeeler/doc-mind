"""配置路由。"""

from fastapi import APIRouter
from server.config import AppConfig

router = APIRouter(prefix="/api/v1/config", tags=["config"])


@router.get("")
def get_config():
    cfg = AppConfig()
    return {"code": "OK", "message": "success", "data": cfg.get_all()}


@router.put("")
def update_config(body: dict):
    cfg = AppConfig()
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
        models["chat"].append({"id": config.get("custom_chat_model", ""), "name": config.get("custom_chat_model", "未配置"), "source": "custom"})
        models["embedding"].append({"id": config.get("custom_embedding_model", ""), "name": config.get("custom_embedding_model", "未配置"), "source": "custom"})
    return {"code": "OK", "message": "success", "data": {"models": models, "provider": provider}}
