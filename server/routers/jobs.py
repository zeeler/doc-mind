"""任务管理路由。"""

from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from server.database import get_session
from server.models.job import Job

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


@router.get("/stats")
def job_stats(session: Session = Depends(get_session)):
    counts = {}
    for s in ["pending", "running", "completed", "failed"]:
        counts[s] = session.query(Job).filter(Job.status == s).count()
    return {"code": "OK", "data": counts}


@router.post("/{job_id}/retry")
def retry_job(job_id: str, session: Session = Depends(get_session)):
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job.status == "failed":
        job.status = "pending"
        job.error_message = None
        session.commit()
    return {"code": "OK", "data": {"id": job.id, "status": job.status}}
