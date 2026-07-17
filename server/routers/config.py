"""配置路由。"""

import logging
import time
import threading
from fastapi import APIRouter, HTTPException, Body
from server.config import AppConfig, DEFAULTS
from server.services.embedder import Embedder
from server.services.registry import ServiceRegistry
from server.schemas import UpdateConfigRequest

logger = logging.getLogger(__name__)

# search-status 缓存（5 分钟 TTL，避免频繁调用外网 API）
_search_status_cache: dict | None = None
_search_status_cache_time: float = 0.0
_search_status_cache_ttl: float = 300.0
_search_status_lock = threading.Lock()

router = APIRouter(prefix="/api/v1/config", tags=["config"])


@router.get("")
def get_config():
    cfg = AppConfig()
    return {"code": "OK", "message": "success", "data": cfg.get_all()}


@router.put("")
def update_config(body: dict = Body(...)):
    cfg = AppConfig()
    unknown_keys = [k for k in body if k not in DEFAULTS]
    if unknown_keys:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的配置项: {', '.join(unknown_keys)}",
        )
    for key, value in body.items():
        cfg.set(key, str(value))
    # 配置变更后主动清空所有服务缓存，确保下次请求用新配置重建
    ServiceRegistry.get_singleton().invalidate_all()
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


@router.get("/vector-info")
def get_vector_info():
    """获取向量库信息（维度 + 最近重建时间 + 向量数量）。"""
    import os
    import time
    from server.database import DATA_DIR
    from server.vector.store import get_client

    info = {"dimension": None, "last_reindex": None, "vector_count": 0}

    try:
        client = get_client(str(DATA_DIR / "chroma"))
        col = client.get_collection("knowledge_base")
        info["vector_count"] = col.count()

        if info["vector_count"] > 0:
            result = col.get(limit=1, include=["embeddings"])
            embs = result.get("embeddings")
            if embs is not None and len(embs) > 0:
                info["dimension"] = len(embs[0])
    except Exception:
        logger.exception("获取向量信息失败")
        pass

    try:
        chroma_dir = DATA_DIR / "chroma"
        max_mtime = 0
        for root, dirs, files in os.walk(chroma_dir):
            for f in files:
                fp = os.path.join(root, f)
                mt = os.path.getmtime(fp)
                if mt > max_mtime:
                    max_mtime = mt
        if max_mtime > 0:
            info["last_reindex"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(max_mtime))
    except Exception:
        logger.exception("获取最近重建时间失败")
        pass

    return {"code": "OK", "data": info}


def _test_search_provider(provider: str, api_key: str, max_results: int = 1) -> dict:
    """内部：测试单个搜索引擎连通性。返回 {ok, error?, result_count?, latency_ms?}。"""
    t0 = time.time()
    try:
        if provider == "anysearch":
            from server.services.anysearch import AnySearchClient
            client = AnySearchClient(api_key=api_key, max_results=max_results)
        elif provider == "tavily":
            from server.services.web_search import WebSearchClient
            client = WebSearchClient(api_key=api_key, max_results=max_results)
        else:
            return {"ok": False, "error": f"未知的搜索引擎: {provider}"}

        results = client.search("test")
        elapsed = int((time.time() - t0) * 1000)
        return {"ok": True, "result_count": len(results), "latency_ms": elapsed}
    except Exception as e:
        elapsed = int((time.time() - t0) * 1000)
        return {"ok": False, "error": str(e), "latency_ms": elapsed}


@router.post("/test-search/{provider}")
def test_search(provider: str, body: dict = Body(default={})):
    """测试单个搜索引擎的 API Key 是否有效。provider: anysearch | tavily"""
    cfg = AppConfig()
    config = cfg.get_all()
    api_key = body.get("api_key", "").strip() or config.get(f"{provider}_api_key", "").strip()
    if not api_key:
        return {"code": "OK", "data": {"ok": False, "error": "未配置 API Key"}}
    result = _test_search_provider(provider, api_key)
    return {"code": "OK", "data": result}


@router.get("/search-status")
def search_status():
    """并行检测 AnySearch 和 Tavily 连通性，用于前端判断「联网搜索」复选框是否可用。结果缓存 5 分钟。"""
    global _search_status_cache, _search_status_cache_time
    now = time.time()
    with _search_status_lock:
        if _search_status_cache is not None and (now - _search_status_cache_time) < _search_status_cache_ttl:
            return _search_status_cache

    cfg = AppConfig()
    config = cfg.get_all()

    from concurrent.futures import ThreadPoolExecutor, as_completed

    anysearch_key = config.get("anysearch_api_key", "").strip()
    tavily_key = config.get("tavily_api_key", "").strip()

    anysearch_result = {"configured": bool(anysearch_key), "ok": False}
    tavily_result = {"configured": bool(tavily_key), "ok": False}

    def _test(provider, key):
        if not key:
            return provider, {"configured": False, "ok": False}
        r = _test_search_provider(provider, key)
        return provider, {**r, "configured": True}

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(_test, p, k): p for p, k in [("anysearch", anysearch_key), ("tavily", tavily_key)]}
        for f in as_completed(futures):
            provider, result = f.result()
            if provider == "anysearch":
                anysearch_result = result
            else:
                tavily_result = result

    web_search_available = anysearch_result.get("ok", False) or tavily_result.get("ok", False)

    result = {
        "code": "OK",
        "data": {
            "anysearch": anysearch_result,
            "tavily": tavily_result,
            "web_search_available": web_search_available,
        },
    }
    with _search_status_lock:
        _search_status_cache = result
        _search_status_cache_time = now
    return result
