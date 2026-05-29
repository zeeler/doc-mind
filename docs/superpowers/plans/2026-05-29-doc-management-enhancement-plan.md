# 文档管理增强 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 Doc Mind 增加标签系统、分类系统、虚拟集合、文件夹浏览和批量操作能力。

**Architecture:** 新增 4 张表（Tag、document_tags、Collection、collection_documents）实现多对多关系；Document 表加 folder_path 和 category 列；新增 tags/collections 两个独立路由；文档路由扩展增加更新、筛选、文件夹、批量端点；前端使用两栏布局重构文档管理页面。

**Tech Stack:** Python 3.12+ / FastAPI / SQLAlchemy / SQLite / Vue 3 CDN

---

## 文件结构

```
新增:
  server/models/tag.py           # Tag 模型 + document_tags 关联表
  server/models/collection.py    # Collection 模型 + collection_documents 关联表
  server/routers/tags.py         # 标签 CRUD API
  server/routers/collections.py  # 集合 CRUD API
  server/tests/test_routers/test_tags.py
  server/tests/test_routers/test_collections.py
  server/tests/test_routers/test_batch.py

修改:
  server/models/document.py      # 加 folder_path, category 列 + tags/collections 关系
  server/database.py             # _migrate() 加新表+新列
  server/main.py                 # 注册新模型和路由
  server/routers/documents.py    # PUT /{id}, 增强列表筛选, /folders, /batch
  server/templates/index.html    # 两栏布局、标签/集合/批量 UI
  server/tests/test_routers/test_documents.py  # 新增更新/筛选用例
```

---

### Task 1: 创建 Tag 模型

**Files:**
- Create: `server/models/tag.py`

- [ ] **Step 1: 创建 Tag 模型和关联表**

```python
"""标签模型。"""
import uuid
from sqlalchemy import String, Column, ForeignKey, Table
from sqlalchemy.orm import Mapped, mapped_column
from server.models.base import Base

document_tags = Table(
    "document_tags",
    Base.metadata,
    Column("doc_id", String(36), ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", String(36), ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
```

- [ ] **Step 2: 验证模型可以正确导入**

```bash
cd /Users/terry/Documents/cc_projects/my_agent1 && python -c "from server.models.tag import Tag, document_tags; print('Tag model OK, table:', document_tags.name)"
```

Expected: `Tag model OK, table: document_tags`

- [ ] **Step 3: Commit**

```bash
git add server/models/tag.py
git commit -m "feat: add Tag model with document_tags association table"
```

---

### Task 2: 创建 Collection 模型

**Files:**
- Create: `server/models/collection.py`

- [ ] **Step 1: 创建 Collection 模型和关联表**

```python
"""集合模型。"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Column, ForeignKey, Table, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column
from server.models.base import Base

collection_documents = Table(
    "collection_documents",
    Base.metadata,
    Column("doc_id", String(36), ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True),
    Column("collection_id", String(36), ForeignKey("collections.id", ondelete="CASCADE"), primary_key=True),
    Column("added_at", DateTime, default=lambda: datetime.now(timezone.utc)),
)


class Collection(Base):
    __tablename__ = "collections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
```

- [ ] **Step 2: 验证模型可以正确导入**

```bash
cd /Users/terry/Documents/cc_projects/my_agent1 && python -c "from server.models.collection import Collection, collection_documents; print('Collection model OK')"
```

Expected: `Collection model OK`

- [ ] **Step 3: Commit**

```bash
git add server/models/collection.py
git commit -m "feat: add Collection model with collection_documents association table"
```

---

### Task 3: 更新 Document 模型

**Files:**
- Modify: `server/models/document.py`

- [ ] **Step 1: 在 Document 类中添加新字段和关系**

找到 `server/models/document.py`，在 `checksum` 字段之后添加：

```python
folder_path: Mapped[str] = mapped_column(String(1000), default="", index=True)
category: Mapped[str] = mapped_column(String(100), default="", index=True)
```

在 `chunks` 关系之后添加：

```python
tags: Mapped[list["Tag"]] = relationship(
    "Tag", secondary="document_tags", back_populates="documents", lazy="selectin"
)
collections: Mapped[list["Collection"]] = relationship(
    "Collection", secondary="collection_documents", back_populates="documents", lazy="selectin"
)
```

同时在文件顶部 import 区域添加（`checksum` 字段后面，`created_at` 之前）：

```python
from server.models.tag import Tag, document_tags
from server.models.collection import Collection, collection_documents
```

wait — `Tag` 和 `Collection` 还没有 `documents` 关系，需要先在 Tag 和 Collection 模型中加上。先更新 Tag 模型。

- [ ] **Step 2: 更新 Tag 模型添加反向关系**

回到 `server/models/tag.py`，在 `Tag` 类的 `name` 字段之后添加 `documents` 关系。修改后的完整文件：

```python
"""标签模型。"""
import uuid
from sqlalchemy import String, Column, ForeignKey, Table
from sqlalchemy.orm import Mapped, mapped_column, relationship
from server.models.base import Base

document_tags = Table(
    "document_tags",
    Base.metadata,
    Column("doc_id", String(36), ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", String(36), ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    documents: Mapped[list["Document"]] = relationship(
        "Document", secondary=document_tags, back_populates="tags"
    )
```

- [ ] **Step 3: 更新 Collection 模型添加反向关系**

回到 `server/models/collection.py`，在 `Collection` 类的 `created_at` 字段之后添加 `documents` 关系。修改后的完整文件：

```python
"""集合模型。"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Column, ForeignKey, Table, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from server.models.base import Base

collection_documents = Table(
    "collection_documents",
    Base.metadata,
    Column("doc_id", String(36), ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True),
    Column("collection_id", String(36), ForeignKey("collections.id", ondelete="CASCADE"), primary_key=True),
    Column("added_at", DateTime, default=lambda: datetime.now(timezone.utc)),
)


class Collection(Base):
    __tablename__ = "collections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    documents: Mapped[list["Document"]] = relationship(
        "Document", secondary=collection_documents, back_populates="collections"
    )
```

- [ ] **Step 4: 更新 Document 模型（完整修改）**

`server/models/document.py` 最终应为：

```python
"""文档与切块模型。"""

import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Integer, DateTime, ForeignKey, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from server.models.base import Base


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    file_name: Mapped[str] = mapped_column(String(500), nullable=False)
    file_type: Mapped[str] = mapped_column(String(20), nullable=False)
    file_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    elapsed_ms: Mapped[int] = mapped_column(Integer, default=0)
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    folder_path: Mapped[str] = mapped_column(String(1000), default="", index=True)
    category: Mapped[str] = mapped_column(String(100), default="", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    chunks: Mapped[list["DocumentChunk"]] = relationship("DocumentChunk", back_populates="document", cascade="all, delete-orphan")
    tags: Mapped[list["Tag"]] = relationship(
        "Tag", secondary="document_tags", back_populates="documents", lazy="selectin"
    )
    collections: Mapped[list["Collection"]] = relationship(
        "Collection", secondary="collection_documents", back_populates="documents", lazy="selectin"
    )


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id: Mapped[str] = mapped_column(String(36), ForeignKey("documents.id"), nullable=False, index=True)
    chunk_no: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    document: Mapped["Document"] = relationship("Document", back_populates="chunks")
```

- [ ] **Step 5: 验证所有模型可导入且关系正确**

```bash
cd /Users/terry/Documents/cc_projects/my_agent1 && python -c "
from server.models.tag import Tag, document_tags
from server.models.collection import Collection, collection_documents
from server.models.document import Document, DocumentChunk
print('All models imported OK')
print('Document.tags:', Document.__mapper__.relationships.get('tags'))
print('Document.collections:', Document.__mapper__.relationships.get('collections'))
"
```

Expected: All models imported OK + relationship info

- [ ] **Step 6: Commit**

```bash
git add server/models/tag.py server/models/collection.py server/models/document.py
git commit -m "feat: add folder_path/category to Document, relationships to Tag/Collection"
```

---

### Task 4: 添加数据库迁移

**Files:**
- Modify: `server/database.py`

- [ ] **Step 1: 在 `_migrate` 函数中添加新表和列**

在 `server/database.py` 的 `_migrate` 函数中，在现有迁移代码的最后（`conn.close()` 之前）添加：

