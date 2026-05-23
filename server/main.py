from fastapi import FastAPI

app = FastAPI(title="知识库", version="0.1.0")

from server.routers.documents import router as documents_router
app.include_router(documents_router)

from server.routers.conversations import router as conversations_router
app.include_router(conversations_router)

from server.routers.chat import router as chat_router
app.include_router(chat_router)

from server.routers.config import router as config_router
app.include_router(config_router)
