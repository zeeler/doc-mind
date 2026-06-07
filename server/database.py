"""数据库连接管理。"""

import os
import re
from contextlib import contextmanager
from pathlib import Path
import sqlalchemy as sa
from sqlalchemy import create_engine, Engine, event
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

        @event.listens_for(_engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, connection_record):
            """每次连接时启用外键约束（SQLite 默认关闭）。"""
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.close()

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

        # 确保 document_chunks 表也存在（首次部署时由 create_all 创建，但 migrate 确保旧库有新字段）
        conn.execute("""
            CREATE TABLE IF NOT EXISTS document_chunks (
                id VARCHAR(36) PRIMARY KEY,
                document_id VARCHAR(36) NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                chunk_no INTEGER NOT NULL,
                content TEXT NOT NULL,
                token_count INTEGER DEFAULT 0,
                metadata_json JSON
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
        conn.commit()

        # v3 迁移：FTS5 全文索引
        ensure_fts5_table()

        # v4 迁移：FTS5 CJK 空格分隔（使每个 CJK 字符成为独立 token）
        ver = conn.execute("PRAGMA user_version").fetchone()[0]
        if ver < 4:
            conn.execute("DELETE FROM chunks_fts")
            conn.commit()
            n = fts_rebuild_all()
            conn.execute("PRAGMA user_version = 4")
            conn.commit()
            print(f"[kb_migrate] FTS5 CJK 索引重建完成: {n} 条 chunk", flush=True)
    finally:
        conn.close()


FTS5_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    chunk_id,
    content,
    document_title
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


CJK_CHARS_RE = re.compile(r'(?<=[一-鿿＀-￯])(?=[一-鿿＀-￯])')


def space_cjk(text: str) -> str:
    """在连续 CJK 字符之间插入空格，让 FTS5 unicode61 将每个字符视为独立 token。

    例如:  "哈佛谈判心理学" → "哈 佛 谈 判 心 理 学"
    """
    return CJK_CHARS_RE.sub(' ', text)


def fts_insert(chunk_id: str, content: str, title: str) -> None:
    """向 FTS5 索引写入一条 chunk（自动 CJK 字符间插空格）。"""
    _fts_execute(
        "INSERT INTO chunks_fts(chunk_id, content, document_title) VALUES (:cid, :c, :t)",
        {"cid": chunk_id, "c": space_cjk(content), "t": space_cjk(title)},
    )


def fts_rebuild_all() -> int:
    """从 document_chunks 重建整个 FTS5 索引。返回索引行数。"""
    with get_engine().connect() as conn:
        conn.execute(sa.text("DELETE FROM chunks_fts"))
        rows = conn.execute(sa.text(
            "SELECT dc.id, dc.content, d.title FROM document_chunks dc JOIN documents d ON dc.document_id = d.id"
        )).fetchall()
        for chunk_id, content, title in rows:
            conn.execute(
                sa.text("INSERT INTO chunks_fts(chunk_id, content, document_title) VALUES (:cid, :c, :t)"),
                {"cid": chunk_id, "c": space_cjk(content), "t": space_cjk(title)},
            )
        conn.commit()
        return len(rows)


def fts_delete_by_document_id(document_id: str) -> None:
    """从 FTS5 索引删除某文档的所有 chunk。"""
    from server.models.document import DocumentChunk
    tbl = DocumentChunk.__tablename__
    _fts_execute(
        "DELETE FROM chunks_fts WHERE chunk_id IN ("
        f"SELECT id FROM {tbl} WHERE document_id = :did"
        ")",
        {"did": document_id},
    )


