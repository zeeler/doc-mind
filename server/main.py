"""知识库应用入口。"""

import sys
import logging
from pathlib import Path

# 确保项目根目录在 sys.path 中，支持直接 python server/main.py 启动
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from server.database import init_db, get_engine
from server.models.base import Base
from server.models.document import Document, DocumentChunk  # noqa: F401
from server.models.conversation import Conversation, Message  # noqa: F401
from server.models.job import Job  # noqa: F401
from server.config import AppConfigModel  # noqa: F401
from server.routers.documents import router as documents_router
from server.routers.conversations import router as conversations_router
from server.routers.chat import router as chat_router
from server.routers.config import router as config_router

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("knowledge-base")

app = FastAPI(title="知识库", version="0.1.0")

app.include_router(documents_router)
app.include_router(conversations_router)
app.include_router(chat_router)
app.include_router(config_router)


@app.get("/api/v1/health")
def health_check():
    try:
        engine = get_engine()
        engine.connect().close()
        db_ok = True
    except Exception:
        db_ok = False
    return {
        "code": "OK",
        "data": {
            "status": "healthy" if db_ok else "degraded",
            "database": "ok" if db_ok else "error",
        },
    }


# 挂载前端
_templates_dir = Path(__file__).parent / "templates"
if _templates_dir.exists():
    app.mount("/", StaticFiles(directory=str(_templates_dir), html=True), name="static")


def startup():
    """在 uvicorn 启动前调用，初始化数据库并执行迁移。"""
    from server.models.document import Document, DocumentChunk  # noqa: F811
    from server.models.conversation import Conversation, Message  # noqa: F811
    from server.config import AppConfigModel  # noqa: F811
    from server.database import init_db
    init_db()
    from server.services.worker import start_workers
    start_workers(num=2)


if __name__ == "__main__":
    import uvicorn
    startup()
    logger.info("SQLite 就绪")
    logger.info("ChromaDB 就绪")
    logger.info("知识库服务已启动: http://localhost:8000")
    uvicorn.run("server.main:app", host="0.0.0.0", port=8000, reload=False, log_level="info")
