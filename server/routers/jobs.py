"""任务管理路由。"""

import logging
import sqlalchemy as sa
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from server.database import get_session
from server.models.job import Job

logger = logging.getLogger("knowledge-base")

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


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
    """将卡住超过 10 分钟（或无 started_at）的 running 任务重置为 pending。"""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
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
    if job.status in ("failed", "running"):
        job.status = "pending"
        job.error_message = None
        job.started_at = None
        session.commit()
    return {"code": "OK", "data": {"id": job.id, "status": job.status}}


@router.post("/retry-failed")
def retry_failed_jobs(session: Session = Depends(get_session)):
    """批量重试所有失败和卡住的任务。"""
    count = Job.reset_by_status(session, ["failed", "running"])
    session.commit()
    return {"code": "OK", "data": {"retried": count}}