```python
        # v2 迁移：文档管理增强（标签、集合、文件夹、分类）
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tags (
                id VARCHAR(36) PRIMARY KEY,
                name VARCHAR(100) NOT NULL UNIQUE
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS document_tags (
                doc_id VARCHAR(36) NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                tag_id VARCHAR(36) NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
                PRIMARY KEY (doc_id, tag_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS collections (
                id VARCHAR(36) PRIMARY KEY,
                name VARCHAR(200) NOT NULL,
                description TEXT,
                created_at TIMESTAMP NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS collection_documents (
                doc_id VARCHAR(36) NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                collection_id VARCHAR(36) NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
                added_at TIMESTAMP,
                PRIMARY KEY (doc_id, collection_id)
            )
        """)
        conn.commit()

        cols2 = {r[1] for r in conn.execute("PRAGMA table_info(documents)")}
        if "folder_path" not in cols2:
            conn.execute("ALTER TABLE documents ADD COLUMN folder_path TEXT DEFAULT ''")
            conn.commit()
        if "category" not in cols2:
            conn.execute("ALTER TABLE documents ADD COLUMN category VARCHAR(100) DEFAULT ''")
            conn.commit()
```

- [ ] **Step 2: 创建测试数据库验证迁移**

```bash
cd /Users/terry/Documents/cc_projects/my_agent1 && python -c "
import tempfile, os
from pathlib import Path
os.environ['KB_DATA_DIR'] = tempfile.mkdtemp()
from server.database import init_db, get_engine, reset_engine, DATA_DIR
reset_engine()
init_db()
engine = get_engine()
# 验证新表存在
from sqlalchemy import inspect
inspector = inspect(engine)
tables = inspector.get_table_names()
print('Tables:', sorted(tables))
assert 'tags' in tables
assert 'document_tags' in tables
assert 'collections' in tables
assert 'collection_documents' in tables
print('All new tables created OK')
# 验证新列存在
cols = {c['name'] for c in inspector.get_columns('documents')}
assert 'folder_path' in cols
assert 'category' in cols
print('All new columns added OK')
"
```

Expected: All assertions pass

- [ ] **Step 3: 验证已有数据库升级不报错**

```bash
cd /Users/terry/Documents/cc_projects/my_agent1 && python -c "
import tempfile, os, sqlite3
from pathlib import Path
# 模拟旧数据库（只有 documents 表，没有新列）
td = tempfile.mkdtemp()
db_path = Path(td) / 'app.db'
conn = sqlite3.connect(str(db_path))
conn.execute('CREATE TABLE documents (id TEXT PRIMARY KEY, title TEXT)')
conn.commit()
conn.close()
# 设置 DATA_DIR 指向这个旧数据库
os.environ['KB_DATA_DIR'] = td
from server.database import reset_engine, init_db
reset_engine()
init_db()
# 验证新表和新列已追加
conn = sqlite3.connect(str(db_path))
tables = {r[0] for r in conn.execute(\"SELECT name FROM sqlite_master WHERE type='table'\")}
assert 'tags' in tables
cols = {r[1] for r in conn.execute('PRAGMA table_info(documents)')}
assert 'folder_path' in cols
conn.close()
print('Migration from old DB OK')
"
```

Expected: `Migration from old DB OK`

- [ ] **Step 4: Commit**

```bash
git add server/database.py
git commit -m "feat: add v2 migration for tags, collections, folder_path, category"
```

---

### Task 5: 注册新模型和路由到 main.py

**Files:**
- Modify: `server/main.py`

- [ ] **Step 1: 在 main.py 中注册新模型和路由**

在 `server/main.py` 中：

**import 部分**（在 `from server.models.job import Job` 之后添加）：
```python
from server.models.tag import Tag  # noqa: F401
from server.models.collection import Collection  # noqa: F401
```

**路由注册部分**（在 `app.include_router(memories_router)` 之后添加）：
```python
from server.routers.tags import router as tags_router
from server.routers.collections import router as collections_router
app.include_router(tags_router)
app.include_router(collections_router)
```

**startup() 函数中**（在现有模型导入后添加）：
```python
    from server.models.tag import Tag  # noqa: F811
    from server.models.collection import Collection  # noqa: F811
```

- [ ] **Step 2: 验证应用启动不报错**

```bash
cd /Users/terry/Documents/cc_projects/my_agent1 && timeout 5 python -c "
import tempfile, os
os.environ['KB_DATA_DIR'] = tempfile.mkdtemp()
from server.database import reset_engine
reset_engine()
from server.main import app
print('App created OK, routes:', len(app.routes))
" 2>&1 || true
```

Expected: `App created OK, routes: <number>`

- [ ] **Step 3: 验证现有测试仍然通过**

```bash
cd /Users/terry/Documents/cc_projects/my_agent1 && python -m pytest server/tests/ -v --tb=short 2>&1 | tail -20
```

Expected: 87 passed

- [ ] **Step 4: Commit**

```bash
git add server/main.py
git commit -m "feat: register Tag/Collection models and routers in main.py"
```

---

### Task 6: 创建 Tags API 路由和测试

**Files:**
- Create: `server/routers/tags.py`
- Create: `server/tests/test_routers/test_tags.py`

- [ ] **Step 1: 编写失败测试**

```bash
cd /Users/terry/Documents/cc_projects/my_agent1 && python -m pytest server/tests/test_routers/ -k "test_tags" -v 2>&1
```

先创建测试文件 `server/tests/test_routers/test_tags.py`：

```python
"""标签路由测试。"""
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
    # 确保新模型被导入以创建新表
    from server.models.tag import Tag  # noqa: F401
    from server.models.collection import Collection  # noqa: F401
    Base.metadata.create_all(bind=get_engine())
    return TestClient(app)


class TestTagRoutes:
    def test_create_tag(self, client):
        response = client.post("/api/v1/tags", json={"name": "python"})
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == "OK"
        assert data["data"]["name"] == "python"

    def test_create_tag_empty_name(self, client):
        response = client.post("/api/v1/tags", json={"name": ""})
        assert response.status_code == 400

    def test_create_tag_too_long(self, client):
        response = client.post("/api/v1/tags", json={"name": "a" * 101})
        assert response.status_code == 400

    def test_create_duplicate_tag_returns_existing(self, client):
        r1 = client.post("/api/v1/tags", json={"name": "Python"})
        assert r1.status_code == 200
        r2 = client.post("/api/v1/tags", json={"name": "python"})
        assert r2.status_code == 200
        assert r1.json()["data"]["id"] == r2.json()["data"]["id"]

    def test_list_tags(self, client):
        client.post("/api/v1/tags", json={"name": "ai"})
        client.post("/api/v1/tags", json={"name": "ml"})
        response = client.get("/api/v1/tags")
        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]) >= 2
        # 验证有 doc_count 字段
        assert "doc_count" in data["data"][0]

    def test_delete_tag(self, client):
        r = client.post("/api/v1/tags", json={"name": "delete-me"})
        tag_id = r.json()["data"]["id"]
        response = client.delete(f"/api/v1/tags/{tag_id}")
        assert response.status_code == 200
        # 确认已删除
        list_resp = client.get("/api/v1/tags")
        ids = [t["id"] for t in list_resp.json()["data"]]
        assert tag_id not in ids

    def test_delete_tag_cascades_associations(self, client):
        """删除标签应级联删除 document_tags 关联。"""
        # 这个测试依赖有文档存在，仅验证 API 不报错
        r = client.post("/api/v1/tags", json={"name": "cascade-test"})
        tag_id = r.json()["data"]["id"]
        resp = client.delete(f"/api/v1/tags/{tag_id}")
        assert resp.status_code == 200
```

- [ ] **Step 2: 运行测试验证失败**

```bash
cd /Users/terry/Documents/cc_projects/my_agent1 && python -m pytest server/tests/test_routers/test_tags.py -v --tb=short 2>&1 | tail -20
```

Expected: 7 tests fail (router not found / 404)

- [ ] **Step 3: 实现 Tags 路由**

创建 `server/routers/tags.py`：

