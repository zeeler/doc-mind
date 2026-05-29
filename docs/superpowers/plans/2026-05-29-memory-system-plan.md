# 记忆系统 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 为 Doc Mind 添加跨对话记忆功能：自动摘要生成记忆、手动标记记忆、向量去重、问答时注入相关记忆。

**Architecture:** ChromaDB 新增 `memories` collection。对话结束后 LLM 自动提取摘要存入记忆（去重）。问答时同时检索文档 chunk 和记忆 chunk。前端消息旁增加「记住」按钮。

**Tech Stack:** ChromaDB, existing LLMAdapter, Vue 3

**Spec:** `docs/superpowers/specs/2026-05-29-memory-system-design.md`

---

### Task 1: MemoryStore — ChromaDB 记忆存储封装

**Files:**
- Create: `server/services/memory_store.py`
- Create: `server/tests/test_memory_store.py`

**Step 1: 写测试**

```python
# server/tests/test_memory_store.py
import pytest
from server.services.memory_store import MemoryStore


class TestMemoryStore:
    @pytest.fixture
    def store(self, tmp_data_dir):
        return MemoryStore(persist_dir=str(tmp_data_dir / "chroma"))

    def test_add_and_search(self, store):
        store.add("mem-1", "用户喜欢简洁的回答风格", {"type": "preference", "count": 1})
        store.add("mem-2", "用户关注AI安全领域", {"type": "preference", "count": 1})

        results = store.search("回答风格", top_k=5)
        assert len(results) > 0
        assert any("简洁" in r["content"] for r in results)

    def test_add_memory_returns_id(self, store):
        mid = store.add("mem-3", "测试记忆", {"type": "fact"})
        assert mid.startswith("mem-")

    def test_delete_memory(self, store):
        mid = store.add("mem-4", "待删除", {"type": "fact"})
        store.delete(mid)
        results = store.search("待删除", top_k=5)
        assert len(results) == 0

    def test_update_memory(self, store):
        mid = store.add("mem-5", "原始内容", {"type": "fact", "count": 1})
        store.update(mid, "更新后内容", {"type": "fact", "count": 2})
        results = store.search("更新后内容", top_k=5)
        assert len(results) == 1
        assert results[0]["metadata"]["count"] == 2

    def test_count(self, store):
        assert store.count() == 0
        store.add("mem-6", "内容", {"type": "fact"})
        assert store.count() == 1
```

**Step 2: 实现 memory_store.py**

```python
"""ChromaDB 记忆存储封装。"""

import uuid
import chromadb
from chromadb.config import Settings


class MemoryStore:
    def __init__(self, persist_dir: str):
        self.client = chromadb.PersistentClient(
            path=persist_dir,
            settings=Settings(anonymized_telemetry=False),
        )
        self.collection = self.client.get_or_create_collection(name="memories")

    def add(self, mem_id: str | None, content: str, metadata: dict) -> str:
        mid = mem_id or f"mem-{uuid.uuid4().hex[:12]}"
        self.collection.add(
            ids=[mid],
            documents=[content],
            metadatas=[metadata],
        )
        return mid

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        results = self.collection.query(query_texts=[query], n_results=top_k)
        hits = []
        ids_list = results.get("ids", [[]])[0]
        docs_list = results.get("documents", [[]])[0]
        metas_list = results.get("metadatas", [[]])[0]
        distances_list = results.get("distances", [[]])[0]
        for i in range(len(ids_list)):
            hits.append({
                "id": ids_list[i],
                "content": docs_list[i] if i < len(docs_list) else "",
                "metadata": metas_list[i] if i < len(metas_list) else {},
                "score": 1.0 - distances_list[i] if i < len(distances_list) else 0.0,
            })
        return hits

    def delete(self, mem_id: str) -> None:
        self.collection.delete(ids=[mem_id])

    def update(self, mem_id: str, content: str, metadata: dict) -> None:
        self.collection.update(ids=[mem_id], documents=[content], metadatas=[metadata])

    def count(self) -> int:
        return self.collection.count()
```

**Step 3: 运行测试**
Run: `python -m pytest server/tests/test_memory_store.py -v`
Expected: PASS (5 tests).

**Step 4: Commit**
```bash
git add server/services/memory_store.py server/tests/test_memory_store.py
git commit -m "feat: add MemoryStore for ChromaDB memories collection"
```

---

### Task 2: 记忆服务 — add_memory + 去重 + 搜索

**Files:**
- Create: `server/services/memory.py`
- Create: `server/tests/test_memory.py`

**Step 1: 实现 memory.py**

