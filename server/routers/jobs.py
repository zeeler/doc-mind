"""任务管理路由。"""

import logging
import threading
import time
import sqlalchemy as sa
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from server.database import get_session, get_session_ctx
from server.models.job import Job

logger = logging.getLogger("knowledge-base")

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])

# 后台定时恢复卡住任务的线程
_stale_recovery_thread: threading.Thread | None = None
_stale_recovery_stop = False
_STALE_TIMEOUT_MINUTES = 10
_STALE_CHECK_INTERVAL = 120  # 每 2 分钟检查一次


@router.get("")
def list_jobs(status: str = None, session: Session = Depends(get_session)):
    q = session.query(Job)
    if status:
        q = q.filter(Job.status == status)
    jobs = q.order_by(Job.created_at.desc()).limit(100).all()
    return {
        "code": "OK",
        "data": [
            {
                "id": j.id,
                "document_id": j.document_id,
                "job_type": j.job_type,
                "status": j.status,
                "progress": j.progress,
                "error_message": j.error_message,
                "created_at": j.created_at.isoformat(),
            }
            for j in jobs
        ],
    }


def _recover_stale_running(session: Session) -> int:
    """将卡住超过 STALE_TIMEOUT_MINUTES 分钟（或无 started_at）的 running 任务重置为 pending。"""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=_STALE_TIMEOUT_MINUTES)
    count = session.query(Job).filter(
        Job.status == "running",
        (Job.started_at == None) | (Job.started_at < cutoff),
    ).update(
        {"status": "pending", "error_message": None, "started_at": None},
        synchronize_session=False,
    )
    if count:
        logger.info(f"自动恢复卡住任务: {count} 个 running → pending")
    return count


def start_stale_recovery():
    """启动后台线程，定期恢复卡住的 running 任务。"""
    global _stale_recovery_thread, _stale_recovery_stop
    _stale_recovery_stop = False

    def _loop():
        while not _stale_recovery_stop:
            time.sleep(_STALE_CHECK_INTERVAL)
            if _stale_recovery_stop:
                break
            try:
                with get_session_ctx() as s:
                    count = _recover_stale_running(s)
                    if count:
                        s.commit()
            except Exception as e:
                logger.warning(f"后台恢复线程出错: {e}")

    _stale_recovery_thread = threading.Thread(
        target=_loop, daemon=True, name="stale-recovery"
    )
    _stale_recovery_thread.start()
    logger.info(f"后台僵尸任务恢复已启动（间隔 {_STALE_CHECK_INTERVAL}s，超时 {_STALE_TIMEOUT_MINUTES}min）")


def stop_stale_recovery():
    """停止后台恢复线程。"""
    global _stale_recovery_stop
    _stale_recovery_stop = True


@router.get("/stats")
def job_stats(session: Session = Depends(get_session)):
    # 自动恢复卡住的 running 任务，确保前端统计不显示僵尸任务
    stale = _recover_stale_running(session)
    if stale:
        session.commit()

    # 一次 GROUP BY 查询替代 8 次独立 COUNT
    rows = session.query(
        Job.job_type, Job.status, sa.func.count(Job.id)
    ).filter(
        Job.job_type.in_(["quick_scan", "full_index"])
    ).group_by(Job.job_type, Job.status).all()

    counts: dict[str, dict[str, int]] = {
        "quick_scan": {"pending": 0, "running": 0, "completed": 0, "failed": 0},
        "full_index": {"pending": 0, "running": 0, "completed": 0, "failed": 0},
    }
    for jt, st, cnt in rows:
        counts[jt][st] = cnt
    return {"code": "OK", "data": counts}


@router.post("/{job_id}/retry")
def retry_job(job_id: str, session: Session = Depends(get_session)):
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job.status == "running":
        raise HTTPException(status_code=409, detail="任务正在执行中，不能重试")
    if job.status == "failed":
        job.status = "pending"
        job.error_message = None
        job.started_at = None
        session.commit()
    return {"code": "OK", "data": {"id": job.id, "status": job.status}}


@router.post("/retry-failed")
def retry_failed_jobs(session: Session = Depends(get_session)):
    """批量重试所有失败的任务（不包含正在执行中的任务）。"""
    count = Job.reset_by_status(session, ["failed"])
    session.commit()
    return {"code": "OK", "data": {"retried": count}}