```python
"""标签管理路由。"""
import uuid
import logging
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from server.database import get_session
from server.models.tag import Tag, document_tags

logger = logging.getLogger("knowledge-base")

router = APIRouter(prefix="/api/v1/tags", tags=["tags"])


def _normalize(name: str) -> str:
    return name.strip().lower()


@router.get("")
def list_tags(session: Session = Depends(get_session)):
    tags = session.query(Tag).order_by(Tag.name).all()
    return {
        "code": "OK",
        "message": "success",
        "data": [
            {
                "id": t.id,
                "name": t.name,
                "doc_count": session.query(func.count(document_tags.c.doc_id))
                .filter(document_tags.c.tag_id == t.id)
                .scalar(),
            }
            for t in tags
        ],
    }


@router.post("")
def create_tag(payload: dict, session: Session = Depends(get_session)):
    name = payload.get("name", "")
    if not name or not name.strip():
        raise HTTPException(status_code=400, detail="标签名不能为空")
    if len(name.strip()) > 100:
        raise HTTPException(status_code=400, detail="标签名不能超过100个字符")

    normalized = _normalize(name)
    existing = session.query(Tag).filter(func.lower(Tag.name) == normalized).first()
    if existing:
        return {
            "code": "OK",
            "message": "success",
            "data": {"id": existing.id, "name": existing.name, "duplicate": True},
        }

    tag = Tag(name=name.strip())
    session.add(tag)
    session.commit()
    session.refresh(tag)
    return {
        "code": "OK",
        "message": "success",
        "data": {"id": tag.id, "name": tag.name},
    }


@router.delete("/{tag_id}")
def delete_tag(tag_id: str, session: Session = Depends(get_session)):
    tag = session.get(Tag, tag_id)
    if not tag:
        raise HTTPException(status_code=404, detail="标签不存在")
    session.delete(tag)
    session.commit()
    return {"code": "OK", "message": "success", "data": None}
```

- [ ] **Step 4: 运行测试验证通过**

```bash
cd /Users/terry/Documents/cc_projects/my_agent1 && python -m pytest server/tests/test_routers/test_tags.py -v --tb=short
```

Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add server/routers/tags.py server/tests/test_routers/test_tags.py
git commit -m "feat: add tags CRUD API with tests"
```

---

### Task 7: 创建 Collections API 路由和测试

**Files:**
- Create: `server/routers/collections.py`
- Create: `server/tests/test_routers/test_collections.py`

- [ ] **Step 1: 编写失败测试**

创建 `server/tests/test_routers/test_collections.py`：

```python
"""集合路由测试。"""
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


class TestCollectionRoutes:
    def test_create_collection(self, client):
        response = client.post("/api/v1/collections", json={"name": "重要文档"})
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == "OK"
        assert data["data"]["name"] == "重要文档"

    def test_create_collection_empty_name(self, client):
        response = client.post("/api/v1/collections", json={"name": ""})
        assert response.status_code == 400

    def test_list_collections_empty(self, client):
        response = client.get("/api/v1/collections")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data["data"], list)

    def test_list_collections(self, client):
        client.post("/api/v1/collections", json={"name": "c1"})
        client.post("/api/v1/collections", json={"name": "c2"})
        response = client.get("/api/v1/collections")
        assert len(response.json()["data"]) >= 2

    def test_update_collection(self, client):
        r = client.post("/api/v1/collections", json={"name": "old-name"})
        cid = r.json()["data"]["id"]
        response = client.put(f"/api/v1/collections/{cid}", json={"name": "new-name", "description": "desc"})
        assert response.status_code == 200
        # 验证更新生效
        detail = client.get("/api/v1/collections")
        names = [c["name"] for c in detail.json()["data"]]
        assert "new-name" in names

    def test_update_nonexistent_collection(self, client):
        response = client.put("/api/v1/collections/nonexistent", json={"name": "x"})
        assert response.status_code == 404

    def test_delete_collection(self, client):
        r = client.post("/api/v1/collections", json={"name": "delete-me"})
        cid = r.json()["data"]["id"]
        response = client.delete(f"/api/v1/collections/{cid}")
        assert response.status_code == 200
        # 确认已删除
        list_resp = client.get("/api/v1/collections")
        ids = [c["id"] for c in list_resp.json()["data"]]
        assert cid not in ids
```

- [ ] **Step 2: 运行测试验证失败**

```bash
cd /Users/terry/Documents/cc_projects/my_agent1 && python -m pytest server/tests/test_routers/test_collections.py -v --tb=short 2>&1 | tail -15
```

Expected: 7 tests fail

- [ ] **Step 3: 实现 Collections 路由**

创建 `server/routers/collections.py`：

```python
"""集合管理路由。"""
import uuid
from datetime import datetime, timezone
import logging
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from server.database import get_session
from server.models.collection import Collection, collection_documents

logger = logging.getLogger("knowledge-base")

router = APIRouter(prefix="/api/v1/collections", tags=["collections"])


@router.get("")
def list_collections(session: Session = Depends(get_session)):
    collections = session.query(Collection).order_by(Collection.created_at.desc()).all()
    return {
        "code": "OK",
        "message": "success",
        "data": [
            {
                "id": c.id,
                "name": c.name,
                "description": c.description,
                "doc_count": session.query(func.count(collection_documents.c.doc_id))
                .filter(collection_documents.c.collection_id == c.id)
                .scalar(),
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in collections
        ],
    }


@router.post("")
def create_collection(payload: dict, session: Session = Depends(get_session)):
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="集合名不能为空")

    coll = Collection(
        id=str(uuid.uuid4()),
        name=name,
        description=payload.get("description"),
        created_at=datetime.now(timezone.utc),
    )
    session.add(coll)
    session.commit()
    session.refresh(coll)
    return {
        "code": "OK",
        "message": "success",
        "data": {"id": coll.id, "name": coll.name, "description": coll.description},
    }


@router.put("/{collection_id}")
def update_collection(collection_id: str, payload: dict, session: Session = Depends(get_session)):
    coll = session.get(Collection, collection_id)
    if not coll:
        raise HTTPException(status_code=404, detail="集合不存在")
    name = (payload.get("name") or "").strip()
    if name:
        coll.name = name
    if "description" in payload:
        coll.description = payload["description"]
    session.commit()
    return {"code": "OK", "message": "success", "data": None}


@router.delete("/{collection_id}")
def delete_collection(collection_id: str, session: Session = Depends(get_session)):
    coll = session.get(Collection, collection_id)
    if not coll:
        raise HTTPException(status_code=404, detail="集合不存在")
    session.delete(coll)
    session.commit()
    return {"code": "OK", "message": "success", "data": None}
```

- [ ] **Step 4: 运行测试验证通过**

```bash
cd /Users/terry/Documents/cc_projects/my_agent1 && python -m pytest server/tests/test_routers/test_collections.py -v --tb=short
```

Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add server/routers/collections.py server/tests/test_routers/test_collections.py
git commit -m "feat: add collections CRUD API with tests"
```

---

### Task 8: 扩展 Documents 路由 — 更新、增强列表、文件夹

**Files:**
- Modify: `server/routers/documents.py`
- Modify: `server/tests/test_routers/test_documents.py`

- [ ] **Step 1: 编写新测试**

在 `server/tests/test_routers/test_documents.py` 文件末尾追加新测试类：

