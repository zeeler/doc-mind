"""文档处理管道 — 解析 → 切块 → embedding → 写入 ChromaDB。"""

import time
import logging
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from server.database import DATA_DIR, get_session
from server.models.document import Document, DocumentChunk
from server.services.parser import parse_file
from server.services.chunker import chunk_text, estimate_tokens
from server.services.embedder import Embedder
from server.vector.store import VectorStore

logger = logging.getLogger("knowledge-base")


def process_document(doc_id: str, config: dict) -> None:
    t_start = time.time()

    with next(get_session()) as session:
        doc = session.get(Document, doc_id)
        if not doc:
            return

        doc.status = "parsing"
        session.commit()

        try:
            text = parse_file(doc.file_path, config)
        except Exception as e:
            logger.error(f"文档解析失败 {doc.title}: {e}", exc_info=True)
            doc.status = "failed"
            session.commit()
            raise

        # 保存 Markdown 备份（扫描件 OCR 结果）
        if doc.file_type == "pdf" and text:
            md_path = Path(doc.file_path).with_suffix(".md")
            try:
                md_path.write_text(f"# {doc.title}\n\n{text}", encoding="utf-8")
            except Exception as e:
                logger.warning(f"Markdown 备份写入失败 {doc.title}: {e}")

        doc.status = "chunking"
        session.commit()

        chunk_size = int(config.get("chunk_size", "800"))
        chunk_overlap = int(config.get("chunk_overlap", "100"))
        chunks_text = chunk_text(text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)

        doc.status = "indexing"
        session.commit()

        store = VectorStore(persist_dir=str(DATA_DIR / "chroma"))

        use_external_embedding = False
        if config.get("custom_embedding_model", "") or config.get("openai_embedding_model", "") or config.get("mlx_embedding_model", ""):
            try:
                embedder = Embedder(config)
                _ = embedder.embed(["test"])
                use_external_embedding = True
            except Exception as e:
                logger.warning(f"外部 embedding 不可用，降级为内置: {e}")
                use_external_embedding = False

        for i, chunk_content in enumerate(chunks_text):
            chunk = DocumentChunk(
                document_id=doc_id,
                chunk_no=i + 1,
                content=chunk_content,
                token_count=estimate_tokens(chunk_content),
                metadata_json={},
            )
            session.add(chunk)

            if use_external_embedding:
                try:
                    embedding = embedder.embed([chunk_content])[0]
                    store.add(
                        ids=[chunk.id],
                        texts=[chunk_content],
                        embeddings=[embedding],
                        metadatas=[{
                            "document_id": doc_id,
                            "title": doc.title,
                            "file_name": doc.file_name,
                            "chunk_no": i + 1,
                        }],
                    )
                    continue
                except Exception as e:
                    logger.warning(f"外部 embedding 失败 chunk {i+1}，降级为内置: {e}")

            store.add(
                ids=[chunk.id],
                texts=[chunk_content],
                metadatas=[{
                    "document_id": doc_id,
                    "title": doc.title,
                    "file_name": doc.file_name,
                    "chunk_no": i + 1,
                }],
            )

        doc.status = "done"
        doc.chunk_count = len(chunks_text)
        doc.elapsed_ms = int((time.time() - t_start) * 1000)
        title = doc.title
        session.commit()

    logger.info(f"文档处理完成: {title} ({len(chunks_text)} chunks, {doc.elapsed_ms}ms)")


def index_document(doc_id: str, text: str, config: dict) -> None:
    """仅执行切块→embedding→写入 ChromaDB，不包含解析。供 Worker 调用。"""
    from server.database import DATA_DIR, get_session
    from server.models.document import Document, DocumentChunk
    from server.services.chunker import chunk_text, estimate_tokens
    from server.services.embedder import Embedder
    from server.vector.store import VectorStore

    with next(get_session()) as session:
        doc = session.get(Document, doc_id)
        if not doc:
            return

        chunk_size = int(config.get("chunk_size", "800"))
        chunk_overlap = int(config.get("chunk_overlap", "100"))
        chunks_text = chunk_text(text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)

        if not chunks_text:
            return

        store = VectorStore(persist_dir=str(DATA_DIR / "chroma"))

        use_external = False
        if config.get("custom_embedding_model") or config.get("openai_embedding_model") or config.get("mlx_embedding_model"):
            try:
                embedder = Embedder(config)
                _ = embedder.embed(["test"])
                use_external = True
            except Exception:
                pass

        for i, chunk_content in enumerate(chunks_text):
            chunk = DocumentChunk(
                document_id=doc_id, chunk_no=i + 1, content=chunk_content,
                token_count=estimate_tokens(chunk_content), metadata_json={},
            )
            session.add(chunk)
            if use_external:
                try:
                    embedding = embedder.embed([chunk_content])[0]
                    store.add(ids=[chunk.id], texts=[chunk_content], embeddings=[embedding],
                              metadatas=[{"document_id": doc_id, "title": doc.title, "file_name": doc.file_name, "chunk_no": i + 1}])
                    continue
                except Exception:
                    pass
            store.add(ids=[chunk.id], texts=[chunk_content],
                      metadatas=[{"document_id": doc_id, "title": doc.title, "file_name": doc.file_name, "chunk_no": i + 1}])

        doc.chunk_count = len(chunks_text)
        session.commit()
