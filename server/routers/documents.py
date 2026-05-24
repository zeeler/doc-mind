"""文档管理路由。"""

import uuid
import shutil
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from sqlalchemy.orm import Session
from server.database import get_session, DATA_DIR
from server.models.document import Document, DocumentChunk
from server.services.parser import parse_file, SUPPORTED_TYPES
from server.services.chunker import chunk_text, estimate_tokens
from server.config import AppConfig

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])


@router.post("/upload")
async def upload_document(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")

    suffix = Path(file.filename).suffix.lower().lstrip(".")
    if suffix not in SUPPORTED_TYPES:
        raise HTTPException(status_code=400, detail=f"不支持的文件类型: {suffix}")

    doc_id = str(uuid.uuid4())
    file_dir = DATA_DIR / "files" / doc_id
    file_dir.mkdir(parents=True, exist_ok=True)
    file_path = file_dir / file.filename

    content = await file.read()
    file_path.write_bytes(content)

    doc = Document(
        id=doc_id,
        title=Path(file.filename).stem,
        file_name=file.filename,
        file_type=suffix,
        file_path=str(file_path),
        file_size=len(content),
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