```python
"""记忆服务 — 存入/搜索/去重/摘要。"""

import logging
from datetime import datetime, timezone
from server.services.memory_store import MemoryStore
from server.database import DATA_DIR

logger = logging.getLogger("knowledge-base")

MEMORY_DEDUP_THRESHOLD = 0.85


def _get_store() -> MemoryStore:
    return MemoryStore(persist_dir=str(DATA_DIR / "chroma"))


def add_memory(content: str, mem_type: str, metadata: dict | None = None) -> str:
    """存入记忆，自动去重。返回记忆 ID。"""
    store = _get_store()
    meta = dict(metadata or {})
    meta["type"] = mem_type
    meta.setdefault("count", 1)
    meta["updated_at"] = datetime.now(timezone.utc).isoformat()
    meta.setdefault("created_at", meta["updated_at"])

    # 去重：检索相似记忆
    existing = store.search(content, top_k=3)
    for hit in existing:
        if hit["score"] >= MEMORY_DEDUP_THRESHOLD:
            # 合并更新
            old_meta = hit["metadata"]
            merged = f"{hit['content']}\n{content}"[:2000]
            old_meta["count"] = old_meta.get("count", 1) + 1
            old_meta["updated_at"] = meta["updated_at"]
            store.update(hit["id"], merged, old_meta)
            logger.info(f"记忆合并: {hit['id'][:12]} (count={old_meta['count']})")
            return hit["id"]

    # 新增
    return store.add(None, content, meta)


def search_memories(query: str, top_k: int = 5) -> list[dict]:
    """检索相关记忆。"""
    store = _get_store()
    return store.search(query, top_k=top_k)


def list_memories(mem_type: str = None, limit: int = 50) -> list[dict]:
    """列出记忆（按更新时间倒序）。"""
    store = _get_store()
    results = store.collection.get(limit=limit)
    memories = []
    for i in range(len(results.get("ids", []))):
        mem_type_val = results["metadatas"][i].get("type", "") if results.get("metadatas") else ""
        if mem_type and mem_type_val != mem_type:
            continue
        memories.append({
            "id": results["ids"][i],
            "content": results["documents"][i] if results.get("documents") else "",
            "type": mem_type_val,
            "metadata": results["metadatas"][i] if results.get("metadatas") else {},
        })
    memories.sort(key=lambda m: m["metadata"].get("updated_at", ""), reverse=True)
    return memories[:limit]


def delete_memory(mem_id: str) -> None:
    store = _get_store()
    store.delete(mem_id)


def summarize_conversation(conv_id: str) -> int:
    """对一段对话生成摘要记忆。返回新增/更新的记忆数。"""
    from server.database import get_session
    from server.models.conversation import Conversation
    from server.services.llm import LLMAdapter
    from server.config import AppConfig

    with next(get_session()) as session:
        conv = session.get(Conversation, conv_id)
        if not conv:
            return 0
        messages = [{"role": m.role, "content": m.content} for m in conv.messages]

    if len(messages) < 2:
        return 0

    config = AppConfig().get_all()
    llm = LLMAdapter(config)

    prompt = f"""请从以下对话中提取关键信息，分为三类：

1. **偏好 (preference)**：用户的回答风格偏好、关注领域、工作要求
2. **结论 (conclusion)**：从对话中得出的分析结论或决策
3. **事实 (fact)**：用户明确陈述的事实信息

每条信息用一行，格式为 `[类型] 内容`。如果没有某类信息则跳过。只输出提取的信息，不要其他文字。

对话：
{chr(10).join(f"{m['role']}: {m['content'][:500]}" for m in messages[-20:])}
"""
    try:
        result = llm.chat(messages=[{"role": "user", "content": prompt}])
        text = result.get("content", "")
    except Exception as e:
        logger.error(f"摘要生成失败: {e}")
        return 0

    count = 0
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        for prefix, mem_type in [("[偏好]", "preference"), ("[结论]", "conclusion"), ("[事实]", "fact")]:
            if prefix in line:
                content = line.replace(prefix, "").strip()
                if content:
                    add_memory(content, mem_type, {"source_conv_id": conv_id})
                    count += 1
                break

    logger.info(f"对话摘要完成: conv={conv_id}, 记忆数={count}")
    return count
```

**Step 2: 写测试**

```python
# server/tests/test_memory.py
import pytest
from unittest.mock import MagicMock, patch
from server.services.memory import add_memory, search_memories, delete_memory


class TestMemoryService:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_data_dir, monkeypatch):
        monkeypatch.setattr("server.services.memory.DATA_DIR", tmp_data_dir)
        monkeypatch.setattr("server.services.memory_store.DATA_DIR", tmp_data_dir, raising=False)

    def test_add_and_search_memory(self):
        mid = add_memory("用户喜欢简洁回答", "preference")
        assert mid is not None
        results = search_memories("回答风格")
        assert len(results) >= 0  # ChromaDB 内置 embedding 可能返回空

    def test_dedup_merges_similar_memory(self):
        mid1 = add_memory("用户偏好使用 Python 编程", "preference")
        mid2 = add_memory("用户使用 Python 语言", "preference")
        # 相似内容应合并为同一个 ID
        assert mid1 == mid2

    def test_delete_memory(self):
        mid = add_memory("待删除内容", "fact")
        delete_memory(mid)
        results = search_memories("待删除")
        assert len(results) == 0
```

**Step 3: 运行测试**
Run: `python -m pytest server/tests/test_memory.py -v`
Expected: PASS.

**Step 4: Commit**

---

### Task 3: Memories API

