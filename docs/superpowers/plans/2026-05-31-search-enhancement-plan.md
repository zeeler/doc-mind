# 搜索增强 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 Doc Mind 增加混合搜索（SQLite FTS5 关键词 + ChromaDB 向量），RRF 融合，搜索结果高亮，独立搜索 API，前端搜索 UI。

**Architecture:** 新建 SearchService 组合 FTS5 关键词搜索和 ChromaDB 向量搜索，RRF 加权融合去重，highlight 函数标记匹配词。新增 `/api/v1/search` 端点，retriever 和 documents 路由内部切到 SearchService。前端文档页工具栏加搜索框。

**Tech Stack:** Python 3.12+ / SQLite FTS5 / ChromaDB / FastAPI / Vue 3 CDN

---

## 文件结构

```
新增:
  server/services/search.py            # SearchService + highlight + RRF
  server/routers/search.py             # GET /api/v1/search
  server/tests/test_search.py          # FTS / 向量 / RRF / 高亮单元测试
  server/tests/test_routers/test_search.py  # 搜索 API 测试

修改:
  server/database.py                   # FTS5 迁移 + FTS 写入/删除辅助
  server/services/pipeline.py          # 索引同步
  server/services/retriever.py         # 切到 SearchService
  server/routers/documents.py          # search= 参数切到 SearchService
  server/templates/index.html          # 搜索 UI
```

---

### Task 1: FTS5 数据库迁移

**Files:**
- Modify: `server/database.py`

- [ ] **Step 1: 添加 FTS5 迁移**

在 `server/database.py` 的 `_migrate()` 函数中，在现有 v2 迁移代码之后、`conn.close()` 之前，追加以下代码：

```python
        # v3 迁移：FTS5 全文索引
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                chunk_id,
                content,
                document_title,
                tokenize='unicode61'
            )
        """)
        conn.commit()
```

- [ ] **Step 2: 添加 FTS 辅助函数**

在 `_migrate` 函数之后（同文件末尾），添加 FTS 写入/删除的辅助函数：

```python
def fts_insert(chunk_id: str, content: str, title: str) -> None:
    """向 FTS5 索引写入一条 chunk。"""
    import sqlite3
    db_path = str(DATA_DIR / "app.db")
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO chunks_fts(chunk_id, content, document_title) VALUES (?, ?, ?)",
            (chunk_id, content, title),
        )
        conn.commit()
    finally:
        conn.close()


def fts_delete_by_chunk_id(chunk_id: str) -> None:
    """从 FTS5 索引删除指定 chunk。"""
    import sqlite3
    db_path = str(DATA_DIR / "app.db")
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DELETE FROM chunks_fts WHERE chunk_id = ?", (chunk_id,))
        conn.commit()
    finally:
        conn.close()


def fts_delete_by_document_id(document_id: str) -> None:
    """从 FTS5 索引删除某文档的所有 chunk。"""
    import sqlite3
    db_path = str(DATA_DIR / "app.db")
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "DELETE FROM chunks_fts WHERE chunk_id IN (SELECT id FROM document_chunks WHERE document_id = ?)",
            (document_id,),
        )
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 3: 验证迁移**

```bash
cd /Users/terry/Documents/cc_projects/my_agent1 && python -c "
import tempfile, os
os.environ['KB_DATA_DIR'] = tempfile.mkdtemp()
from server.database import reset_engine, init_db, get_engine
reset_engine()
init_db()
engine = get_engine()
from sqlalchemy import inspect
tables = inspect(engine).get_table_names()
# FTS5 虚拟表可能不在 SQLAlchemy table_names 中，用 raw sqlite 验证
import sqlite3
db_path = str(engine.url).replace('sqlite:///', '')
conn = sqlite3.connect(db_path)
rows = conn.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name='chunks_fts'\").fetchall()
assert len(rows) == 1
print('FTS5 table created OK')
conn.close()
"
```

- [ ] **Step 4: Commit**

```bash
git add server/database.py
git commit -m "feat: add FTS5 full-text index migration and helpers"
```

---

### Task 2: SearchService 核心实现 + 单元测试

**Files:**
- Create: `server/services/search.py`
- Create: `server/tests/test_search.py`

- [ ] **Step 1: 编写 SearchService 测试**

创建 `server/tests/test_search.py`：

```python
"""SearchService 单元测试。"""
import pytest
import tempfile
import os
from pathlib import Path


