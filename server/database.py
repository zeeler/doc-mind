"""数据库连接管理。"""

import os
from pathlib import Path
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


def reset_engine():
    """重置引擎和会话工厂（用于测试时切换 DATA_DIR）。"""
    global _engine, _SessionLocal
    _engine = None
    _SessionLocal = None


def init_db():
    """创建所有表，并执行迁移。在模型导入后调用。"""
    from server.models.base import Base
    Base.metadata.create_all(bind=get_engine())
    _migrate(get_engine())


def _migrate(engine):
    """增量迁移：为旧数据库补齐缺失的列，并创建新表。"""
    import sqlite3
    db_path = str(engine.url).replace("sqlite:///", "")
    conn = sqlite3.connect(db_path)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(documents)")}
        if "elapsed_ms" not in cols:
            conn.execute("ALTER TABLE documents ADD COLUMN elapsed_ms INTEGER DEFAULT 0")
            conn.commit()
        if "checksum" not in cols:
            conn.execute("ALTER TABLE documents ADD COLUMN checksum VARCHAR(64)")
            conn.commit()

        # 确保 jobs 表存在（新表或旧数据库迁移）
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id VARCHAR(36) PRIMARY KEY,
                document_id VARCHAR(36) NOT NULL,
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

        cols2 = {r[1] for r in conn.execute("PRAGMA table_info(documents)")}
        if "folder_path" not in cols2:
            conn.execute("ALTER TABLE documents ADD COLUMN folder_path TEXT DEFAULT ''")
            conn.commit()
        if "category" not in cols2:
            conn.execute("ALTER TABLE documents ADD COLUMN category VARCHAR(100) DEFAULT ''")
            conn.commit()

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
    finally:
        conn.close()


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
