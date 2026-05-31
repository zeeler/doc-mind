"""文档管理路由。"""

import uuid
import hashlib
import shutil
import logging
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session
from server.database import get_session, DATA_DIR, fts_delete_by_document_id
from server.models.document import Document, DocumentChunk
from server.models.tag import Tag, document_tags
from server.models.collection import Collection, collection_documents
from server.services.parser import parse_file, SUPPORTED_TYPES
from server.services.chunker import chunk_text, estimate_tokens
from server.services.worker import create_jobs_for_document
from server.config import AppConfig

logger = logging.getLogger("knowledge-base")

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])


def _compute_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@router.post("/upload")
async def upload_document(file: UploadFile = File(...), folder_path: str = Form("")):
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
        # 判断是否需要重新处理：无 chunk 或状态为 failed
        need_reprocess = existing.chunk_count == 0 or existing.status == "failed"
        new_status = existing.status

        if need_reprocess:
            sid = existing.id
            with next(get_session()) as s:
                doc = s.get(Document, sid)
                if doc and doc.status in ("done", "failed"):
                    doc.status = "pending"
                    s.commit()
                    new_status = "pending"
                elif doc:
                    new_status = doc.status
            create_jobs_for_document(sid)
            logger.info(f"去重: 已存在 {existing.title}，触发重新解析")

        return {
            "code": "OK",
            "message": "success",
            "data": {
                "id": existing.id,
                "title": existing.title,
                "file_name": existing.file_name,
                "file_type": existing.file_type,
                "status": new_status,
                "duplicate": True,
                "reprocess": need_reprocess,
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
        folder_path=folder_path,
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
def list_documents(
    skip: int = 0,
    limit: int = 50,
    folder: str | None = None,
    category: str | None = None,
    tag: str | None = None,
    collection: str | None = None,
    status: str | None = None,
    search: str | None = None,
    session: Session = Depends(get_session),
):
    q = session.query(Document)

    if folder is not None:
        q = q.filter(Document.folder_path == folder)
    if category is not None:
        q = q.filter(Document.category == category)
    if status is not None:
        q = q.filter(Document.status == status)
    if search is not None:
        q = q.filter(Document.title.ilike(f"%{search}%"))
    if tag is not None:
        q = q.join(Document.tags).filter(Tag.name == tag)
    if collection is not None:
        q = q.join(Document.collections).filter(Collection.id == collection)

    docs = q.order_by(Document.created_at.desc()).offset(skip).limit(limit).all()
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
                "folder_path": d.folder_path,
                "category": d.category,
                "tags": [{"id": t.id, "name": t.name} for t in d.tags],
                "collections": [{"id": c.id, "name": c.name} for c in d.collections],
                "created_at": d.created_at.isoformat(),
            }
            for d in docs
        ],
    }


@router.get("/folders")
def list_folders(session: Session = Depends(get_session)):
    rows = session.query(Document.folder_path).distinct().order_by(Document.folder_path).all()
    paths = [r[0] for r in rows]
    return {"code": "OK", "message": "success", "data": paths}


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
    try:
        fts_delete_by_document_id(doc_id)
    except Exception:
        pass
    session.delete(doc)
    session.commit()
    file_dir = DATA_DIR / "files" / doc_id
    if file_dir.exists():
        shutil.rmtree(file_dir)
    return {"code": "OK", "message": "success", "data": None}


@router.put("/{doc_id}")
def update_document(doc_id: str, payload: dict, session: Session = Depends(get_session)):
    doc = session.get(Document, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")

    if "category" in payload:
        doc.category = (payload["category"] or "").strip()

    for tag_name in payload.get("add_tags") or []:
        name = tag_name.strip()
        if not name:
            continue
        normalized = name.lower()
        tag_obj = session.query(Tag).filter(func.lower(Tag.name) == normalized).first()
        if not tag_obj:
            tag_obj = Tag(id=str(uuid.uuid4()), name=name)
            session.add(tag_obj)
            session.flush()
        if tag_obj not in doc.tags:
            doc.tags.append(tag_obj)

    for tag_name in payload.get("remove_tags") or []:
        normalized = tag_name.strip().lower()
        tag_obj = session.query(Tag).filter(func.lower(Tag.name) == normalized).first()
        if tag_obj and tag_obj in doc.tags:
            doc.tags.remove(tag_obj)

    for coll_id in payload.get("add_collections") or []:
        coll = session.get(Collection, coll_id)
        if coll and coll not in doc.collections:
            doc.collections.append(coll)

    for coll_id in payload.get("remove_collections") or []:
        coll = session.get(Collection, coll_id)
        if coll and coll in doc.collections:
            doc.collections.remove(coll)

    session.commit()
    return {"code": "OK", "message": "success", "data": None}


@router.post("/batch")
def batch_operation(payload: dict, session: Session = Depends(get_session)):
    ids = payload.get("ids") or []
    action = payload.get("action", "")
    params = payload.get("params") or {}

    if not ids:
        raise HTTPException(status_code=400, detail="ids 不能为空")

    valid_actions = {"delete", "retry", "tag", "untag", "categorize", "collect"}
    if action not in valid_actions:
        raise HTTPException(status_code=400, detail=f"不支持的操作类型: {action}")

    results = []
    for doc_id in ids:
        try:
            doc = session.get(Document, doc_id)
            if not doc:
                results.append({"id": doc_id, "success": False, "error": "文档不存在"})
                continue

            if action == "delete":
                try:
                    fts_delete_by_document_id(doc_id)
                except Exception:
                    pass
                session.delete(doc)
            elif action == "categorize":
                doc.category = (params.get("category") or "").strip()
            elif action == "tag":
                for tag_name in params.get("tags") or []:
                    name = tag_name.strip()
                    if not name:
                        continue
                    normalized = name.lower()
                    tag_obj = session.query(Tag).filter(func.lower(Tag.name) == normalized).first()
                    if not tag_obj:
                        tag_obj = Tag(id=str(uuid.uuid4()), name=name)
                        session.add(tag_obj)
                        session.flush()
                    if tag_obj not in doc.tags:
                        doc.tags.append(tag_obj)
            elif action == "untag":
                for tag_name in params.get("tags") or []:
                    normalized = tag_name.strip().lower()
                    tag_obj = session.query(Tag).filter(func.lower(Tag.name) == normalized).first()
                    if tag_obj and tag_obj in doc.tags:
                        doc.tags.remove(tag_obj)
            elif action == "collect":
                coll_id = params.get("collection_id")
                if coll_id:
                    coll = session.get(Collection, coll_id)
                    if coll and coll not in doc.collections:
                        doc.collections.append(coll)
            elif action == "retry":
                if doc.status in ("done", "failed"):
                    doc.status = "pending"
                create_jobs_for_document(doc_id)

            results.append({"id": doc_id, "success": True})
        except Exception as e:
            logger.error(f"批量操作 {action} 在 {doc_id} 失败: {e}")
            results.append({"id": doc_id, "success": False, "error": str(e)})

    session.commit()
    return {"code": "OK", "message": "success", "data": results}
