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
    """增量迁移：为旧数据库补齐缺失的列。"""
    import sqlite3
    db_path = str(engine.url).replace("sqlite:///", "")
    conn = sqlite3.connect(db_path)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(documents)")}
        if "elapsed_ms" not in cols:
            conn.execute("ALTER TABLE documents ADD COLUMN elapsed_ms INTEGER DEFAULT 0")
            conn.commit()
    finally:
        conn.close()
