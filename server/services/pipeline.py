"""文档处理管道 — 解析 → 切块 → embedding → 写入 ChromaDB。"""

import time
import logging
from pathlib import Path
from server.database import DATA_DIR, get_session_ctx, fts_insert, fts_delete_by_document_id
from server.models.document import Document, DocumentChunk
from server.services.parser import parse_file
from server.services.chunker import chunk_text, estimate_tokens
from server.config import has_embedding_model
from server.services.embedder import Embedder
from server.vector.store import VectorStore

logger = logging.getLogger("knowledge-base")


def _init_embedder(config: dict) -> tuple[Embedder | None, bool]:
    """初始化外部 embedding 模型，返回 (embedder, 是否可用)。"""
    if not has_embedding_model(config):
        return None, False
    try:
        embedder = Embedder(config)
        embedder.embed(["test"])
        return embedder, True
    except Exception as e:
        logger.warning(f"外部 embedding 不可用，降级为内置: {e}")
        return None, False


def _index_chunks(
    session,
    doc: Document,
    chunks_text: list[str],
    config: dict,
) -> int:
    """将 chunks 写入 ChromaDB + FTS5，返回成功写入的 chunk 数。

    如果外部 embedding 在任一个 chunk 失败，会立即回退：
    先删掉前面已写入的 chunk（ChromaDB + SQLite），再用 ChromaDB 内置 embedding
    重新索引所有 chunk，确保同一文档的 chunks 不会分布在两个不同的向量空间中。
    """
    store = VectorStore(persist_dir=str(DATA_DIR / "chroma"))
    embedder, use_external = _init_embedder(config)

    result = _try_index_chunks(session, doc, chunks_text, config, store, embedder, use_external)
    if result is not None:
        return result

    # 外部 embedding 中途失败 → 回滚已写入的 chunk，全部用内置 embedding 重新索引
    logger.warning(
        f"外部 embedding 失败，回退全部 {len(chunks_text)} 个 chunk 到内置 embedding"
    )
    _rollback_chunks(session, doc.id, store)
    return _try_index_chunks(session, doc, chunks_text, config, store, None, False)


def _try_index_chunks(
    session,
    doc: Document,
    chunks_text: list[str],
    config: dict,
    store,
    embedder,
    use_external: bool,
) -> int | None:
    """尝试索引所有 chunk，返回写入数；外部 embedding 中途失败返回 None。"""
    for i, chunk_content in enumerate(chunks_text):
        chunk = DocumentChunk(
            document_id=doc.id,
            chunk_no=i + 1,
            content=chunk_content,
            token_count=estimate_tokens(chunk_content),
            metadata_json={},
        )
        session.add(chunk)

        metadata = {
            "document_id": doc.id,
            "title": doc.title,
            "file_name": doc.file_name,
            "chunk_no": i + 1,
        }

        if use_external and embedder is not None:
            try:
                embedding = embedder.embed([chunk_content])[0]
                store.add(
                    ids=[chunk.id], texts=[chunk_content],
                    embeddings=[embedding], metadatas=[metadata],
                )
                _safe_fts_insert(chunk.id, chunk_content, doc.title)
                continue
            except Exception as e:
                logger.warning(f"外部 embedding 失败 chunk {i+1}/{len(chunks_text)}: {e}")
                return None  # 触发调用方回滚重试

        # 内置 embedding
        store.add(
            ids=[chunk.id], texts=[chunk_content], metadatas=[metadata],
        )
        _safe_fts_insert(chunk.id, chunk_content, doc.title)

    return len(chunks_text)


def _rollback_chunks(session, doc_id: str, store) -> None:
    """删除已写入的所有 chunk（SQLite + ChromaDB + FTS5）。"""
    from server.models.document import DocumentChunk

    # 收集已写入的 chunk ID（从 ChromaDB）
    try:
        existing = store.collection.get(where={"document_id": doc_id})
        if existing and existing.get("ids"):
            store.collection.delete(ids=existing["ids"])
    except Exception as e:
        logger.warning(f"回滚 ChromaDB 失败 doc {doc_id}: {e}")

    # 从 SQLite 删除
    session.query(DocumentChunk).filter(DocumentChunk.document_id == doc_id).delete()
    session.flush()

    # 清除 FTS5 索引
    _clear_old_index(doc_id)


def _safe_fts_insert(chunk_id: str, content: str, title: str) -> None:
    """写入 FTS5 索引，失败时仅警告不中断流程。"""
    try:
        fts_insert(chunk_id, content, title)
    except Exception as e:
        logger.warning(f"FTS5 索引写入失败 chunk {chunk_id}: {e}")


def _clear_old_index(doc_id: str) -> None:
    """清除文档旧的 FTS5 索引（失败时警告）。"""
    try:
        fts_delete_by_document_id(doc_id)
    except Exception as e:
        logger.warning(f"FTS5 清除旧索引失败 doc {doc_id}: {e}")


def process_document(doc_id: str, config: dict) -> None:
    """完整文档处理流程：解析 → 切块 → embedding → 写入 ChromaDB。

    顺序调整：先成功索引新数据，再清除旧索引，避免处理失败后数据丢失。
    """
    t_start = time.time()

    with get_session_ctx() as session:
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
        section_chunk_size = chunk_size * 2
        chunks_text = chunk_text(
            text,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            section_chunk_size=section_chunk_size,
        )

        doc.status = "indexing"
        session.commit()

        # 先索引新数据，成功后再清除旧索引
        _index_chunks(session, doc, chunks_text, config)

        # 索引成功后清除旧 FTS5 记录（避免处理失败导致数据丢失）
        _clear_old_index(doc_id)

        doc.status = "done"
        doc.chunk_count = len(chunks_text)
        doc.elapsed_ms = int((time.time() - t_start) * 1000)
        title = doc.title
        session.commit()

    logger.info(f"文档处理完成: {title} ({len(chunks_text)} chunks, {doc.elapsed_ms}ms)")


def index_document(doc_id: str, text: str, config: dict) -> None:
    """仅执行切块 → embedding → 写入 ChromaDB，不包含解析。供 Worker 调用。"""
    with get_session_ctx() as session:
        doc = session.get(Document, doc_id)
        if not doc:
            return

        chunk_size = int(config.get("chunk_size", "800"))
        chunk_overlap = int(config.get("chunk_overlap", "100"))
        section_chunk_size = chunk_size * 2
        chunks_text = chunk_text(
            text,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            section_chunk_size=section_chunk_size,
        )

        if not chunks_text:
            return

        _index_chunks(session, doc, chunks_text, config)

        doc.chunk_count = len(chunks_text)
        session.commit()
