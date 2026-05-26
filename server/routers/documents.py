"""文档管理路由。"""

import uuid
import hashlib
import shutil
import logging
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from sqlalchemy.orm import Session
from server.database import get_session, DATA_DIR
from server.models.document import Document, DocumentChunk
from server.services.parser import parse_file, SUPPORTED_TYPES
from server.services.chunker import chunk_text, estimate_tokens
from server.services.worker import create_jobs_for_document
from server.config import AppConfig

logger = logging.getLogger("knowledge-base")

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])


def _compute_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@router.post("/upload")
async def upload_document(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")

    suffix = Path(file.filename).suffix.lower().lstrip(".")
    if suffix not in SUPPORTED_TYPES:
        raise HTTPException(status_code=400, detail=f"不支持的文件类型: {suffix}")

    content = await file.read()
    checksum = _compute_sha256(content)

    # === 去重检查 ===
    with next(get_session()) as session:
        existing = session.query(Document).filter(Document.checksum == checksum).first()
    if existing:
        md_path = Path(existing.file_path).parent / "index.md"
        file_dir = Path(existing.file_path).parent

        if md_path.exists() and existing.status == "done":
            # 源文件和 markdown 都已存在，无需重复上传
            return {
                "code": "OK",
                "message": "success",
                "data": {
                    "id": existing.id,
                    "title": existing.title,
                    "file_name": existing.file_name,
                    "file_type": existing.file_type,
                    "status": existing.status,
                    "duplicate": True,
                },
            }
        else:
            # 源文件相同但缺 markdown，重新触发解析
            if existing.status in ("done", "failed"):
                sid = existing.id
                with next(get_session()) as s:
                    doc = s.get(Document, sid)
                    if doc:
                        doc.status = "pending"
                        s.commit()

            create_jobs_for_document(existing.id)
            logger.info(f"去重: 源文件已存在 {existing.title}，重新触发解析")
            return {
                "code": "OK",
                "message": "success",
                "data": {
                    "id": existing.id,
                    "title": existing.title,
                    "file_name": existing.file_name,
                    "file_type": existing.file_type,
                    "status": "pending",
                    "reprocess": True,
                },
            }

    # === 新文件 ===
    doc_id = str(uuid.uuid4())
    file_dir = DATA_DIR / "files" / doc_id
    file_dir.mkdir(parents=True, exist_ok=True)
    file_path = file_dir / file.filename
    file_path.write_bytes(content)

    doc = Document(
        id=doc_id,
        title=Path(file.filename).stem,
        file_name=file.filename,
        file_type=suffix,
        file_path=str(file_path),
        file_size=len(content),
        checksum=checksum,
        status="pending",
    )

    with next(get_session()) as session:
        session.add(doc)
        session.commit()
        session.refresh(doc)

        # 处理文档（解析→切块→embedding→索引）
        try:
            from server.services.pipeline import process_document
            from server.config import AppConfig
            process_document(doc_id, AppConfig().get_all())
            session.refresh(doc)
        except Exception as e:
            logger.error(f"文档处理失败 {doc.title} ({doc_id}): {e}", exc_info=True)
            doc.status = "failed"
            session.commit()

        return {
            "code": "OK",
            "message": "success",
            "data": {
                "id": doc.id,
                "title": doc.title,
                "file_name": doc.file_name,
                "file_type": doc.file_type,
                "status": doc.status,
            },
        }


@router.get("")
def list_documents(skip: int = 0, limit: int = 50, session: Session = Depends(get_session)):
    docs = session.query(Document).order_by(Document.created_at.desc()).offset(skip).limit(limit).all()
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
                "created_at": d.created_at.isoformat(),
            }
            for d in docs
        ],
    }


@router.get("/{doc_id}")
def get_document(doc_id: str, session: Session = Depends(get_session)):
    doc = session.get(Document, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    chunks = session.query(DocumentChunk).filter(DocumentChunk.document_id == doc_id).order_by(DocumentChunk.chunk_no).all()
    return {
        "code": "OK",
        "message": "success",
        "data": {
            "id": doc.id,
            "title": doc.title,
            "file_name": doc.file_name,
            "file_type": doc.file_type,
            "file_size": doc.file_size,
            "status": doc.status,
            "chunk_count": doc.chunk_count,
            "created_at": doc.created_at.isoformat(),
            "chunks": [
                {"id": c.id, "chunk_no": c.chunk_no, "content": c.content[:200] + "..." if len(c.content) > 200 else c.content, "token_count": c.token_count}
                for c in chunks
            ],
        },
    }


@router.delete("/{doc_id}")
def delete_document(doc_id: str, session: Session = Depends(get_session)):
    doc = session.get(Document, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    session.delete(doc)
    session.commit()
    file_dir = DATA_DIR / "files" / doc_id
    if file_dir.exists():
        shutil.rmtree(file_dir)
    return {"code": "OK", "message": "success", "data": None}