@pytest.fixture
def search_service():
    """创建带测试数据的 SearchService。"""
    td = tempfile.mkdtemp()
    data_dir = Path(td)
    (data_dir / "chroma").mkdir()
    db_path = data_dir / "app.db"

    # 创建 FTS5 表和 document_chunks / documents 表（简化版）
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY,
            title TEXT,
            file_name TEXT,
            file_type TEXT,
            status TEXT,
            folder_path TEXT,
            category TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS document_chunks (
            id TEXT PRIMARY KEY,
            document_id TEXT,
            chunk_no INTEGER,
            content TEXT,
            token_count INTEGER
        )
    """)
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
            chunk_id, content, document_title, tokenize='unicode61'
        )
    """)
    # 插入测试数据
    conn.execute("INSERT INTO documents VALUES ('doc1', 'Python入门', 'python.pdf', 'pdf', 'done', '', '技术')")
    conn.execute("INSERT INTO document_chunks VALUES ('c1', 'doc1', 1, 'Python是一种解释型编程语言，广泛用于数据科学和机器学习。', 20)")
    conn.execute("INSERT INTO document_chunks VALUES ('c2', 'doc1', 2, '机器学习是人工智能的一个分支，专注于从数据中学习模式。', 20)")
    conn.execute("INSERT INTO document_chunks VALUES ('c3', 'doc1', 3, 'Python拥有丰富的科学计算库，如NumPy、Pandas和Scikit-learn。', 20)")
    conn.execute("INSERT INTO chunks_fts VALUES ('c1', 'Python是一种解释型编程语言，广泛用于数据科学和机器学习。', 'Python入门')")
    conn.execute("INSERT INTO chunks_fts VALUES ('c2', '机器学习是人工智能的一个分支，专注于从数据中学习模式。', 'Python入门')")
    conn.execute("INSERT INTO chunks_fts VALUES ('c3', 'Python拥有丰富的科学计算库，如NumPy、Pandas和Scikit-learn。', 'Python入门')")
    conn.commit()
    conn.close()

    os.environ["KB_DATA_DIR"] = td
    from server.database import reset_engine, DATA_DIR
    reset_engine()

    from server.services.search import SearchService
    svc = SearchService(data_dir=data_dir, top_k=10)
    return svc


class TestFTSSearch:
    def test_fts_keyword_search(self, search_service):
        results = search_service._fts_search("Python")
        assert len(results) > 0
        assert any("Python" in r["content"] for r in results)

    def test_fts_no_match(self, search_service):
        results = search_service._fts_search("zzzznonexistent")
        assert len(results) == 0


class TestRRFMerge:
    def test_rrf_merge_ranks(self, search_service):
        kw = [
            {"chunk_id": "c1", "content": "a", "document_title": "t1"},
            {"chunk_id": "c2", "content": "b", "document_title": "t1"},
        ]
        vec = [
            {"chunk_id": "c2", "content": "b", "document_title": "t1"},
            {"chunk_id": "c1", "content": "a", "document_title": "t1"},
        ]
        merged = search_service._rrf_merge(kw, vec)
        # c2 在两个结果中都排位靠前，应排第一
        assert merged[0]["chunk_id"] == "c2"

    def test_rrf_merge_dedup(self, search_service):
        kw = [{"chunk_id": "c1", "content": "a", "document_title": "t"}]
        vec = [{"chunk_id": "c1", "content": "a", "document_title": "t"}]
        merged = search_service._rrf_merge(kw, vec)
        assert len(merged) == 1

    def test_rrf_marks_match_type(self, search_service):
        kw = [{"chunk_id": "c1", "content": "a", "document_title": "t"}]
        vec = [{"chunk_id": "c2", "content": "b", "document_title": "t"}]
        merged = search_service._rrf_merge(kw, vec)
        assert merged[0]["match_type"] == "hybrid" if merged[0]["chunk_id"] == "c1" else True
        # c1 只有 keyword，c2 只有 vector
        types = {m["chunk_id"]: m["match_type"] for m in merged}
        assert types["c1"] == "keyword"
        assert types["c2"] == "vector"


