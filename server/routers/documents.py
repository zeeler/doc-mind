"""文档管理路由。"""

import uuid
import hashlib
import shutil
import logging
import threading
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, Form, Body, HTTPException, Depends, Request
from sqlalchemy.orm import Session
from server.database import get_session, get_session_ctx, DATA_DIR, fts_delete_by_document_id
from server.models.document import Document, DocumentChunk
from server.models.tag import Tag, document_tags
from server.models.job import Job
from server.services.parser import SUPPORTED_TYPES
from server.services.tag_utils import normalize_tag_name, get_or_create_tag, get_tag
from server.services.search import get_search_service
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

    # Use dedup lock (same as upload endpoint) to prevent TOCTOU race
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

        from server.services.url_fetcher import fetch_url
        result = fetch_url(url)
        if result["error"]:
            raise HTTPException(status_code=400, detail=f"获取 URL 失败: {result['error']}")

        title = result["title"] or url
        text_content = result["text_content"]
        if not text_content or len(text_content.strip()) < 10:
            raise HTTPException(status_code=400, detail="无法提取页面内容（内容过短）")

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


@router.post("/import-bookmarks")
async def import_bookmarks(
    file: UploadFile | None = None,
    payload: dict | None = Body(None),
    session: Session = Depends(get_session),
):
    """导入 Chrome 书签：上传 HTML 预览或批量导入 URL。

    预览模式：上传 .html 文件，返回结构化预览
    导入模式：传入 {"urls": [...], "folder_path": "..."}
    """
    from server.services.bookmark_parser import parse_bookmarks_html

    # Preview mode: parse bookmark HTML file
    if file:
        content = await file.read()
        bookmarks = parse_bookmarks_html(content.decode("utf-8", errors="replace"))

        # Group by folder for preview
        folders: dict[str, list[dict]] = {}
        for bm in bookmarks:
            fp = bm["folder_path"] or "根目录"
            folders.setdefault(fp, []).append(bm)

        return {
            "code": "OK",
            "message": "success",
            "data": {
                "total": len(bookmarks),
                "folders": [
                    {"path": fp, "count": len(items)}
                    for fp, items in sorted(folders.items())
                ],
            },
        }

    # Import mode: create background job for batch import
    if payload:
        urls = payload.get("urls") or []
        folder_path = (payload.get("folder_path") or "书签导入").strip() or "书签导入"

        if not urls:
            raise HTTPException(status_code=400, detail="URLs 不能为空")

        import json as _json
        # Store URLs + folder_path in a staging file for the worker
        import uuid as _uuid
        job_id = str(_uuid.uuid4())
        staging_dir = DATA_DIR / "files" / f"_import_{job_id}"
        staging_dir.mkdir(parents=True, exist_ok=True)
        (staging_dir / "urls.json").write_text(
            _json.dumps({"urls": urls, "folder_path": folder_path}, ensure_ascii=False),
            encoding="utf-8"
        )

        # Create a single Job for background processing
        from server.models.job import Job
        job = Job(
            id=job_id,
            document_id="",  # no single document
            job_type="bookmark_import",
            priority=5,
            status="pending",
        )
        session.add(job)
        session.commit()

        return {
            "code": "OK",
            "message": "success",
            "data": {
                "job_id": job_id,
                "total": len(urls),
                "message": f"已创建后台导入任务，共 {len(urls)} 条书签",
            },
        }

    raise HTTPException(status_code=400, detail="请提供书签文件或 URLs 参数")


@router.get("")
def list_documents(
    skip: int = 0,
    limit: int = 50,
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


@router.post("/{doc_id}/retag")
def retag_document(doc_id: str, session: Session = Depends(get_session)):
    """清除现有标签并重新自动生成。"""
    doc = session.get(Document, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")

    from server.services.parser import parse_file
    from server.services.auto_tagger import auto_tag_document
    from server.config import AppConfig

    config = AppConfig().get_all()

    # Get text from file
    text = ""
    file_path = Path(doc.file_path)
    if file_path.exists():
        try:
            text = parse_file(str(file_path), config)
        except Exception:
            pass

    # Fallback to chunks
    if not text:
        from server.models.document import DocumentChunk
        chunks = (
            session.query(DocumentChunk)
            .filter(DocumentChunk.document_id == doc_id)
            .order_by(DocumentChunk.chunk_no)
            .all()
        )
        text = "\n".join(c.content for c in chunks)

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