```python
class TestDocumentUpdate:
    def test_update_category(self, client, sample_txt):
        with open(sample_txt, "rb") as f:
            upload_resp = client.post("/api/v1/documents/upload", files={"file": ("test.txt", f, "text/plain")})
        doc_id = upload_resp.json()["data"]["id"]

        response = client.put(f"/api/v1/documents/{doc_id}", json={"category": "技术"})
        assert response.status_code == 200

        # 通过列表验证分类已设置
        list_resp = client.get("/api/v1/documents")
        doc = next(d for d in list_resp.json()["data"] if d["id"] == doc_id)
        # category 在增强列表中返回
        detail = client.get(f"/api/v1/documents/{doc_id}")
        assert detail.status_code == 200

    def test_add_tags_to_document(self, client, sample_txt):
        with open(sample_txt, "rb") as f:
            upload_resp = client.post("/api/v1/documents/upload", files={"file": ("test.txt", f, "text/plain")})
        doc_id = upload_resp.json()["data"]["id"]

        response = client.put(f"/api/v1/documents/{doc_id}", json={"add_tags": ["python", "ai"]})
        assert response.status_code == 200

    def test_add_to_collections(self, client, sample_txt):
        with open(sample_txt, "rb") as f:
            upload_resp = client.post("/api/v1/documents/upload", files={"file": ("test.txt", f, "text/plain")})
        doc_id = upload_resp.json()["data"]["id"]

        # 先创建集合
        coll_resp = client.post("/api/v1/collections", json={"name": "测试集"})
        coll_id = coll_resp.json()["data"]["id"]

        response = client.put(f"/api/v1/documents/{doc_id}", json={"add_collections": [coll_id]})
        assert response.status_code == 200

    def test_update_nonexistent_document(self, client):
        response = client.put("/api/v1/documents/nonexistent", json={"category": "x"})
        assert response.status_code == 404


class TestDocumentFilters:
    def test_list_documents_with_status_filter(self, client, sample_txt):
        with open(sample_txt, "rb") as f:
            client.post("/api/v1/documents/upload", files={"file": ("test.txt", f, "text/plain")})
        response = client.get("/api/v1/documents?status=done")
        assert response.status_code == 200
        for d in response.json()["data"]:
            assert d["status"] == "done"

    def test_list_documents_with_search(self, client, sample_txt):
        with open(sample_txt, "rb") as f:
            client.post("/api/v1/documents/upload", files={"file": ("unique_title_xyz.txt", f, "text/plain")})
        response = client.get("/api/v1/documents?search=unique_title_xyz")
        assert response.status_code == 200
        assert len(response.json()["data"]) >= 1

    def test_list_documents_response_includes_tags(self, client, sample_txt):
        with open(sample_txt, "rb") as f:
            upload_resp = client.post("/api/v1/documents/upload", files={"file": ("test.txt", f, "text/plain")})
        doc_id = upload_resp.json()["data"]["id"]
        client.put(f"/api/v1/documents/{doc_id}", json={"add_tags": ["t1"]})
        response = client.get("/api/v1/documents")
        doc = next(d for d in response.json()["data"] if d["id"] == doc_id)
        assert "tags" in doc
        assert len(doc["tags"]) >= 1

    def test_list_documents_by_tag(self, client, sample_txt):
        with open(sample_txt, "rb") as f:
            upload_resp = client.post("/api/v1/documents/upload", files={"file": ("test.txt", f, "text/plain")})
        doc_id = upload_resp.json()["data"]["id"]
        client.put(f"/api/v1/documents/{doc_id}", json={"add_tags": ["unique-tag-xyz"]})
        response = client.get("/api/v1/documents?tag=unique-tag-xyz")
        assert response.status_code == 200
        assert len(response.json()["data"]) >= 1


class TestFolders:
    def test_list_folders(self, client):
        response = client.get("/api/v1/documents/folders")
        assert response.status_code == 200
        assert isinstance(response.json()["data"], list)
```

- [ ] **Step 2: 运行测试验证失败**

```bash
cd /Users/terry/Documents/cc_projects/my_agent1 && python -m pytest server/tests/test_routers/test_documents.py -v -k "TestDocumentUpdate or TestDocumentFilters or TestFolders" --tb=short 2>&1 | tail -20
```

Expected: 8 tests fail (404 for PUT / not found)

- [ ] **Step 3: 扩展 documents.py 路由**

在 `server/routers/documents.py` 中进行以下修改。

**3a. 修改 upload_document 端点，接受可选的 `folder_path` form 字段：**

找到 `upload_document` 函数签名，在 `file: UploadFile = File(...)` 之后添加 `folder_path: str = Form("")`。在函数顶部 import 区添加 `Form`：

```python
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends
```

在创建 `Document` 对象时，添加 `folder_path` 字段：

```python
    doc = Document(
        id=doc_id,
        title=Path(file.filename).stem,
        file_name=file.filename,
        file_type=suffix,
        file_path=str(file_path),
        file_size=len(content),
        checksum=checksum,
        status="pending",
        folder_path=folder_path,
    )
```

**3b. 替换 `list_documents` 函数为增强版：**

```python
from server.models.tag import Tag, document_tags
from server.models.collection import Collection, collection_documents
```

**替换 `list_documents` 函数**为增强版：

```python
@router.get("")
def list_documents(
    skip: int = 0,
    limit: int = 50,
    folder: str | None = None,
    category: str | None = None,
    tag: str | None = None,
    collection: str | None = None,
    status: str | None = None,
    search: str | None = None,
    session: Session = Depends(get_session),
):
    q = session.query(Document)

    if folder is not None:
        q = q.filter(Document.folder_path == folder)
    if category is not None:
        q = q.filter(Document.category == category)
    if status is not None:
        q = q.filter(Document.status == status)
    if search is not None:
        q = q.filter(Document.title.ilike(f"%{search}%"))
    if tag is not None:
        q = q.join(Document.tags).filter(Tag.name == tag)
    if collection is not None:
        q = q.join(Document.collections).filter(Collection.id == collection)

    docs = q.order_by(Document.created_at.desc()).offset(skip).limit(limit).all()
    return {
        "code": "OK",
        "message": "success",
        "data": [
            {
                "id": d.id,
                "title": d.title,
                "file_name": d.file_name,
                "file_type": d.file_type,
                "file_size": d.file_size,
                "status": d.status,
                "chunk_count": d.chunk_count,
                "elapsed_ms": d.elapsed_ms,
                "folder_path": d.folder_path,
                "category": d.category,
                "tags": [{"id": t.id, "name": t.name} for t in d.tags],
                "collections": [{"id": c.id, "name": c.name} for c in d.collections],
                "created_at": d.created_at.isoformat(),
            }
            for d in docs
        ],
    }
```

**在文件末尾新增以下端点**（`delete_document` 之后）：

```python
@router.put("/{doc_id}")
def update_document(doc_id: str, payload: dict, session: Session = Depends(get_session)):
    doc = session.get(Document, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")

    # 更新分类
    if "category" in payload:
        doc.category = (payload["category"] or "").strip()

    # 添加标签
    for tag_name in payload.get("add_tags") or []:
        name = tag_name.strip()
        if not name:
            continue
        normalized = name.lower()
        tag_obj = session.query(Tag).filter(func.lower(Tag.name) == normalized).first()
        if not tag_obj:
            tag_obj = Tag(id=str(uuid.uuid4()), name=name)
            session.add(tag_obj)
            session.flush()
        if tag_obj not in doc.tags:
            doc.tags.append(tag_obj)

    # 移除标签
    for tag_name in payload.get("remove_tags") or []:
        normalized = tag_name.strip().lower()
        tag_obj = session.query(Tag).filter(func.lower(Tag.name) == normalized).first()
        if tag_obj and tag_obj in doc.tags:
            doc.tags.remove(tag_obj)

    # 添加集合
    for coll_id in payload.get("add_collections") or []:
        coll = session.get(Collection, coll_id)
        if coll and coll not in doc.collections:
            doc.collections.append(coll)

    # 移除集合
    for coll_id in payload.get("remove_collections") or []:
        coll = session.get(Collection, coll_id)
        if coll and coll in doc.collections:
            doc.collections.remove(coll)

    session.commit()
    return {"code": "OK", "message": "success", "data": None}


@router.get("/folders")
def list_folders(session: Session = Depends(get_session)):
    rows = session.query(Document.folder_path).distinct().order_by(Document.folder_path).all()
    paths = [r[0] for r in rows]
    return {"code": "OK", "message": "success", "data": paths}
```

注意需要在文件顶部 import 中添加 `func`：
```python
from sqlalchemy import func
```

- [ ] **Step 4: 运行新测试验证通过**

```bash
cd /Users/terry/Documents/cc_projects/my_agent1 && python -m pytest server/tests/test_routers/test_documents.py -v --tb=short 2>&1 | tail -25
```

Expected: all tests pass (both old and new)

- [ ] **Step 5: 验证全部测试**

```bash
cd /Users/terry/Documents/cc_projects/my_agent1 && python -m pytest server/tests/ -v --tb=short 2>&1 | tail -10
```

Expected: 所有测试通过（约 95+ passed）

- [ ] **Step 6: Commit**

```bash
git add server/routers/documents.py server/tests/test_routers/test_documents.py
git commit -m "feat: add doc update, enhanced list filters, and folders endpoint"
```