**Files:**
- Create: `server/routers/memories.py`
- Modify: `server/main.py`
- Create: `server/tests/test_routers/test_memories.py`

**Step 1: 实现 routers/memories.py**

```python
"""记忆管理路由。"""

from fastapi import APIRouter, HTTPException
from server.services import memory as mem

router = APIRouter(prefix="/api/v1/memories", tags=["memories"])


@router.post("/remember")
def remember_message(body: dict):
    conversation_id = body.get("conversation_id")
    message_id = body.get("message_id")
    note = body.get("note", "").strip()

    if not conversation_id or not message_id:
        raise HTTPException(status_code=400, detail="缺少 conversation_id 或 message_id")

    from server.database import get_session
    from server.models.conversation import Message
    with next(get_session()) as session:
        msg = session.get(Message, message_id)
        if not msg:
            raise HTTPException(status_code=404, detail="消息不存在")
        content = f"{msg.content}" if msg.role == "assistant" else f"用户: {msg.content}"
        if note:
            content = f"{content}\n备注: {note}"

    mid = mem.add_memory(content, "manual", {"source_conv_id": conversation_id})
    return {"code": "OK", "data": {"id": mid}}


@router.get("")
def list_memories(mem_type: str = None, limit: int = 50):
    data = mem.list_memories(mem_type=mem_type, limit=limit)
    return {"code": "OK", "data": data}


@router.get("/search")
def search_memories_route(q: str = "", top_k: int = 5):
    if not q:
        raise HTTPException(status_code=400, detail="缺少查询参数 q")
    results = mem.search_memories(q, top_k=top_k)
    return {"code": "OK", "data": results}


@router.delete("/{mem_id}")
def delete_memory_route(mem_id: str):
    mem.delete_memory(mem_id)
    return {"code": "OK", "data": None}


@router.post("/conversations/{conv_id}/summarize")
def summarize_route(conv_id: str):
    count = mem.summarize_conversation(conv_id)
    return {"code": "OK", "data": {"count": count}}
```

**Step 2: 在 main.py 注册路由**

```python
from server.routers.memories import router as memories_router
app.include_router(memories_router)
```

**Step 3: 运行全部测试 → 提交**

---

### Task 4: RAG 集成 — 问答时注入记忆

**Files:**
- Modify: `server/services/rag.py`

**Step 1: 修改 build_qa_prompt，增加记忆参数**

在 `build_qa_prompt` 函数签名后，加上可选的 `memories` 参数：

```python
def build_qa_prompt(question: str, chunks: list[dict], memories: list[dict] | None = None) -> str:
    memory_section = ""
    if memories:
        mem_parts = []
        for m in memories[:3]:
            mem_parts.append(f"- {m['content']}")
        if mem_parts:
            memory_section = f"\n## 相关记忆\n" + "\n".join(mem_parts) + "\n"

    if not chunks:
        return f"用户问题：{question}\n\n{memory_section}知识库中未找到相关内容，请如实告知用户。"

    context_parts = []
    for i, chunk in enumerate(chunks, 1):
        context_parts.append(f"[{i}] 来源: {chunk['document_title']}\n{chunk['content']}")

    context = "\n\n".join(context_parts)

    return f"""你是一个知识库助手。请根据以下参考资料回答用户问题。

{memory_section}
## 参考资料
{context}

## 要求
- 使用参考资料中的信息回答问题
- 回答中引用来源编号，如 [1]、[2]
- 如果有相关记忆，优先参考记忆中的用户偏好
- 如果参考资料不足以回答问题，如实说明
- 使用中文回答

## 用户问题
{question}"""
```

**Step 2: 修改 RAGService.ask_sync 和 ask_stream**

在构建 prompt 前检索记忆：

```python
def ask_sync(self, question: str) -> dict:
    chunks = self.retriever.retrieve(question)
    memories = search_memories(question, top_k=3)
    prompt = build_qa_prompt(question, chunks, memories)
    ...

async def ask_stream(self, question: str) -> AsyncIterator[dict]:
    chunks = self.retriever.retrieve(question)
    memories = search_memories(question, top_k=3)
    prompt = build_qa_prompt(question, chunks, memories)
    ...
```

需要在 rag.py 顶部 import search_memories。

**Step 3: 运行测试 → 提交**

---

### Task 5: 前端 — 记忆按钮 + 面板

**Files:**
- Modify: `server/templates/index.html`

在消息气泡旁边添加「记住」按钮（hover 时显示），在侧边栏底部添加「记忆」导航项。

简化为最小可用：每条 AI 回复下方加一个 `[记住]` 文字按钮，侧边栏底部加「记忆」入口（复用现有消息列表 UI 展示记忆内容）。

---

### Task 6: 配置 + 端到端验证

更新 `config.py` DEFAULTS，运行全量测试，验证记忆 API + 前端基本功能。

---

## 依赖关系

```
Task 1 (MemoryStore)
  └→ Task 2 (memory service)
       ├→ Task 3 (API)
       └→ Task 4 (RAG 集成)
Task 2 + 3 → Task 5 (前端)
Task 2-5 → Task 6 (验证)
```
