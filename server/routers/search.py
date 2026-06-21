"""搜索路由 — 混合搜索 API。"""
import logging
from fastapi import APIRouter, HTTPException, Query
from server.database import DATA_DIR
from server.services.registry import ServiceRegistry
from server.config import AppConfig

logger = logging.getLogger("knowledge-base")

router = APIRouter(prefix="/api/v1", tags=["search"])


@router.get("/search")
def search(
    q: str = Query(default="", description="搜索关键词"),
    type: str = Query(default="chunks", description="搜索类型: chunks 或 documents"),
    top_k: int = Query(default=10, ge=1, le=50),
    document_id: str | None = Query(default=None),
):
    if not q.strip():
        raise HTTPException(status_code=400, detail="搜索关键词不能为空")

    svc = ServiceRegistry.get_singleton().get_search_service(DATA_DIR, top_k=top_k)
    config = AppConfig().get_all()

    if type == "documents":
        results = svc.document_search(q.strip(), top_k=top_k, config=config)
    else:
        results = svc.hybrid_search(q.strip(), top_k=top_k, document_id=document_id, config=config)

    return {"code": "OK", "message": "success", "data": results}