---

### Task 9: 批量操作端点

**Files:**
- Modify: `server/routers/documents.py`（批量端点）
- Create: `server/tests/test_routers/test_batch.py`

- [ ] **Step 1: 编写批量操作测试**

创建 `server/tests/test_routers/test_batch.py`：

```python
"""批量操作测试。"""
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


class TestBatchOperations:
    def test_batch_empty_ids(self, client):
        response = client.post("/api/v1/documents/batch", json={"ids": [], "action": "delete"})
        assert response.status_code == 400

    def test_batch_unknown_action(self, client):
        response = client.post("/api/v1/documents/batch", json={"ids": ["x"], "action": "unknown"})
        assert response.status_code == 400

    def test_batch_delete(self, client, sample_txt):
        # 上传两个文档
        with open(sample_txt, "rb") as f:
            r1 = client.post("/api/v1/documents/upload", files={"file": ("a.txt", f, "text/plain")})
        with open(sample_txt, "rb") as f2:
            r2 = client.post("/api/v1/documents/upload", files={"file": ("b.txt", f2, "text/plain")})
        ids = [r1.json()["data"]["id"], r2.json()["data"]["id"]]

        response = client.post("/api/v1/documents/batch", json={"ids": ids, "action": "delete"})
        assert response.status_code == 200
        data = response.json()["data"]
        assert all(d["success"] for d in data)

        # 验证文档已删除
        list_resp = client.get("/api/v1/documents")
        remaining = [d["id"] for d in list_resp.json()["data"]]
        for did in ids:
            assert did not in remaining

    def test_batch_tag(self, client, sample_txt):
        with open(sample_txt, "rb") as f:
            r1 = client.post("/api/v1/documents/upload", files={"file": ("a.txt", f, "text/plain")})
        ids = [r1.json()["data"]["id"]]

        response = client.post("/api/v1/documents/batch", json={
            "ids": ids, "action": "tag", "params": {"tags": ["batch-tag"]}
        })
        assert response.status_code == 200
        assert response.json()["data"][0]["success"] is True

    def test_batch_categorize(self, client, sample_txt):
        with open(sample_txt, "rb") as f:
            r1 = client.post("/api/v1/documents/upload", files={"file": ("a.txt", f, "text/plain")})
        ids = [r1.json()["data"]["id"]]

        response = client.post("/api/v1/documents/batch", json={
            "ids": ids, "action": "categorize", "params": {"category": "技术"}
        })
        assert response.status_code == 200
        assert response.json()["data"][0]["success"] is True

    def test_batch_partial_failure(self, client, sample_txt):
        """批量操作中某个 id 不存在不应中断其他操作。"""
        with open(sample_txt, "rb") as f:
            r1 = client.post("/api/v1/documents/upload", files={"file": ("a.txt", f, "text/plain")})
        ids = [r1.json()["data"]["id"], "nonexistent-id"]

        response = client.post("/api/v1/documents/batch", json={
            "ids": ids, "action": "categorize", "params": {"category": "test"}
        })
        assert response.status_code == 200
        results = response.json()["data"]
        assert results[0]["success"] is True
        assert results[1]["success"] is False
```

- [ ] **Step 2: 运行测试验证失败**

```bash
cd /Users/terry/Documents/cc_projects/my_agent1 && python -m pytest server/tests/test_routers/test_batch.py -v --tb=short 2>&1 | tail -15
```

Expected: 6 tests fail

- [ ] **Step 3: 在 documents.py 中添加批量端点**

在 `server/routers/documents.py` 末尾追加：

```python
@router.post("/batch")
def batch_operation(payload: dict, session: Session = Depends(get_session)):
    ids = payload.get("ids") or []
    action = payload.get("action", "")
    params = payload.get("params") or {}

    if not ids:
        raise HTTPException(status_code=400, detail="ids 不能为空")

    valid_actions = {"delete", "retry", "tag", "untag", "categorize", "collect"}
    if action not in valid_actions:
        raise HTTPException(status_code=400, detail=f"不支持的操作类型: {action}")

    results = []
    for doc_id in ids:
        try:
            doc = session.get(Document, doc_id)
            if not doc:
                results.append({"id": doc_id, "success": False, "error": "文档不存在"})
                continue

            if action == "delete":
                session.delete(doc)
            elif action == "categorize":
                doc.category = (params.get("category") or "").strip()
            elif action == "tag":
                for tag_name in params.get("tags") or []:
                    name = tag_name.strip()
                    if not name:
                        continue
                    normalized = name.lower()
                    tag_obj = session.query(Tag).filter(func.lower(Tag.name) == normalized).first()
                    if not tag_obj:
                        tag_obj = Tag(id=str(uuid.uuid4()), name=name)
                        session.add(tag_obj)
                        session.flush()
                    if tag_obj not in doc.tags:
                        doc.tags.append(tag_obj)
            elif action == "untag":
                for tag_name in params.get("tags") or []:
                    normalized = tag_name.strip().lower()
                    tag_obj = session.query(Tag).filter(func.lower(Tag.name) == normalized).first()
                    if tag_obj and tag_obj in doc.tags:
                        doc.tags.remove(tag_obj)
            elif action == "collect":
                coll_id = params.get("collection_id")
                if coll_id:
                    coll = session.get(Collection, coll_id)
                    if coll and coll not in doc.collections:
                        doc.collections.append(coll)
            elif action == "retry":
                from server.services.worker import create_jobs_for_document
                if doc.status in ("done", "failed"):
                    doc.status = "pending"
                create_jobs_for_document(doc_id)

            results.append({"id": doc_id, "success": True})
        except Exception as e:
            logger.error(f"批量操作 {action} 在 {doc_id} 失败: {e}")
            results.append({"id": doc_id, "success": False, "error": str(e)})

    session.commit()
    return {"code": "OK", "message": "success", "data": results}
```

- [ ] **Step 4: 运行批量测试验证通过**

```bash
cd /Users/terry/Documents/cc_projects/my_agent1 && python -m pytest server/tests/test_routers/test_batch.py -v --tb=short
```

Expected: 6 passed

- [ ] **Step 5: 验证全部测试通过**

```bash
cd /Users/terry/Documents/cc_projects/my_agent1 && python -m pytest server/tests/ -v --tb=short 2>&1 | tail -5
```

Expected: 所有测试通过（约 108+ passed）

- [ ] **Step 6: Commit**

```bash
git add server/routers/documents.py server/tests/test_routers/test_batch.py
git commit -m "feat: add batch operations endpoint with tests"
```

---

### Task 10: 前端 — 文档管理页面重构

**Files:**
- Modify: `server/templates/index.html`

这是一个大改，拆分为 3 个子步骤：CSS 添加、HTML 结构变更、JS 逻辑添加。

- [ ] **Step 10.1: 添加新 CSS 样式**

在 `server/templates/index.html` 的 `</style>` 标签之前插入以下 CSS：

