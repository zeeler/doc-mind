"""后台任务 Worker — 线程池消费 Job 队列。"""

import threading
import logging
import time
from pathlib import Path
from datetime import datetime, timezone
from server.database import get_session
from server.models.job import Job
from server.models.document import Document
from server.services.scanner import quick_scan, build_index_md
from server.services.parser import parse_file
from server.config import AppConfig

logger = logging.getLogger("knowledge-base")

_workers: list[threading.Thread] = []
_stop = False


def start_workers(num: int = 2):
    global _stop
    _stop = False
    _recover_stuck_jobs()
    for i in range(num):
        t = threading.Thread(target=_worker_loop, args=(i,), daemon=True, name=f"kb-worker-{i}")
        t.start()
        _workers.append(t)
    logger.info(f"Worker 启动: {num} 线程")


def _recover_stuck_jobs():
    """启动时将卡在 running 状态的任务重置为 pending。"""
    with next(get_session()) as s:
        from server.models.job import Job
        count = s.query(Job).filter(Job.status == "running").update(
            {"status": "pending", "error_message": None, "started_at": None}, synchronize_session=False
        )
        if count:
            s.commit()
            logger.info(f"恢复卡住任务: {count} 个 running → pending")


def stop_workers():
    global _stop
    _stop = True
    for t in _workers:
        t.join(timeout=5)


def _worker_loop(idx: int):
    logger.info(f"Worker {idx} 就绪")
    while not _stop:
        job = _claim_job()
        if job is None:
            time.sleep(1)
            continue
        try:
            _execute_job(job)
        except Exception as e:
            logger.error(f"Worker {idx} 任务失败 {job.id}: {e}", exc_info=True)
            with next(get_session()) as s:
                j = s.get(Job, job.id)
                if j:
                    j.status = "failed"
                    j.error_message = str(e)[:500]
                    j.finished_at = datetime.now(timezone.utc)
                    s.commit()


def _claim_job() -> Job | None:
    with next(get_session()) as s:
        job = s.query(Job).filter(Job.status == "pending").order_by(Job.priority, Job.created_at).first()
        if job:
            job.status = "running"
            job.started_at = datetime.now(timezone.utc)
            s.commit()
            s.refresh(job)
            return job
    return None


def _execute_job(job: Job):
    config = AppConfig().get_all()
    with next(get_session()) as s:
        doc = s.get(Document, job.document_id)
        if not doc:
            job.status = "failed"
            job.error_message = "文档不存在"
            job.finished_at = datetime.now(timezone.utc)
            s.commit()
            return

        if job.job_type == "quick_scan":
            info = quick_scan(doc.file_path)
            doc.title = info["title"] or doc.title
            doc.status = "scanned"
            md_dir = Path(doc.file_path).parent
            md_path = md_dir / "index.md"
            info["status"] = "scanned"
            md_path.write_text(build_index_md(info), encoding="utf-8")
            job.progress = 100
            job.status = "completed"
            job.finished_at = datetime.now(timezone.utc)
            s.commit()
            logger.info(f"快速扫描完成: {doc.title}")

        elif job.job_type == "full_index":
            text = parse_file(doc.file_path, config)
            md_dir = Path(doc.file_path).parent
            md_path = md_dir / "index.md"
            info = {
                "title": doc.title, "format": doc.file_type,
                "page_count": doc.chunk_count or 0, "size_bytes": doc.file_size,
                "status": "done",
            }
            md_path.write_text(build_index_md(info, text), encoding="utf-8")

            # 调用索引管道
            from server.services.pipeline import index_document
            index_document(job.document_id, text, config)

            doc.status = "done"
            job.progress = 100
            job.status = "completed"
            job.finished_at = datetime.now(timezone.utc)
            s.commit()
            logger.info(f"全文索引完成: {doc.title} ({doc.chunk_count} chunks)")


def create_jobs_for_document(doc_id: str):
    """为一篇文档创建 quick_scan + full_index 两个任务（跳过已有活跃任务）。"""
    with next(get_session()) as s:
        for jt, pri in [("quick_scan", 1), ("full_index", 5)]:
            existing = s.query(Job).filter(
                Job.document_id == doc_id,
                Job.job_type == jt,
                Job.status.in_(["pending", "running"]),
            ).first()
            if not existing:
                job = Job(document_id=doc_id, job_type=jt, priority=pri)
                s.add(job)
        s.commit()
