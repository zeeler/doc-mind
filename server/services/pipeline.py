"""文档处理管道 — 解析 → 切块 → embedding → 写入 ChromaDB。"""

from server.database import DATA_DIR, get_session
from server.models.document import Document, DocumentChunk
from server.services.parser import parse_file
from server.services.chunker import chunk_text, estimate_tokens
from server.services.embedder import Embedder
from server.vector.store import VectorStore


def process_document(doc_id: str, config: dict) -> None:
    with next(get_session()) as session:
        doc = session.get(Document, doc_id)
        if not doc:
            return

        doc.status = "parsing"
        session.commit()

        try:
            text = parse_file(doc.file_path)
        except Exception:
            doc.status = "failed"
            session.commit()
            raise

        doc.status = "chunking"
        session.commit()

        chunk_size = int(config.get("chunk_size", "800"))
        chunk_overlap = int(config.get("chunk_overlap", "100"))
        chunks_text = chunk_text(text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)

        doc.status = "indexing"
        session.commit()

        store = VectorStore(persist_dir=str(DATA_DIR / "chroma"))

        # 尝试获取外部 embedding，失败则降级为 ChromaDB 内置 embedding
        use_external_embedding = False
        if config.get("custom_embedding_model", "") or config.get("openai_embedding_model", "") or config.get("mlx_embedding_model", ""):
            try:
                embedder = Embedder(config)
                _ = embedder.embed(["test"])
                use_external_embedding = True
            except Exception:
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
                except Exception:
                    pass

            # 降级：使用 ChromaDB 内置 embedding
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
        session.commit()