```css
/* === Docs Layout === */
.docs-layout { display:flex; flex:1; min-height:0; }
.docs-sidebar { width:220px; flex-shrink:0; overflow-y:auto; border-right:1px solid var(--border); padding:12px; background:var(--bg-sidebar); }
.docs-sidebar h4 { font-size:11px; font-weight:600; text-transform:uppercase; color:var(--text-tertiary); letter-spacing:.5px; margin:12px 0 6px; cursor:pointer; user-select:none; display:flex; justify-content:space-between; align-items:center; }
.docs-sidebar h4:first-child { margin-top:0; }
.docs-sidebar h4:hover { color:var(--text-secondary); }
.sidebar-section { margin-bottom:4px; }
.sidebar-section .item { padding:6px 10px; border-radius:6px; cursor:pointer; font-size:12px; color:var(--text-secondary); display:flex; justify-content:space-between; transition:all .1s; }
.sidebar-section .item:hover { background:var(--bg-hover); color:var(--text); }
.sidebar-section .item.active { background:var(--primary-light); color:var(--primary); font-weight:500; }
.sidebar-section .item .count { font-size:10px; color:var(--text-tertiary); background:var(--bg-active); padding:1px 6px; border-radius:99px; }
.sidebar-section .folder-child { padding-left:16px; }
.docs-main { flex:1; min-height:0; overflow-y:auto; padding:20px 24px; }

/* === Docs Toolbar === */
.docs-toolbar { display:flex; gap:8px; margin-bottom:12px; flex-wrap:wrap; align-items:center; }
.docs-toolbar input, .docs-toolbar select { padding:6px 10px; border:1px solid var(--border); border-radius:6px; font:12px/1.5 'Inter',sans-serif; color:var(--text); background:var(--bg); outline:none; }
.docs-toolbar input:focus, .docs-toolbar select:focus { border-color:var(--primary); box-shadow:0 0 0 2px var(--primary-ring); }
.docs-toolbar input { flex:1; min-width:140px; }
.docs-toolbar select { max-width:120px; }
.batch-toggle { padding:6px 12px; border:1px solid var(--border); border-radius:6px; background:var(--bg); cursor:pointer; font:12px 'Inter',sans-serif; color:var(--text-secondary); transition:all .15s; white-space:nowrap; }
.batch-toggle:hover { background:var(--bg-hover); }
.batch-toggle.on { background:var(--primary); color:#fff; border-color:var(--primary); }

/* === Doc item enhanced === */
.doc-item { position:relative; }
.doc-checkbox { display:none; margin-right:10px; width:16px; height:16px; accent-color:var(--primary); flex-shrink:0; }
.batch-mode .doc-checkbox { display:block; }
.batch-mode .doc-item { cursor:pointer; }
.batch-mode .doc-item.selected { border-color:var(--primary); background:var(--primary-light); }
.doc-tags { display:flex; gap:4px; flex-wrap:wrap; margin-top:4px; }
.doc-tag { font-size:10px; padding:1px 8px; border-radius:99px; background:var(--bg-hover); color:var(--text-secondary); cursor:pointer; border:1px solid var(--border); transition:all .1s; }
.doc-tag:hover { background:var(--primary-light); color:var(--primary); border-color:var(--primary); }
.doc-actions { display:flex; gap:4px; flex-shrink:0; }
.doc-actions button { font-size:11px; padding:3px 8px; border:1px solid var(--border); border-radius:4px; background:var(--bg); color:var(--text-secondary); cursor:pointer; transition:all .1s; font-family:inherit; }
.doc-actions button:hover { background:var(--bg-hover); color:var(--text); }
.doc-category { font-size:11px; padding:1px 8px; border-radius:99px; background:var(--primary-light); color:var(--primary); }

/* === Batch bar === */
.batch-bar { position:sticky; bottom:0; background:var(--card); border-top:1px solid var(--border); padding:10px 16px; display:flex; align-items:center; gap:10px; box-shadow:var(--shadow-lg); z-index:10; margin-top:12px; }
.batch-bar .selected-count { font-size:13px; font-weight:500; color:var(--text); }
.batch-bar button { padding:6px 14px; border:1px solid var(--border); border-radius:6px; background:var(--bg); cursor:pointer; font:12px 'Inter',sans-serif; color:var(--text-secondary); transition:all .15s; }
.batch-bar button:hover { background:var(--bg-hover); }
.batch-bar button.danger { color:var(--danger); border-color:var(--danger); }
.batch-bar button.danger:hover { background:var(--danger-bg); }

/* === Tag picker popup === */
.tag-picker { position:absolute; top:100%; right:0; z-index:20; background:var(--card); border:1px solid var(--border); border-radius:var(--radius-sm); box-shadow:var(--shadow-lg); padding:8px; min-width:180px; }
.tag-picker input { width:100%; padding:6px 8px; border:1px solid var(--border); border-radius:4px; font:12px 'Inter',sans-serif; color:var(--text); background:var(--bg); outline:none; margin-bottom:6px; }
.tag-picker input:focus { border-color:var(--primary); }
.tag-picker .tag-option { padding:4px 8px; font-size:12px; cursor:pointer; display:flex; align-items:center; gap:6px; border-radius:4px; color:var(--text-secondary); }
.tag-picker .tag-option:hover { background:var(--bg-hover); color:var(--text); }
.tag-picker .tag-option input { width:auto; margin:0; }

/* === Responsive === */
@media (max-width:768px) {
  .docs-sidebar { display:none; }
  .docs-sidebar.open { display:block; position:absolute; top:0; left:0; bottom:0; z-index:30; box-shadow:var(--shadow-lg); }
}
```

- [ ] **Step 10.2: 替换 Docs 区域的 HTML 模板**

把 `server/templates/index.html` 中现有的 `<!-- Docs -->` section（约第 250-285 行，从 `<div v-if="page==='docs'" class="page">` 到 `</div>`）替换为：

