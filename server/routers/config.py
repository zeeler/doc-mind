"""配置路由。"""

from fastapi import APIRouter, HTTPException
from server.config import AppConfig, DEFAULTS
from server.services.embedder import Embedder

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


@router.get("/embedding-test")
def test_embedding():
    """测试独立 embedding 模型连接。"""
    cfg = AppConfig()
    config = cfg.get_all()

    if config.get("embedding_enabled") != "true":
        raise HTTPException(status_code=400, detail="未启用独立 embedding 模型")
    if not config.get("embedding_model", "").strip():
        raise HTTPException(status_code=400, detail="未配置 embedding 模型名称")
    if not config.get("embedding_api_base", "").strip():
        raise HTTPException(status_code=400, detail="未配置 embedding API Base URL")

    try:
        embedder = Embedder(config)
        # 用简短的测试文本生成向量
        vectors = embedder.embed(["test"])
        if not vectors or not vectors[0]:
            raise HTTPException(status_code=500, detail="Embedding API 返回空结果")
        dim = len(vectors[0])
        return {
            "code": "OK",
            "message": "success",
            "data": {
                "ok": True,
                "model": config.get("embedding_model"),
                "dimension": dim,
                "sample": [round(v, 6) for v in vectors[0][:5]],
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Embedding 连接测试失败: {str(e)}")


@router.get("/reranker-test")
def test_reranker():
    """测试 Reranker 模型连接。"""
    from server.config import has_reranker_model

    cfg = AppConfig()
    config = cfg.get_all()

    if not has_reranker_model(config):
        raise HTTPException(status_code=400, detail="未启用 Reranker 模型或配置不完整")

    try:
        from server.services.reranker import Reranker
        reranker = Reranker(config)
        # 用两条测试文档验证 API 连通性
        results = reranker.rerank(
            query="测试查询",
            documents=["这是第一篇测试文档", "这是第二篇测试文档"],
            top_k=2,
        )
        return {
            "code": "OK",
            "message": "success",
            "data": {
                "ok": True,
                "model": config.get("reranker_model"),
                "results": results,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Reranker 连接测试失败: {str(e)}")
