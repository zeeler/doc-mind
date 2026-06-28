"""文档管理路由。"""

import uuid
import hashlib
import shutil
import threading
from sqlalchemy import func
import logging
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, Form, Body, HTTPException, Depends, Request
from sqlalchemy.orm import Session
from server.database import get_session, get_session_ctx, DATA_DIR, fts_delete_by_document_id
from server.models.document import Document, DocumentChunk
from server.models.tag import Tag, document_tags
from server.models.job import Job
from server.services.parser import SUPPORTED_TYPES
from server.services.tag_utils import normalize_tag_name, get_or_create_tag, get_tag
from server.services.worker import create_jobs_for_document

logger = logging.getLogger("knowledge-base")

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])

# 文件上传限制
MAX_FILE_SIZE = 200 * 1024 * 1024  # 200MB

# 去重检查锁：防止并发上传相同文件绕过 TOCTOU 检查
_dedup_lock = threading.Lock()


def _compute_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()



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
        safe_name = Path(file.filename).name  # 仅取文件名，防路径穿越
        file_path = file_dir / safe_name
        file_path.write_bytes(content)

        doc = Document(
            id=doc_id,
            title=Path(safe_name).stem,
            file_name=safe_name,
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


@router.post("/import-url")
def import_url(payload: dict = Body(...), session: Session = Depends(get_session)):
    """导入 URL 作为资料：抓取、提取、创建记录、排队处理。"""
    url = (payload.get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL 不能为空")
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="URL 必须以 http:// 或 https:// 开头")

    folder_path = (payload.get("folder_path") or "").strip()

    # SHA256(url) for dedup
    import hashlib
    checksum = hashlib.sha256(url.encode("utf-8")).hexdigest()

    # 在锁外完成网络 IO，避免阻塞并发上传请求
    from server.services.url_fetcher import fetch_url
    result = fetch_url(url)
    if result["error"]:
        raise HTTPException(status_code=400, detail=f"获取 URL 失败: {result['error']}")

    title = result["title"] or url
    text_content = result["text_content"]
    if not text_content or len(text_content.strip()) < 10:
        raise HTTPException(status_code=400, detail="无法提取页面内容（内容过短）")

    # 去重锁仅保护 DB 查询和创建，避免 TOCTOU 竞争
    with _dedup_lock:
        existing = session.query(Document).filter(Document.checksum == checksum).first()
        if existing:
            return {
                "code": "OK",
                "message": "success",
                "data": {
                    "id": existing.id, "title": existing.title,
                    "file_name": existing.file_name, "status": existing.status,
                    "duplicate": True,
                },
            }

        # Create document
        import uuid as _uuid
        doc_id = str(_uuid.uuid4())
        file_dir = DATA_DIR / "files" / doc_id
        file_dir.mkdir(parents=True, exist_ok=True)

        # Save extracted text as .md
        safe_title = "".join(c for c in title if c.isalnum() or c in "._- ()（）")[:80]
        file_name = f"{safe_title}.md" if safe_title else f"import_{doc_id[:8]}.md"
        file_path = file_dir / file_name
        file_path.write_text(text_content, encoding="utf-8")

        doc = Document(
            id=doc_id,
            title=title[:500],
            file_name=file_name,
            file_type="url",
            file_path=str(file_path),
            file_size=len(text_content.encode("utf-8")),
            checksum=checksum,
            folder_path=folder_path,
            status="pending",
        )
        session.add(doc)
        session.commit()

    create_jobs_for_document(doc_id)

    return {
        "code": "OK",
        "message": "success",
        "data": {
            "id": doc.id, "title": doc.title,
            "file_name": doc.file_name, "file_type": doc.file_type,
            "status": doc.status,
        },
    }


@router.get("/stats")
def get_document_stats(session: Session = Depends(get_session)):
    """获取资料统计：按类型计数 + 任务摘要。"""
    # File type counts
    type_rows = session.query(
        Document.file_type, func.count(Document.id)
    ).group_by(Document.file_type).all()

    type_labels = {
        "pdf": "PDF", "docx": "Word", "xlsx": "Excel", "pptx": "PPT",
        "mobi": "MOBI", "md": "Markdown", "txt": "TXT", "url": "网页",
        "markdown": "Markdown",
    }
    file_types = {}
    for ft, cnt in type_rows:
        label = type_labels.get(ft, ft)
        file_types[label] = file_types.get(label, 0) + cnt

    total = sum(file_types.values())

    # Job summary
    from server.models.job import Job
    job_rows = session.query(
        Job.job_type, Job.status, func.count(Job.id)
    ).filter(Job.job_type.in_(["quick_scan", "full_index"])).group_by(
        Job.job_type, Job.status
    ).all()

    job_summary = {}
    for jt, status, cnt in job_rows:
        job_summary.setdefault(jt, {"completed": 0, "running": 0, "pending": 0, "failed": 0})
        job_summary[jt][status] = cnt

    return {
        "code": "OK",
        "data": {
            "total": total,
            "file_types": file_types,
            "job_summary": job_summary,
        },
    }


@router.get("")
def list_documents(
    skip: int = 0,
    limit: int = 20,
    folder: str | None = None,
    category: str | None = None,
    tag: str | None = None,
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
        from server.services.registry import ServiceRegistry
        search_svc = ServiceRegistry.get_singleton().get_search_service(DATA_DIR, top_k=50)
        doc_results = search_svc.document_search(search, top_k=50)
        match_ids = [d["document_id"] for d in doc_results]
        if match_ids:
            q = q.filter(Document.id.in_(match_ids))
        else:
            # FTS 无结果时回退到标题模糊匹配
            q = q.filter(Document.title.ilike(f"%{search}%"))
    if tag:
        q = q.join(Document.tags).filter(Tag.name == tag)

    total = q.count()
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
                "created_at": d.created_at.isoformat(),
            }
            for d in docs
        ],
        "total": total,
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

    valid_actions = {"delete", "retry", "tag", "untag", "categorize"}
    if action not in valid_actions:
        raise HTTPException(status_code=400, detail=f"不支持的操作类型: {action}")

    results = []
    retry_ids = []  # retry 需要先 commit 再建任务，避免 SQLite 锁冲突
    for doc_id in ids:
        # 用 savepoint 隔离每个文档的操作：单文档失败只回滚自身，不影响已成功的文档
        savepoint = session.begin_nested()
        try:
            doc = session.get(Document, doc_id)
            if not doc:
                savepoint.commit()
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
                    name = normalize_tag_name(tag_name)
                    if not name:
                        continue
                    if name.lower() in seen_names:
                        continue
                    seen_names.add(name.lower())
                    tag_obj = get_or_create_tag(session, name)
                    if tag_obj and tag_obj not in doc.tags:
                        doc.tags.append(tag_obj)
            elif action == "untag":
                seen_names = set()
                for tag_name in params.get("tags") or []:
                    name = normalize_tag_name(tag_name)
                    if not name:
                        continue
                    name_lower = name.lower()
                    if name_lower in seen_names:
                        continue
                    seen_names.add(name_lower)
                    tag_obj = get_tag(session, name)
                    if tag_obj and tag_obj in doc.tags:
                        doc.tags.remove(tag_obj)
            elif action == "retry":
                if doc.status in ("done", "failed", "scanned"):
                    doc.status = "pending"
                    retry_ids.append(doc_id)

            savepoint.commit()
            results.append({"id": doc_id, "success": True})
        except Exception as e:
            logger.error(f"批量操作 {action} 在 {doc_id} 失败: {e}")
            savepoint.rollback()
            results.append({"id": doc_id, "success": False, "error": str(e)})

    session.commit()

    # retry：先 commit 状态变更再建任务，避免 SQLite 锁冲突
    for doc_id in retry_ids:
        create_jobs_for_document(doc_id)

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
        name = normalize_tag_name(tag_name)
        if not name or name.lower() in seen_add:
            continue
        seen_add.add(name.lower())
        tag_obj = get_or_create_tag(session, name)
        if tag_obj and tag_obj not in doc.tags:
            doc.tags.append(tag_obj)

    seen_remove: set[str] = set()
    for tag_name in payload.get("remove_tags") or []:
        name = normalize_tag_name(tag_name)
        if not name:
            continue
        name_lower = name.lower()
        if name_lower in seen_remove:
            continue
        seen_remove.add(name_lower)
        tag_obj = get_tag(session, name)
        if tag_obj and tag_obj in doc.tags:
            doc.tags.remove(tag_obj)

    session.commit()
    return {"code": "OK", "message": "success", "data": None}