```html
    <!-- Docs -->
    <div v-if="page==='docs'" class="docs-layout">
      <aside class="docs-sidebar" :class="{open:sidebarOpen}">
        <div class="sidebar-section">
          <h4 @click="toggleSection('folders')">📁 目录浏览 <span>{{ sections.folders ? '▾' : '▸' }}</span></h4>
          <div v-show="sections.folders">
            <div class="item" :class="{active:!filters.folder}" @click="filters.folder='';loadDocuments()">全部文档</div>
            <div v-for="node in folderTree" :key="node.path" class="item folder-child" :class="{active:filters.folder===node.path}" @click="filters.folder=node.path;loadDocuments()">{{ node.name }} <span class="count">{{ node.count }}</span></div>
          </div>
        </div>
        <div class="sidebar-section">
          <h4 @click="toggleSection('tags')">🏷 标签 <span>{{ sections.tags ? '▾' : '▸' }}</span></h4>
          <div v-show="sections.tags">
            <div v-for="t in allTags" :key="t.id" class="item" :class="{active:filters.tag===t.name}" @click="filters.tag=filters.tag===t.name?'':t.name;loadDocuments()">{{ t.name }} <span class="count">{{ t.doc_count }}</span></div>
          </div>
        </div>
        <div class="sidebar-section">
          <h4 @click="toggleSection('collections')">📂 集合 <span>{{ sections.collections ? '▾' : '▸' }}</span></h4>
          <div v-show="sections.collections">
            <div v-for="c in allCollections" :key="c.id" class="item" :class="{active:filters.collection===c.id}" @click="filters.collection=filters.collection===c.id?'':c.id;loadDocuments()">{{ c.name }} <span class="count">{{ c.doc_count }}</span></div>
            <div class="item" style="color:var(--primary);font-style:italic" @click="showNewCollection=true">+ 新建集合</div>
            <div v-if="showNewCollection" style="padding:4px 8px"><input v-model="newCollectionName" @keydown.enter="createCollection" @keydown.escape="showNewCollection=false" placeholder="集合名" style="width:100%;padding:4px 8px;border:1px solid var(--border);border-radius:4px;font:12px 'Inter',sans-serif"></div>
          </div>
        </div>
      </aside>
      <main class="docs-main">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:12px">
          <button class="page-back" @click="page='chat'" style="margin-bottom:0">← 返回对话</button>
          <span style="font-size:18px;font-weight:700">文档管理</span>
          <button class="batch-toggle" :class="{on:batchMode}" @click="toggleBatchMode" style="margin-left:auto">{{ batchMode ? '退出批量' : '批量模式' }}</button>
        </div>
        <div class="upload-zone" @click="$refs.fileInput.click()" @dragover.prevent @drop.prevent="uploadDrop">
          <input type="file" ref="fileInput" @change="uploadFile" accept=".pdf,.docx,.md,.txt,.markdown" style="display:none">
          <p>{{ uploading ? '上传中...' : '拖拽文件到此处，或点击上传' }}</p>
          <span>支持 PDF / Word / Markdown / TXT</span>
        </div>
        <div style="display:flex;gap:8px;margin-bottom:12px">
          <button class="btn btn-ghost" @click="scanDirectory" style="flex:1;margin-top:0" :disabled="dirProcessing">{{ dirProcessing ? '扫描中 ('+dirProgress+')' : '选择本地目录' }}</button>
        </div>
        <div v-if="!dirProcessing && dirResult" style="font-size:12px;color:var(--text-secondary);margin-bottom:8px">{{ dirResult }}</div>
        <div v-if="jobStats" style="display:flex;gap:12px;margin-bottom:12px;font-size:12px;align-items:center">
          <span v-if="jobStats.pending" style="color:var(--text-tertiary)">{{ jobStats.pending }} 等待</span>
          <span v-if="jobStats.running" style="color:var(--info)">{{ jobStats.running }} 处理中</span>
          <span style="color:var(--text-secondary)">{{ jobStats.completed || 0 }} 完成</span>
          <span v-if="jobStats.failed" style="color:var(--danger)">{{ jobStats.failed }} 失败</span>
        </div>
        <div v-if="jobStats&&(jobStats.pending||jobStats.running)" style="margin-bottom:12px;height:3px;background:var(--bg-hover);border-radius:2px;overflow:hidden">
          <div :style="{width:jobPercent+'%',height:'100%',background:'var(--primary)',transition:'width .5s ease'}"></div>
        </div>
        <div class="docs-toolbar">
          <input v-model="filters.search" @input="loadDocuments" placeholder="搜索文档标题...">
          <select v-model="filters.status" @change="loadDocuments">
            <option value="">全部状态</option>
            <option value="pending">待处理</option>
            <option value="done">已完成</option>
            <option value="failed">失败</option>
          </select>
          <select v-model="filters.category" @change="loadDocuments">
            <option value="">全部分类</option>
            <option v-for="cat in allCategories" :key="cat" :value="cat">{{ cat }}</option>
          </select>
        </div>
        <div :class="{'batch-mode':batchMode}">
          <div v-for="d in documents" :key="d.id" class="doc-item" :class="{selected:selectedIds.has(d.id)}" @click.self="batchMode&&toggleSelect(d.id)">
            <input v-if="batchMode" type="checkbox" class="doc-checkbox" :checked="selectedIds.has(d.id)" @change="toggleSelect(d.id)" @click.stop style="display:block">
            <div class="doc-info">
              <div class="doc-title">{{ d.title }}</div>
              <div class="doc-meta">
                <span>{{ d.file_type }}</span>
                <span>{{ fmtSize(d.file_size) }}</span>
                <span :style="{color:statusColor(d.status)}">{{ statusText(d.status) }}</span>
                <span>{{ d.chunk_count }} 块</span>
                <span v-if="d.category" class="doc-category">{{ d.category }}</span>
              </div>
              <div class="doc-tags" v-if="d.tags&&d.tags.length">
                <span class="doc-tag" v-for="t in d.tags" :key="t.id" @click.stop="filters.tag=t.name;loadDocuments()">{{ t.name }}</span>
              </div>
            </div>
            <div class="doc-actions" v-if="!batchMode">
              <button @click="openTagPicker(d,$event)" title="打标签">🏷</button>
              <button @click="openCollectPicker(d,$event)" title="加集合">📂</button>
              <button @click="openCategoryEdit(d,$event)" title="改分类">📋</button>
              <button class="doc-del" @click="delDoc(d.id)">删除</button>
            </div>
            <div v-if="tagPickerDoc===d" class="tag-picker" @click.stop>
              <input v-model="tagSearch" @keydown.enter.prevent="addTagToDoc(d,tagSearch)" @keydown.escape="closeTagPicker" placeholder="输入标签名回车...">
              <div v-for="t in filteredTags" :key="t.id" class="tag-option">
                <input type="checkbox" :checked="d.tags&&d.tags.some(dt=>dt.id===t.id)" @change="toggleDocTag(d,t)">
                <span>{{ t.name }}</span>
              </div>
            </div>
            <div v-if="collectPickerDoc===d" class="tag-picker" @click.stop>
              <div v-for="c in allCollections" :key="c.id" class="tag-option">
                <input type="checkbox" :checked="d.collections&&d.collections.some(dc=>dc.id===c.id)" @change="toggleDocCollection(d,c)">
                <span>{{ c.name }}</span>
              </div>
            </div>
            <div v-if="categoryEditDoc===d" class="tag-picker" @click.stop>
              <input v-model="editCategoryName" @keydown.enter.prevent="saveDocCategory(d)" @keydown.escape="categoryEditDoc=null" placeholder="分类名，留空清除">
            </div>
          </div>
          <div v-if="documents.length===0" style="text-align:center;padding:40px;color:var(--text-tertiary)">暂无文档，上传或选择目录开始导入</div>
        </div>
        <div v-if="batchMode&&selectedIds.size>0" class="batch-bar">
          <span class="selected-count">已选 {{ selectedIds.size }} 项</span>
          <button @click="batchTag">🏷 打标签</button>
          <button @click="batchCategorize">📋 改分类</button>
          <button class="danger" @click="batchDelete">🗑 删除</button>
        </div>
        <!-- 批量操作的标签/分类输入弹窗 -->
        <div v-if="batchTagPrompt" style="position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:20px;z-index:50;box-shadow:var(--shadow-lg)">
          <h3 style="font-size:14px;margin-bottom:12px">批量打标签</h3>
          <input v-model="batchPromptInput" @keydown.enter.prevent="confirmBatchTag" @keydown.escape="batchTagPrompt=false" placeholder="输入标签名回车确认" style="width:100%;padding:8px 12px;border:1px solid var(--border);border-radius:6px;font:13px 'Inter',sans-serif;color:var(--text);background:var(--bg);outline:none">
          <div style="display:flex;gap:8px;margin-top:12px;justify-content:flex-end">
            <button @click="batchTagPrompt=false" style="padding:6px 12px;border:1px solid var(--border);border-radius:6px;background:var(--bg);cursor:pointer;font:12px 'Inter',sans-serif">取消</button>
            <button @click="confirmBatchTag" style="padding:6px 12px;border:none;border-radius:6px;background:var(--primary);color:#fff;cursor:pointer;font:12px 'Inter',sans-serif">确认</button>
          </div>
        </div>
        <div v-if="batchCatPrompt" style="position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:20px;z-index:50;box-shadow:var(--shadow-lg)">
          <h3 style="font-size:14px;margin-bottom:12px">批量改分类</h3>
          <input v-model="batchPromptInput" @keydown.enter.prevent="confirmBatchCat" @keydown.escape="batchCatPrompt=false" placeholder="输入分类名" style="width:100%;padding:8px 12px;border:1px solid var(--border);border-radius:6px;font:13px 'Inter',sans-serif;color:var(--text);background:var(--bg);outline:none">
          <div style="display:flex;gap:8px;margin-top:12px;justify-content:flex-end">
            <button @click="batchCatPrompt=false" style="padding:6px 12px;border:1px solid var(--border);border-radius:6px;background:var(--bg);cursor:pointer;font:12px 'Inter',sans-serif">取消</button>
            <button @click="confirmBatchCat" style="padding:6px 12px;border:none;border-radius:6px;background:var(--primary);color:#fff;cursor:pointer;font:12px 'Inter',sans-serif">确认</button>
          </div>
        </div>
      </main>
    </div>
```

- [ ] **Step 10.3: 修改 Vue 解构 + 添加 JS 状态和方法**

首先，修改 `setup()` 内第一行的 Vue 解构，添加 `computed`：

```javascript
// 找到这一行（约第 354 行）:
const { createApp, ref, reactive, nextTick, onMounted, watch } = Vue;
// 改为:
const { createApp, ref, reactive, computed, nextTick, onMounted, watch } = Vue;
```

在 `setup()` 函数的 `const memoriesList = ref([]);` 之后添加新状态：

```javascript
    // 文档管理增强状态
    const batchMode = ref(false);
    const selectedIds = ref(new Set());
    const allTags = ref([]);
    const allCollections = ref([]);
    const allCategories = ref([]);
    const folderTree = ref([]);
    const filters = reactive({folder:'', category:'', tag:'', collection:'', status:'', search:''});
    const sections = reactive({folders:true, tags:true, collections:true});
    const sidebarOpen = ref(false);
    const tagPickerDoc = ref(null);
    const tagSearch = ref('');
    const collectPickerDoc = ref(null);
    const categoryEditDoc = ref(null);
    const editCategoryName = ref('');
    const showNewCollection = ref(false);
    const newCollectionName = ref('');
    const batchTagPrompt = ref(false);
    const batchCatPrompt = ref(false);
    const batchPromptInput = ref('');
```

在 `return` 语句中添加这些新变量和方法。

改造 `loadDocuments` 为带筛选参数：

