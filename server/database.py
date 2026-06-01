"""数据库连接管理。"""

import os
from contextlib import contextmanager
from pathlib import Path
import sqlalchemy as sa
from sqlalchemy import create_engine, Engine
from sqlalchemy.orm import Session, sessionmaker

DATA_DIR = Path(os.environ.get("KB_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
(DATA_DIR / "files").mkdir(exist_ok=True)
(DATA_DIR / "chroma").mkdir(exist_ok=True)

_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        db_path = DATA_DIR / "app.db"
        _engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
            echo=False,
        )
    return _engine


def get_session():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(autocommit=False, autoflush=False, expire_on_commit=False, bind=get_engine())
    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def get_session_ctx() -> Session:
    """上下文管理器版 session 获取，供非 FastAPI 代码（Worker、Config 等）使用。

    用法: with get_session_ctx() as session:
    """
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(autocommit=False, autoflush=False, expire_on_commit=False, bind=get_engine())
    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()


def reset_engine():
    """重置引擎和会话工厂（用于测试时切换 DATA_DIR）。"""
    global _engine, _SessionLocal
    _engine = None
    _SessionLocal = None


def init_db():
    """创建所有表，并执行迁移。显式导入所有模型确保 create_all 正确工作。"""
    from server.models.base import Base
    # 显式导入所有模型类，确保 SQLAlchemy 知道要创建哪些表
    from server.models.document import Document, DocumentChunk  # noqa: F401
    from server.models.conversation import Conversation, Message  # noqa: F401
    from server.models.job import Job  # noqa: F401
    from server.models.tag import Tag  # noqa: F401
    from server.models.collection import Collection  # noqa: F401
    from server.config import AppConfigModel  # noqa: F401
    Base.metadata.create_all(bind=get_engine())
    _migrate(get_engine())


def _table_exists(conn, name: str) -> bool:
    result = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,))
    return result.fetchone() is not None


def _migrate(engine):
    """增量迁移：为旧数据库补齐缺失的列，并创建新表。"""
    conn = engine.raw_connection()
    try:
        # 先检查 documents 表是否存在，避免首次部署时 ALTER TABLE 崩溃
        if _table_exists(conn, "documents"):
            cols = {r[1] for r in conn.execute("PRAGMA table_info(documents)")}
            if "elapsed_ms" not in cols:
                conn.execute("ALTER TABLE documents ADD COLUMN elapsed_ms INTEGER DEFAULT 0")
                conn.commit()
            if "checksum" not in cols:
                conn.execute("ALTER TABLE documents ADD COLUMN checksum VARCHAR(64)")
                conn.commit()
            if "folder_path" not in cols:
                conn.execute("ALTER TABLE documents ADD COLUMN folder_path TEXT DEFAULT ''")
                conn.commit()
            if "category" not in cols:
                conn.execute("ALTER TABLE documents ADD COLUMN category VARCHAR(100) DEFAULT ''")
                conn.commit()

        # 确保 jobs 表存在（新表或旧数据库迁移）
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id VARCHAR(36) PRIMARY KEY,
                document_id VARCHAR(36) NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                job_type VARCHAR(20) NOT NULL,
                priority INTEGER DEFAULT 5,
                status VARCHAR(20) DEFAULT 'pending',
                progress INTEGER DEFAULT 0,
                error_message TEXT,
                started_at TIMESTAMP,
                finished_at TIMESTAMP,
                created_at TIMESTAMP NOT NULL
            )
        """)
        conn.commit()

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

        # v3 迁移：FTS5 全文索引
        ensure_fts5_table()
    finally:
        conn.close()


FTS5_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    chunk_id,
    content,
    document_title,
    tokenize='unicode61'
)
"""


def ensure_fts5_table():
    """创建 FTS5 全文索引虚拟表（若不存在）。使用 SQLAlchemy 连接池。"""
    with get_engine().connect() as conn:
        conn.execute(sa.text(FTS5_DDL))
        conn.commit()


def _fts_execute(sql: str, params: dict | None = None) -> None:
    """执行 FTS5 DML 语句（使用 SQLAlchemy engine 连接池）。"""
    with get_engine().connect() as conn:
        conn.execute(sa.text(sql), params or {})
        conn.commit()


def fts_insert(chunk_id: str, content: str, title: str) -> None:
    """向 FTS5 索引写入一条 chunk。"""
    _fts_execute(
        "INSERT INTO chunks_fts(chunk_id, content, document_title) VALUES (:cid, :text, :title)",
        {"cid": chunk_id, "text": content, "title": title},
    )



def fts_delete_by_document_id(document_id: str) -> None:
    """从 FTS5 索引删除某文档的所有 chunk。"""
    from server.models.document import DocumentChunk
    tbl = DocumentChunk.__tablename__
    _fts_execute(
        f"DELETE FROM chunks_fts WHERE chunk_id IN (SELECT id FROM {tbl} WHERE document_id = :did)",
        {"did": document_id},
    )


