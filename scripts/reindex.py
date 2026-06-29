"""重新索引所有文档 — 清除旧索引后用新的结构感知分块重新处理。"""

import sys
import logging
from pathlib import Path

# 确保项目根目录在 sys.path 中
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("reindex")


def main():
    from server.database import get_session_ctx, init_db, fts_delete_by_document_id
    from server.models.document import Document, DocumentChunk
    from server.vector.store import VectorStore
    from server.config import AppConfig
    from server.services.pipeline import index_document

    # 初始化数据库
    init_db()
    config = AppConfig().get_all()

    # 查询所有已处理的文档
    with get_session_ctx() as session:
        docs = session.query(Document).filter(
            Document.status.in_(["done", "failed"])
        ).all()

    if not docs:
        logger.info("没有需要重新索引的文档")
        return

    logger.info(f"找到 {len(docs)} 篇文档需要重新索引")

    store = VectorStore(persist_dir=str(Path("data") / "chroma"))

    success = 0
    for i, doc in enumerate(docs, 1):
        logger.info(f"[{i}/{len(docs)}] 重新索引: {doc.title} ({doc.file_name})")

        try:
            # 1. 清除 ChromaDB
            try:
                store.delete_by_document_id(doc.id)
            except Exception as e:
                logger.warning(f"  清除 ChromaDB 失败: {e}")

            # 2. 清除 FTS5 + chunks（通过 session）
            with get_session_ctx() as session:
                doc_ref = session.get(Document, doc.id)
                if doc_ref:
                    # 删除 chunks（SQLAlchemy cascade 处理）
                    session.query(DocumentChunk).filter(
                        DocumentChunk.document_id == doc.id
                    ).delete()
                    session.commit()

                try:
                    fts_delete_by_document_id(doc.id)
                except Exception as e:
                    logger.warning(f"  清除 FTS5 失败: {e}")

            # 3. 读取文本后重新索引（优先从 .md 备份读取）
            try:
                md_path = Path(doc.file_path).with_suffix(".md")
                if md_path.exists():
                    text = md_path.read_text(encoding="utf-8")
                else:
                    logger.warning(f"  无 .md 备份，跳过: {doc.title}")
                    continue
                index_document(doc.id, text, config)
                success += 1
                logger.info(f"  ✓ 完成")
            except Exception as e:
                logger.error(f"  ✗ 处理失败: {e}")

        except Exception as e:
            logger.error(f"  ✗ 清理失败: {e}")

    logger.info(f"重新索引完成: {success}/{len(docs)} 成功")


if __name__ == "__main__":
    main()
