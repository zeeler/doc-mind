"""后台任务 Worker — 线程池消费 Job 队列。"""

import threading
import sqlalchemy as sa
import logging
import time
from pathlib import Path
from datetime import datetime, timezone
from server.database import get_session_ctx
from server.models.job import Job
from server.models.document import Document
from server.services.scanner import quick_scan, build_index_md
from server.services.parser import parse_file
from server.config import AppConfig

logger = logging.getLogger("knowledge-base")

_workers: list[threading.Thread] = []
_stop = False
_claim_lock = threading.Lock()


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
    """启动时清理：恢复卡住的 running 任务、删除孤儿 jobs、去重。"""
    with get_session_ctx() as s:
        # 恢复卡住的 running 任务
        count = Job.reset_by_status(s, ["running"])

        # 删除文档已被删除的孤儿任务
        orphan = s.query(Job).filter(
            ~Job.document_id.in_(s.query(Document.id))
        ).delete(synchronize_session=False)

        # 删除同文档同类型重复的 pending（保留最早），防止启动恢复造成堆积
        kept = s.query(
            Job.document_id, Job.job_type,
            sa.func.min(Job.created_at).label("earliest")
        ).filter(Job.status == "pending").group_by(
            Job.document_id, Job.job_type
        ).subquery()
        dup = s.query(Job).filter(
            Job.status == "pending",
            ~Job.id.in_(
                s.query(Job.id).join(
                    kept,
                    sa.and_(
                        Job.document_id == kept.c.document_id,
                        Job.job_type == kept.c.job_type,
                        Job.created_at == kept.c.earliest,
                    ),
                )
            ),
        ).delete(synchronize_session=False)

        s.commit()  # 一次提交所有清理操作

        if count or orphan or dup:
            logger.info(f"启动清理: {count or 0} running→pending, {orphan or 0} 孤儿, {dup or 0} 重复已删除")


def stop_workers():
    global _stop
    _stop = True
    for t in _workers:
        t.join(timeout=5)


def _worker_loop(idx: int):
    logger.info(f"Worker {idx} 就绪")
    while not _stop:
        try:
            job = _claim_job()
        except Exception as e:
            logger.error(f"Worker {idx} 认领任务失败: {e}")
            time.sleep(1)
            continue

        if job is None:
            time.sleep(1)
            continue
        try:
            _execute_job(job)
        except Exception as e:
            logger.error(f"Worker {idx} 任务失败 {job.id}: {e}", exc_info=True)
            with get_session_ctx() as s:
                j = s.get(Job, job.id)
                if j:
                    j.status = "failed"
                    j.error_message = str(e)[:500]
                    j.finished_at = datetime.now(timezone.utc)
                    s.commit()


def _claim_job() -> Job | None:
    """认领一个 pending 任务。锁只保护 claim 操作，不阻塞 commit。"""
    with _claim_lock:
        with get_session_ctx() as s:
            job = s.query(Job).filter(Job.status == "pending").order_by(Job.priority, Job.created_at).first()
            if job:
                job.status = "running"
                job.started_at = datetime.now(timezone.utc)
                s.commit()
                s.refresh(job)
                s.expunge(job)  # 显式分离，在锁外使用
                return job
    return None


def _execute_job(job: Job):
    config = AppConfig().get_all()
    with get_session_ctx() as s:
        # 使用 get() 而非 merge()，避免在 job 已被删除时复活一条新记录
        job = s.get(Job, job.id)
        if not job:
            logger.warning(f"任务已被删除（文档可能已删除），跳过: {job.id}")
            return
        if job.status != "running":
            # 已由其他进程处理或状态已变更
            return

        doc = s.get(Document, job.document_id)
        if not doc:
            job.status = "failed"
            job.error_message = "文档不存在"
            job.finished_at = datetime.now(timezone.utc)
            s.commit()
            return

        # 处理前检查文件是否仍然存在
        file_path = Path(doc.file_path)
        if not file_path.exists():
            job.status = "failed"
            job.error_message = f"文件不存在（可能已被移动或删除）: {doc.file_path}"
            job.finished_at = datetime.now(timezone.utc)
            s.commit()
            logger.warning(f"任务 {job.id} 失败: 文件不存在 {doc.file_path}")
            return

        if job.job_type == "quick_scan":
            info = quick_scan(str(file_path))
            doc.title = info["title"] or doc.title
            doc.status = "scanned"
            md_dir = file_path.parent
            md_path = md_dir / "index.md"
            info["status"] = "scanned"
            md_path.write_text(build_index_md(info), encoding="utf-8")
            job.progress = 100
            job.status = "completed"
            job.finished_at = datetime.now(timezone.utc)
            s.commit()
            logger.info(f"快速扫描完成: {doc.title}")

        elif job.job_type == "full_index":
            text = parse_file(str(file_path), config)
            md_dir = file_path.parent
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
    with get_session_ctx() as s:
        # 清理旧的 completed/failed 任务，避免多次重建后堆积
        s.query(Job).filter(
            Job.document_id == doc_id,
            Job.status.in_(["completed", "failed"]),
        ).delete()
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