def _get_document_text(doc_id: str, file_path: str, session: Session) -> str:
    """获取文档文本内容：优先读取 markdown 文件，回退到数据库 chunks。"""
    md_path = Path(file_path).with_suffix(".md")
    if md_path.exists():
        return md_path.read_text(encoding="utf-8")

    from server.models.document import DocumentChunk
    chunks = (
        session.query(DocumentChunk)
        .filter(DocumentChunk.document_id == doc_id)
        .order_by(DocumentChunk.chunk_no)
        .all()
    )
    return "\n".join(c.content for c in chunks)


@router.post("/{doc_id}/retag")
def retag_document(doc_id: str, session: Session = Depends(get_session)):
    """清除现有标签并重新自动生成。"""
    doc = session.get(Document, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")

    from server.services.auto_tagger import auto_tag_document
    from server.config import AppConfig

    config = AppConfig().get_all()

    text = _get_document_text(doc_id, doc.file_path, session)

    # Clear existing tags
    doc.tags = []
    session.flush()

    # Auto-tag
    new_tags = auto_tag_document(doc_id, text, config, session)

    session.refresh(doc)
    tags_out = list({t.id: {"id": t.id, "name": t.name} for t in doc.tags}.values())

    return {
        "code": "OK",
        "message": "success",
        "data": {"tags": tags_out},
    }
