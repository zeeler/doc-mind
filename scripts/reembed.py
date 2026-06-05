"""仅重建 ChromaDB 向量 — 从已有 chunks 生成 embedding，跳过解析和切块。"""
import sys
import logging
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("reembed")


def main():
    from server.database import get_session_ctx, init_db, DATA_DIR, fts_insert
    from server.models.document import Document, DocumentChunk
    from server.vector.store import VectorStore
    from server.config import AppConfig, has_embedding_model
    from server.services.embedder import Embedder

    init_db()
    config = AppConfig().get_all()

    if not has_embedding_model(config):
        logger.error("未配置外部 embedding 模型，无法重建向量")
        return

    embedder = Embedder(config)
    logger.info("Embedding 模型已初始化: %s", config.get("embedding_model", "?"))

    with get_session_ctx() as session:
        docs = session.query(Document).filter(
            Document.status == "done"
        ).all()

    if not docs:
        logger.info("没有已处理的文档")
        return

    store = VectorStore(persist_dir=str(DATA_DIR / "chroma"))
    total_chunks = 0
    batch_size = 256  # 批量生成 embedding

    for doc_idx, doc in enumerate(docs, 1):
        with get_session_ctx() as session:
            chunks = session.query(DocumentChunk).filter(
                DocumentChunk.document_id == doc.id
            ).order_by(DocumentChunk.chunk_no).all()

        if not chunks:
            logger.info(f"[{doc_idx}/{len(docs)}] 跳过 (无chunk): {doc.title}")
            continue

        # 清除旧向量
        try:
            store.delete_by_document_id(doc.id)
        except Exception as e:
            logger.warning(f"  清除旧向量失败: {e}")

        # 批量生成 embedding 并写入
        success = 0
        texts = [c.content for c in chunks]

        for batch_start in range(0, len(texts), batch_size):
            batch_texts = texts[batch_start:batch_start + batch_size]
            batch_chunks = chunks[batch_start:batch_start + batch_size]

            try:
                embeddings = embedder.embed(batch_texts)
                ids = [c.id for c in batch_chunks]
                metadatas = [{
                    "document_id": doc.id,
                    "title": doc.title,
                    "file_name": doc.file_name,
                    "chunk_no": c.chunk_no,
                } for c in batch_chunks]

                store.add(ids=ids, texts=batch_texts, embeddings=embeddings, metadatas=metadatas)
                success += len(batch_chunks)
            except Exception as e:
                logger.error(f"  batch {batch_start}-{batch_start+batch_size} 失败: {e}")

        total_chunks += success
        logger.info(f"[{doc_idx}/{len(docs)}] {doc.title}: {success}/{len(chunks)} chunks ✓")

    logger.info(f"重建完成: {total_chunks} 个向量已写入 ChromaDB")


if __name__ == "__main__":
    main()
