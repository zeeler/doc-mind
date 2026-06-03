"""文档管理路由。"""

import uuid
import hashlib
import shutil
import logging
import threading
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends, Request
from sqlalchemy import func
from sqlalchemy.orm import Session
from server.database import get_session, get_session_ctx, DATA_DIR, fts_delete_by_document_id
from server.models.document import Document, DocumentChunk
from server.models.tag import Tag, document_tags
from server.models.collection import Collection, collection_documents
from server.models.job import Job
from server.services.parser import SUPPORTED_TYPES
from server.services.search import get_search_service
from server.services.worker import create_jobs_for_document

logger = logging.getLogger("knowledge-base")

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])

# 文件上传限制
MAX_FILE_SIZE = 200 * 1024 * 1024  # 200MB
MAX_TAG_NAME_LENGTH = 100

# 去重检查锁：防止并发上传相同文件绕过 TOCTOU 检查
_dedup_lock = threading.Lock()


def _compute_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _normalize_tag_name(name: str) -> str:
    """标准化标签名：去空白、截断至最大长度。"""
    cleaned = name.strip()
    return cleaned[:MAX_TAG_NAME_LENGTH] if cleaned else ""


def _get_or_create_tag(session: Session, name: str) -> "Tag | None":
    """获取或创建标签（大小写不敏感），name 必须已通过 _normalize_tag_name 处理。"""
    if not name:
        return None
    normalized = name.lower()
    tag_obj = session.query(Tag).filter(func.lower(Tag.name) == normalized).first()
    if not tag_obj:
        tag_obj = Tag(id=str(uuid.uuid4()), name=name)
        session.add(tag_obj)
        session.flush()
    return tag_obj


def _get_tag(session: Session, name: str) -> "Tag | None":
    """按名称查找标签（大小写不敏感），name 必须已通过 _normalize_tag_name 处理。"""
    if not name:
        return None
    return session.query(Tag).filter(func.lower(Tag.name) == name.lower()).first()


def _cleanup_document_indices(doc_id: str) -> None:
    """清理某文档的所有索引（ChromaDB + FTS5）。静默处理错误。"""
    try:
        from server.vector.store import VectorStore
        store = VectorStore(persist_dir=str(DATA_DIR / "chroma"))
        store.delete_by_document_id(doc_id)
    except Exception as e:
        logger.warning(f"ChromaDB 清理失败 doc {doc_id}: {e}")
    try:
        fts_delete_by_document_id(doc_id)
    except Exception as e:
        logger.warning(f"FTS5 清除索引失败 doc {doc_id}: {e}")


@router.post("/upload")
async def upload_document(request: Request, file: UploadFile = File(...), folder_path: str = Form("")):
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")

    suffix = Path(file.filename).suffix.lower().lstrip(".")
    if suffix not in SUPPORTED_TYPES:
        raise HTTPException(status_code=400, detail=f"不支持的文件类型: {suffix}")

    # 检查 Content-Length（如果客户端提供了的话，快速拒绝超大文件）
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"文件大小超过限制 ({MAX_FILE_SIZE // 1024 // 1024}MB)",
        )

    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"文件大小超过限制 ({MAX_FILE_SIZE // 1024 // 1024}MB)",
        )

    checksum = _compute_sha256(content)

    # === 去重检查（加锁防止 TOCTOU 竞态）===
    with _dedup_lock:
        with get_session_ctx() as session:
            existing = session.query(Document).filter(Document.checksum == checksum).first()

        if existing:
            need_reprocess = existing.chunk_count == 0 or existing.status == "failed"
            new_status = existing.status

            if need_reprocess:
                sid = existing.id
                with get_session_ctx() as s:
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

        with get_session_ctx() as session:
            session.add(doc)
            session.commit()
            session.refresh(doc)

    # 在锁外创建任务，避免长时间持锁
    create_jobs_for_document(doc_id)

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

    if folder:
        q = q.filter(Document.folder_path == folder)
    if category:
        q = q.filter(Document.category == category)
    if status:
        q = q.filter(Document.status == status)
    if search:
        search_svc = get_search_service(data_dir=DATA_DIR, top_k=50)
        doc_results = search_svc.document_search(search, top_k=50)
        match_ids = [d["document_id"] for d in doc_results]
        if match_ids:
            q = q.filter(Document.id.in_(match_ids))
        else:
            # FTS 无结果时回退到标题模糊匹配
            q = q.filter(Document.title.ilike(f"%{search}%"))
    if tag:
        q = q.join(Document.tags).filter(Tag.name == tag)
    if collection:
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
                "tags": list({t.id: {"id": t.id, "name": t.name} for t in d.tags}.values()),
                "collections": list({c.id: {"id": c.id, "name": c.name} for c in d.collections}.values()),
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
                _cleanup_document_indices(doc_id)
                session.query(DocumentChunk).filter(DocumentChunk.document_id == doc_id).delete()
                session.query(Job).filter(Job.document_id == doc_id).delete()
                session.delete(doc)
                file_dir = DATA_DIR / "files" / doc_id
                if file_dir.exists():
                    shutil.rmtree(file_dir)
            elif action == "categorize":
                doc.category = (params.get("category") or "").strip()
            elif action == "tag":
                # 对输入去重（按 lower 名），防止同一请求重复添加
                seen_names: set[str] = set()
                for tag_name in params.get("tags") or []:
                    name = _normalize_tag_name(tag_name)
                    if not name:
                        continue
                    if name.lower() in seen_names:
                        continue
                    seen_names.add(name.lower())
                    tag_obj = _get_or_create_tag(session, name)
                    if tag_obj and tag_obj not in doc.tags:
                        doc.tags.append(tag_obj)
            elif action == "untag":
                seen_names = set()
                for tag_name in params.get("tags") or []:
                    name = _normalize_tag_name(tag_name)
                    if not name:
                        continue
                    name_lower = name.lower()
                    if name_lower in seen_names:
                        continue
                    seen_names.add(name_lower)
                    tag_obj = _get_tag(session, name)
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
            session.rollback()
            results.append({"id": doc_id, "success": False, "error": str(e)})

    session.commit()
    return {"code": "OK", "message": "success", "data": results}


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

    _cleanup_document_indices(doc_id)

    # 显式清理关联数据（兼容未启用 CASCADE 的旧数据库）
    session.query(DocumentChunk).filter(DocumentChunk.document_id == doc_id).delete()
    session.query(Job).filter(Job.document_id == doc_id).delete()
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

    seen_add: set[str] = set()
    for tag_name in payload.get("add_tags") or []:
        name = _normalize_tag_name(tag_name)
        if not name or name.lower() in seen_add:
            continue
        seen_add.add(name.lower())
        tag_obj = _get_or_create_tag(session, name)
        if tag_obj and tag_obj not in doc.tags:
            doc.tags.append(tag_obj)

    seen_remove: set[str] = set()
    for tag_name in payload.get("remove_tags") or []:
        name = _normalize_tag_name(tag_name)
        if not name:
            continue
        name_lower = name.lower()
        if name_lower in seen_remove:
            continue
        seen_remove.add(name_lower)
        tag_obj = _get_tag(session, name)
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