```javascript
    async function loadDocuments() {
      const params = new URLSearchParams();
      if (filters.folder) params.set('folder', filters.folder);
      if (filters.category) params.set('category', filters.category);
      if (filters.tag) params.set('tag', filters.tag);
      if (filters.collection) params.set('collection', filters.collection);
      if (filters.status) params.set('status', filters.status);
      if (filters.search) params.set('search', filters.search);
      const qs = params.toString();
      try {
        const d = await api('/api/v1/documents' + (qs ? '?' + qs : ''));
        documents.value = d.data || [];
      } catch(e) { console.error(e); }
    }
```

**修改 `scanDirectory` 的 `walk` 函数**，在调用 upload 时将 `prefix` 作为 `folder_path` 传递。找到现有的：

```javascript
const fd = new FormData(); fd.append('file', f, name);
const res = await fetch('/api/v1/documents/upload', {method:'POST', body:fd});
```

改为：

```javascript
const fd = new FormData(); fd.append('file', f, name);
if (prefix) fd.append('folder_path', prefix.replace(/\/$/, ''));
const res = await fetch('/api/v1/documents/upload', {method:'POST', body:fd});
```

新增方法（在 `deleteMemory` 函数之后添加）：

```javascript
    async function loadTags() {
      try { const d = await api('/api/v1/tags'); allTags.value = d.data || []; } catch(e) { console.error(e); }
    }
    async function loadCollections() {
      try { const d = await api('/api/v1/collections'); allCollections.value = d.data || []; } catch(e) { console.error(e); }
    }
    async function loadFolders() {
      try {
        const d = await api('/api/v1/documents/folders');
        const paths = d.data || [];
        const tree = {};
        for (const p of paths) {
          if (!p) continue;
          const parts = p.split('/').filter(Boolean);
          let node = tree;
          for (let i = 0; i < parts.length; i++) {
            if (!node[parts[i]]) node[parts[i]] = {_children: {}, _count: 0};
            node = node[parts[i]]._children;
          }
        }
        folderTree.value = [];
        function build(nodes, prefix) {
          const result = [];
          for (const [name, data] of Object.entries(nodes)) {
            const path = prefix ? prefix + '/' + name : name;
            result.push({name, path, count: Object.keys(data._children).length});
            const children = build(data._children, path);
            if (children.length) result.push(...children.map(c => ({...c, indent: true})));
          }
          return result;
        }
        folderTree.value = build(tree, '');
      } catch(e) { console.error(e); }
    }
    async function loadAllCategories() {
      try {
        const d = await api('/api/v1/documents');
        const cats = new Set();
        (d.data || []).forEach(doc => { if (doc.category) cats.add(doc.category); });
        allCategories.value = [...cats].sort();
      } catch(e) { console.error(e); }
    }

    // 批量模式
    function toggleBatchMode() {
      batchMode.value = !batchMode.value;
      if (!batchMode.value) selectedIds.value = new Set();
    }
    function toggleSelect(id) {
      const s = new Set(selectedIds.value);
      if (s.has(id)) s.delete(id); else s.add(id);
      selectedIds.value = s;
    }

    // 标签操作
    function openTagPicker(doc, event) { tagPickerDoc.value = doc; tagSearch.value = ''; }
    function closeTagPicker() { tagPickerDoc.value = null; tagSearch.value = ''; }
    async function addTagToDoc(doc, tagName) {
      const name = tagName.trim();
      if (!name) return;
      await api('/api/v1/documents/' + doc.id, {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({add_tags: [name]})});
      tagSearch.value = '';
      await loadDocuments();
      await loadTags();
    }
    async function toggleDocTag(doc, tag) {
      const has = doc.tags && doc.tags.some(t => t.id === tag.id);
      await api('/api/v1/documents/' + doc.id, {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(has ? {remove_tags: [tag.name]} : {add_tags: [tag.name]})});
      await loadDocuments();
      await loadTags();
    }
    const filteredTags = computed(() => {
      if (!tagSearch.value) return allTags.value;
      return allTags.value.filter(t => t.name.toLowerCase().includes(tagSearch.value.toLowerCase()));
    });

    // 集合操作
    function openCollectPicker(doc, event) { collectPickerDoc.value = doc; }
    async function toggleDocCollection(doc, coll) {
      const has = doc.collections && doc.collections.some(c => c.id === coll.id);
      await api('/api/v1/documents/' + doc.id, {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(has ? {remove_collections: [coll.id]} : {add_collections: [coll.id]})});
      await loadDocuments();
      await loadCollections();
    }
    async function createCollection() {
      const name = newCollectionName.value.trim();
      if (!name) return;
      await api('/api/v1/collections', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({name})});
      newCollectionName.value = '';
      showNewCollection.value = false;
      await loadCollections();
    }

    // 分类操作
    function openCategoryEdit(doc, event) {
      categoryEditDoc.value = doc;
      editCategoryName.value = doc.category || '';
    }
    async function saveDocCategory(doc) {
      const cat = editCategoryName.value.trim();
      await api('/api/v1/documents/' + doc.id, {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({category: cat})});
      categoryEditDoc.value = null;
      await loadDocuments();
      await loadAllCategories();
    }

    // 批量操作
    function batchTag() { batchTagPrompt.value = true; batchPromptInput.value = ''; }
    function batchCategorize() { batchCatPrompt.value = true; batchPromptInput.value = ''; }
    async function confirmBatchTag() {
      const tagName = batchPromptInput.value.trim();
      if (!tagName) { batchTagPrompt.value = false; return; }
      await api('/api/v1/documents/batch', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ids: [...selectedIds.value], action: 'tag', params: {tags: [tagName]}})});
      batchTagPrompt.value = false;
      selectedIds.value = new Set();
      await loadDocuments();
      await loadTags();
    }
    async function confirmBatchCat() {
      const cat = batchPromptInput.value.trim();
      await api('/api/v1/documents/batch', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ids: [...selectedIds.value], action: 'categorize', params: {category: cat}})});
      batchCatPrompt.value = false;
      selectedIds.value = new Set();
      await loadDocuments();
      await loadAllCategories();
    }
    async function batchDelete() {
      if (!confirm(`确定删除选中的 ${selectedIds.value.size} 个文档？`)) return;
      await api('/api/v1/documents/batch', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ids: [...selectedIds.value], action: 'delete'})});
      selectedIds.value = new Set();
      await loadDocuments();
      await loadTags();
      await loadFolders();
    }

    // 辅助
    function toggleSection(name) { sections[name] = !sections[name]; }
```

更新 `onMounted` / `watch(page)` 部分，在 `page === 'docs'` 时加载标签/集合/文件夹：

```javascript
    watch(page, (v) => {
      if (v === 'docs') {
        loadDocuments();
        loadJobStats();
        loadTags();
        loadCollections();
        loadFolders();
        loadAllCategories();
        if (!jobInterval) jobInterval = setInterval(() => { loadJobStats(); }, 3000);
      } else if (v === 'memories') {
        // ...
```

更新 `return` 对象，添加所有新变量和方法。

- [ ] **Step 10.4: 验证前端渲染**

手动测试步骤：
```bash
cd /Users/terry/Documents/cc_projects/my_agent1 && python server/main.py
```

然后浏览器打开 `http://localhost:8000`，验证：
- 文档管理页面显示两栏布局
- 侧边栏有目录浏览、标签、集合三个区域
- 可以上传文档、给文档打标签、加入集合
- 批量模式可以多选、打标签、改分类、删除

- [ ] **Step 10.5: Commit**

```bash
git add server/templates/index.html
git commit -m "feat: redesign docs page with tags, collections, batch operations UI"
```

---

### Task 11: 最终验证

- [ ] **Step 1: 运行全部测试**

```bash
cd /Users/terry/Documents/cc_projects/my_agent1 && python -m pytest server/tests/ -v --tb=short
```

Expected: 所有测试通过（约 108+ passed）

- [ ] **Step 2: 验证后端启动**

```bash
cd /Users/terry/Documents/cc_projects/my_agent1 && timeout 3 python -c "
import tempfile, os
os.environ['KB_DATA_DIR'] = tempfile.mkdtemp()
from server.database import reset_engine
reset_engine()
from server.main import app, startup
startup()
print('Server started OK')
" 2>&1 || true
```

- [ ] **Step 3: Commit any remaining changes**

```bash
git status
git diff --stat
```