class TestHighlight:
    def test_highlight_simple(self):
        from server.services.search import highlight
        result = highlight("Python是一种编程语言", "Python")
        assert "<mark>Python</mark>" in result

    def test_highlight_excerpt(self):
        from server.services.search import highlight
        long_text = "前" * 100 + "Python编程" + "后" * 100
        result = highlight(long_text, "Python", max_len=80)
        assert "<mark>Python</mark>" in result
        assert len(result) <= 80 + len("<mark></mark>") + 10  # 允许省略号
```

- [ ] **Step 2: 运行测试验证失败**

```bash
cd /Users/terry/Documents/cc_projects/my_agent1 && python -m pytest server/tests/test_search.py -v --tb=short 2>&1 | tail -20
```

Expected: all fail (module not found)

- [ ] **Step 3: 实现 SearchService**

创建 `server/services/search.py`：

```python
"""混合搜索服务 — FTS5 关键词 + ChromaDB 向量 + RRF 融合 + 高亮。"""

import re
import sqlite3
import logging
from pathlib import Path
from server.vector.store import VectorStore

logger = logging.getLogger("knowledge-base")


def highlight(text: str, query: str, max_len: int = 160) -> str:
    """在文本中高亮搜索词，截取第一个匹配附近的 excerpt。"""
    tokens = query.strip().split()
    result = text
    for token in tokens:
        result = re.sub(
            f"({re.escape(token)})",
            r"<mark>\1</mark>",
            result,
            flags=re.IGNORECASE,
        )

    first = result.find("<mark>")
    if first >= 0:
        start = max(0, first - max_len // 2)
        end = min(len(result), first + max_len // 2)
        prefix = "..." if start > 0 else ""
        suffix = "..." if end < len(result) else ""
        result = prefix + result[start:end] + suffix

    return result


class SearchService:
    def __init__(self, data_dir: Path, top_k: int = 10):
        self.data_dir = data_dir
        self.top_k = top_k
        self.db_path = str(data_dir / "app.db")
        self._vector_store = None

    @property
    def vector_store(self):
        if self._vector_store is None:
            self._vector_store = VectorStore(persist_dir=str(self.data_dir / "chroma"))
        return self._vector_store

    def _fts_search(self, query: str, top_k: int | None = None, document_id: str | None = None) -> list[dict]:
        """FTS5 关键词搜索，返回排名结果。"""
        k = top_k or self.top_k
        conn = sqlite3.connect(self.db_path)
        try:
            base_sql = """
                SELECT c.id, c.content, d.title, d.file_name, c.chunk_no, d.id as doc_id
                FROM chunks_fts
                JOIN document_chunks c ON chunks_fts.chunk_id = c.id
                JOIN documents d ON c.document_id = d.id
                WHERE chunks_fts MATCH ?
            """
            params = [query]
            if document_id:
                base_sql += " AND d.id = ?"
                params.append(document_id)
            base_sql += " ORDER BY rank LIMIT ?"
            params.append(k)

            rows = conn.execute(base_sql, params).fetchall()
            return [
                {
                    "chunk_id": r[0],
                    "content": r[1],
                    "document_title": r[2],
                    "file_name": r[3],
                    "chunk_no": r[4],
                    "document_id": r[5],
                }
                for r in rows
            ]
        except sqlite3.OperationalError as e:
            logger.warning(f"FTS search error: {e}")
            return []
        finally:
            conn.close()

    def _vector_search(self, query: str, top_k: int | None = None, document_id: str | None = None) -> list[dict]:
        """ChromaDB 向量搜索。"""
        k = top_k or self.top_k
        where = {"document_id": document_id} if document_id else None
        hits = self.vector_store.search(query, top_k=k, where=where)
        return [
            {
                "chunk_id": h["id"],
                "content": h["content"],
                "document_title": h.get("metadata", {}).get("title", ""),
                "file_name": h.get("metadata", {}).get("file_name", ""),
                "chunk_no": h.get("metadata", {}).get("chunk_no", 0),
                "document_id": h.get("metadata", {}).get("document_id", ""),
            }
            for h in hits
        ]

    def _rrf_merge(self, keyword_results: list[dict], vector_results: list[dict], k: int = 60, alpha: float = 0.5) -> list[dict]:
        """RRF (Reciprocal Rank Fusion) 结果融合。"""
        info: dict[str, dict] = {}

        for rank, r in enumerate(keyword_results, 1):
            cid = r["chunk_id"]
            info[cid] = {"keyword_rank": rank, "vector_rank": None, "data": r}

        for rank, r in enumerate(vector_results, 1):
            cid = r["chunk_id"]
            if cid in info:
                info[cid]["vector_rank"] = rank
            else:
                info[cid] = {"keyword_rank": None, "vector_rank": rank, "data": r}

        merged = []
        for cid, entry in info.items():
            kw_rank = entry["keyword_rank"]
            vec_rank = entry["vector_rank"]
            score = 0.0
            if kw_rank:
                score += alpha / (k + kw_rank)
            if vec_rank:
                score += (1 - alpha) / (k + vec_rank)

            match_type = "hybrid"
            if kw_rank and not vec_rank:
                match_type = "keyword"
            elif vec_rank and not kw_rank:
                match_type = "vector"

            merged.append({
                **entry["data"],
                "score": round(score, 4),
                "match_type": match_type,
            })

        merged.sort(key=lambda x: x["score"], reverse=True)
        return merged

    def hybrid_search(self, query: str, top_k: int | None = None, document_id: str | None = None) -> list[dict]:
        """混合搜索：FTS5 关键词 + 向量搜索 + RRF 融合。"""
        k = top_k or self.top_k
        fetch_k = k * 2

        keyword_results = self._fts_search(query, top_k=fetch_k, document_id=document_id)
        vector_results = self._vector_search(query, top_k=fetch_k, document_id=document_id)

        merged = self._rrf_merge(keyword_results, vector_results)

        for r in merged:
            r["excerpt"] = highlight(r["content"], query)

        return merged[:k]

    def document_search(self, query: str, top_k: int | None = None) -> list[dict]:
        """文档级搜索：按文档聚合、去重、取最佳匹配。"""
        chunks = self.hybrid_search(query, top_k=top_k or self.top_k * 3)

        docs: dict[str, dict] = {}
        for c in chunks:
            did = c["document_id"]
            if did not in docs:
                docs[did] = {
                    "document_id": did,
                    "title": c["document_title"],
                    "best_score": c["score"],
                    "match_count": 1,
                    "top_excerpts": [c["excerpt"]],
                }
            else:
                docs[did]["match_count"] += 1
                docs[did]["best_score"] = max(docs[did]["best_score"], c["score"])
                if len(docs[did]["top_excerpts"]) < 3:
                    docs[did]["top_excerpts"].append(c["excerpt"])

        result = sorted(docs.values(), key=lambda x: x["best_score"], reverse=True)
        return result[: top_k or self.top_k]
```

- [ ] **Step 4: 运行测试**

```bash
cd /Users/terry/Documents/cc_projects/my_agent1 && python -m pytest server/tests/test_search.py -v --tb=short
```

Expected: all tests pass

- [ ] **Step 5: Commit**

```bash
git add server/services/search.py server/tests/test_search.py
git commit -m "feat: add SearchService with FTS5 keyword + vector hybrid search and RRF merge"
```

---

### Task 3: FTS 索引同步 — pipeline.py + documents 删除

**Files:**
- Modify: `server/services/pipeline.py`
- Modify: `server/routers/documents.py`

- [ ] **Step 1: 在 pipeline.py 中写入 FTS**

在 `server/services/pipeline.py` 的 `process_document` 函数中，每次向 VectorStore 添加 chunk 后，同步写入 FTS。在文件顶部添加 import：

```python
from server.database import DATA_DIR, get_session, fts_insert, fts_delete_by_document_id
```

在每次 `store.add(...)` 调用之后，添加 FTS 写入。找到两处 `store.add(...)`（约第 79 行和 94 行），每处之后添加：

```python
                    fts_insert(chunk.id, chunk_content, doc.title)
```

同样在 `index_document` 函数中的 `store.add(...)` 之后（约第 155 行和 159 行）也添加：

```python
                fts_insert(chunk.id, chunk_content, doc.title)
```

在 `process_document` 开始处（解析前），清理旧 FTS 数据。在 `doc.status = "parsing"` 之前添加：

```python
        fts_delete_by_document_id(doc_id)
```

- [ ] **Step 2: 在 documents.py 删除端点中同步删除 FTS**

在 `server/routers/documents.py` 的 `delete_document` 函数中，在删除向量数据之后、session.delete 之前，添加 FTS 清理。在现有 import 中添加：

```python
from server.database import fts_delete_by_document_id
```

在 `delete_document` 函数中，在 `shutil.rmtree(file_dir)` 之后添加：

```python
    try:
        fts_delete_by_document_id(doc_id)
    except Exception:
        pass
```

在批量删除中也会调用 session.delete(doc)，但通过 cascade 删除 chunks。需要在批量操作中也处理。在 batch 端点的 delete action 中，`session.delete(doc)` 之前添加：

```python
                try:
                    fts_delete_by_document_id(doc_id)
                except Exception:
                    pass
```

- [ ] **Step 3: 验证测试全部通过**

```bash
cd /Users/terry/Documents/cc_projects/my_agent1 && python -m pytest server/tests/ -v --tb=short 2>&1 | tail -5
```

Expected: 所有测试通过

- [ ] **Step 4: Commit**

```bash
git add server/services/pipeline.py server/routers/documents.py
git commit -m "feat: sync FTS index on document processing and deletion"
```

---

### Task 4: 搜索 API 路由 + 测试

**Files:**
- Create: `server/routers/search.py`
- Create: `server/tests/test_routers/test_search.py`
- Modify: `server/main.py`

- [ ] **Step 1: 编写搜索 API 测试**

创建 `server/tests/test_routers/test_search.py`：

```python
"""搜索 API 测试。"""
import pytest
from fastapi.testclient import TestClient
from server.main import app


@pytest.fixture
def client(tmp_data_dir, monkeypatch):
    monkeypatch.setattr("server.database.DATA_DIR", tmp_data_dir)
    monkeypatch.setattr("server.config.DATA_DIR", tmp_data_dir)
    monkeypatch.setattr("server.routers.documents.DATA_DIR", tmp_data_dir)
    from server.database import reset_engine
    reset_engine()
    from server.models.base import Base
    from server.database import get_engine
    from server.models.tag import Tag  # noqa: F401
    from server.models.collection import Collection  # noqa: F401
    Base.metadata.create_all(bind=get_engine())
    return TestClient(app)


class TestSearchRoutes:
    def test_search_empty_query(self, client):
        response = client.get("/api/v1/search")
        assert response.status_code == 400

    def test_search_chunks_no_results(self, client):
        response = client.get("/api/v1/search?q=zzzznonexistent12345")
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == "OK"
        assert isinstance(data["data"], list)

    def test_search_chunks(self, client, sample_txt):
        with open(sample_txt, "rb") as f:
            client.post("/api/v1/documents/upload", files={"file": ("test.txt", f, "text/plain")})
        response = client.get("/api/v1/search?q=测试")
        assert response.status_code == 200
        data = response.json()
        assert "data" in data

    def test_search_documents(self, client, sample_txt):
        with open(sample_txt, "rb") as f:
            client.post("/api/v1/documents/upload", files={"file": ("test.txt", f, "text/plain")})
        response = client.get("/api/v1/search?q=测试&type=documents")
        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        if data["data"]:
            assert "best_score" in data["data"][0]
            assert "match_count" in data["data"][0]

    def test_search_with_document_filter(self, client, sample_txt):
        with open(sample_txt, "rb") as f:
            upload_resp = client.post("/api/v1/documents/upload", files={"file": ("test.txt", f, "text/plain")})
        doc_id = upload_resp.json()["data"]["id"]
        response = client.get(f"/api/v1/search?q=测试&document_id={doc_id}")
        assert response.status_code == 200
```

- [ ] **Step 2: 运行测试验证失败**

```bash
cd /Users/terry/Documents/cc_projects/my_agent1 && python -m pytest server/tests/test_routers/test_search.py -v --tb=short 2>&1 | tail -15
```

Expected: 5 tests fail (404)

- [ ] **Step 3: 实现搜索路由**

创建 `server/routers/search.py`：

```python
"""搜索路由 — 混合搜索 API。"""
import logging
from fastapi import APIRouter, HTTPException, Query, Depends
from sqlalchemy.orm import Session
from server.database import DATA_DIR, get_session
from server.services.search import SearchService
from server.models.document import Document

logger = logging.getLogger("knowledge-base")

router = APIRouter(prefix="/api/v1", tags=["search"])


@router.get("/search")
def search(
    q: str = Query(default="", description="搜索关键词"),
    type: str = Query(default="chunks", description="搜索类型: chunks 或 documents"),
    top_k: int = Query(default=10, ge=1, le=50),
    document_id: str | None = Query(default=None),
    session: Session = Depends(get_session),
):
    if not q.strip():
        raise HTTPException(status_code=400, detail="搜索关键词不能为空")

    svc = SearchService(data_dir=DATA_DIR, top_k=top_k)

    if type == "documents":
        results = svc.document_search(q.strip(), top_k=top_k)
    else:
        results = svc.hybrid_search(q.strip(), top_k=top_k, document_id=document_id)

    return {"code": "OK", "message": "success", "data": results}
```

- [ ] **Step 4: 在 main.py 注册路由**

在 `server/main.py` 中，在现有路由注册之后添加：

```python
from server.routers.search import router as search_router
app.include_router(search_router)
```

- [ ] **Step 5: 运行测试验证通过**

```bash
cd /Users/terry/Documents/cc_projects/my_agent1 && python -m pytest server/tests/test_routers/test_search.py -v --tb=short
```

Expected: 5 passed

- [ ] **Step 6: 验证全部测试**

```bash
cd /Users/terry/Documents/cc_projects/my_agent1 && python -m pytest server/tests/ -v --tb=short 2>&1 | tail -5
```

- [ ] **Step 7: Commit**

```bash
git add server/routers/search.py server/tests/test_routers/test_search.py server/main.py
git commit -m "feat: add search API endpoint with hybrid search support"
```

---

### Task 5: 更新 Retriever 使用 SearchService

**Files:**
- Modify: `server/services/retriever.py`

- [ ] **Step 1: 切换到 SearchService**

修改 `server/services/retriever.py`，将原来的纯向量检索替换为 SearchService 混合搜索：

```python
"""检索服务 — 混合搜索（关键词 + 向量）。"""

from server.database import DATA_DIR
from server.services.search import SearchService


class Retriever:
    def __init__(self, vector_store, config: dict):
        self.top_k = int(config.get("retrieval_top_k", "5"))
        self.search_service = SearchService(data_dir=DATA_DIR, top_k=self.top_k)

    def retrieve(self, query: str) -> list[dict]:
        results = self.search_service.hybrid_search(query, top_k=self.top_k)
        return [
            {
                "chunk_id": r["chunk_id"],
                "content": r["content"],
                "score": r.get("score", 0.0),
                "document_id": r.get("document_id", ""),
                "document_title": r.get("document_title", ""),
                "file_name": r.get("file_name", ""),
                "chunk_no": r.get("chunk_no", 0),
            }
            for r in results
        ]
```

注意：`vector_store` 参数保留以兼容现有调用方，但不再使用。

- [ ] **Step 2: 运行测试验证**

```bash
cd /Users/terry/Documents/cc_projects/my_agent1 && python -m pytest server/tests/test_retriever.py server/tests/test_rag.py -v --tb=short
```

- [ ] **Step 3: Commit**

```bash
git add server/services/retriever.py
git commit -m "feat: switch retriever to hybrid SearchService"
```

---

### Task 6: 更新 Documents 路由的 search 参数

**Files:**
- Modify: `server/routers/documents.py`

- [ ] **Step 1: 文档列表 search 参数切到 SearchService**

在 `server/routers/documents.py` 的 `list_documents` 函数中，当 `search` 参数存在时，使用 SearchService 的文档级搜索获取匹配的文档 ID，然后按 ID 过滤。

在文件顶部添加 import：

```python
from server.services.search import SearchService
```

修改 `list_documents` 中的 search 处理逻辑。当前代码：

```python
    if search is not None:
        q = q.filter(Document.title.ilike(f"%{search}%"))
```

替换为：

```python
    if search is not None:
        search_svc = SearchService(data_dir=DATA_DIR, top_k=50)
        doc_results = search_svc.document_search(search, top_k=50)
        match_ids = [d["document_id"] for d in doc_results]
        if match_ids:
            q = q.filter(Document.id.in_(match_ids))
        else:
            q = q.filter(Document.id == "__no_match__")
```

- [ ] **Step 2: 运行测试验证**

```bash
cd /Users/terry/Documents/cc_projects/my_agent1 && python -m pytest server/tests/test_routers/test_documents.py -v -k "search" --tb=short
```

- [ ] **Step 3: Commit**

```bash
git add server/routers/documents.py
git commit -m "feat: use SearchService for document search instead of ilike"
```

---

### Task 7: 前端搜索 UI

**Files:**
- Modify: `server/templates/index.html`

- [ ] **Step 1: 修改 docs 工具栏，增强搜索框**

在 `server/templates/index.html` 中找到 docs 页面的工具栏 div `.docs-toolbar`（约在新增的文档管理 HTML 中），将现有的搜索 input：

```html
          <input v-model="filters.search" @input="loadDocuments" placeholder="搜索文档标题...">
```

替换为：

```html
          <input v-model="filters.search" @keydown.enter="searchDocs" placeholder="搜索文档内容... (回车搜索)">
          <select v-model="searchType" style="max-width:100px">
            <option value="chunks">片段</option>
            <option value="documents">文档</option>
          </select>
          <button @click="searchDocs" style="padding:6px 12px;border:1px solid var(--border);border-radius:6px;background:var(--bg);cursor:pointer;font:12px 'Inter',sans-serif;color:var(--text-secondary)">搜索</button>
          <button v-if="searchResults!==null" @click="clearSearch" style="padding:6px 8px;border:none;background:none;cursor:pointer;font-size:14px">✕</button>
```

- [ ] **Step 2: 在文档列表区域之下，添加搜索结果展示**

在 `.docs-toolbar` div 之后、文档列表 `.batch-mode` div 之前，添加搜索结果展示区域：

```html
        <div v-if="searchResults!==null" style="margin-bottom:12px">
          <div v-if="searchType==='chunks'">
            <div v-for="r in searchResults" :key="r.chunk_id" style="padding:12px 16px;border:1px solid var(--border);border-radius:var(--radius-sm);margin-bottom:6px">
              <div style="font-size:12px;color:var(--text-tertiary);margin-bottom:4px;display:flex;gap:8px">
                <span>{{ r.document_title }}</span>
                <span>块 {{ r.chunk_no }}</span>
                <span>分数: {{ r.score }}</span>
                <span style="background:var(--bg-hover);padding:0 6px;border-radius:99px;font-size:10px">{{ r.match_type }}</span>
              </div>
              <div class="search-excerpt" v-html="r.excerpt" style="font-size:13px;line-height:1.6"></div>
            </div>
          </div>
          <div v-if="searchType==='documents'">
            <div v-for="r in searchResults" :key="r.document_id" style="padding:12px 16px;border:1px solid var(--border);border-radius:var(--radius-sm);margin-bottom:6px;cursor:pointer" @click="filters.search='';searchResults=null">
              <div style="font-size:14px;font-weight:500">{{ r.title }}</div>
              <div style="font-size:12px;color:var(--text-tertiary);margin-top:2px">{{ r.match_count }} 处匹配 · 最佳分数 {{ r.best_score }}</div>
              <div v-for="(ex,i) in r.top_excerpts" :key="i" class="search-excerpt" v-html="ex" style="font-size:12px;margin-top:4px;color:var(--text-secondary)"></div>
            </div>
          </div>
          <div v-if="searchResults.length===0" style="text-align:center;padding:40px;color:var(--text-tertiary)">未找到匹配内容</div>
        </div>
```

- [ ] **Step 3: 添加 CSS**

在 `</style>` 之前添加搜索高亮样式：

```css
/* === Search === */
.search-excerpt mark { background:#fde68a; color:#000; padding:1px 2px; border-radius:2px; }
@media (prefers-color-scheme: dark) {
  .search-excerpt mark { background:#854d0e; color:#fef9c3; }
}
```

- [ ] **Step 4: 添加 JS 状态和方法**

在 `setup()` 中添加新状态（在现有 `filters` 之后）：

```javascript
    const searchResults = ref(null);
    const searchType = ref('chunks');
```

添加搜索方法（在 `loadAllCategories` 之后）：

```javascript
    async function searchDocs() {
      const q = filters.search.trim();
      if (!q) { searchResults.value = null; return; }
      try {
        const d = await api('/api/v1/search?q=' + encodeURIComponent(q) + '&type=' + searchType.value + '&top_k=10');
        searchResults.value = d.data || [];
      } catch(e) { console.error(e); }
    }
    function clearSearch() {
      filters.search = '';
      searchResults.value = null;
      loadDocuments();
    }
```

更新 `return` 对象，添加 `searchResults`、`searchType`、`searchDocs`、`clearSearch`。

- [ ] **Step 5: 运行测试验证**

```bash
cd /Users/terry/Documents/cc_projects/my_agent1 && python -m pytest server/tests/ -v --tb=short 2>&1 | tail -5
```

Expected: 所有测试通过

- [ ] **Step 6: 手动验证前端**

```bash
cd /Users/terry/Documents/cc_projects/my_agent1 && python server/main.py
```

浏览器打开 `http://localhost:8000`，进入文档管理页面，测试搜索功能。

- [ ] **Step 7: Commit**

```bash
git add server/templates/index.html
git commit -m "feat: add search UI with highlighting in docs page"
```

---

### Task 8: 最终验证

- [ ] **Step 1: 运行全部测试**

```bash
cd /Users/terry/Documents/cc_projects/my_agent1 && python -m pytest server/tests/ -v --tb=short 2>&1 | tail -8
```

Expected: ~128 passed

- [ ] **Step 2: 更新 README 测试计数**

```bash
sed -i '' 's/# 116 tests/# 128 tests/' README.md
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: update test count to 128"
```
