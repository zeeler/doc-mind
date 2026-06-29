"""标签管理路由。"""
import uuid
import logging
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from server.database import get_session
from server.models.tag import Tag, document_tags

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/tags", tags=["tags"])


@router.get("")
def list_tags(session: Session = Depends(get_session)):
    tags = session.query(Tag).order_by(Tag.name).all()
    return {
        "code": "OK",
        "message": "success",
        "data": [
            {
                "id": t.id,
                "name": t.name,
                "doc_count": session.query(func.count(document_tags.c.doc_id))
                .filter(document_tags.c.tag_id == t.id)
                .scalar(),
            }
            for t in tags
        ],
    }


@router.post("")
def create_tag(payload: dict, session: Session = Depends(get_session)):
    name = payload.get("name", "")
    if not name or not name.strip():
        raise HTTPException(status_code=400, detail="标签名不能为空")
    if len(name.strip()) > 100:
        raise HTTPException(status_code=400, detail="标签名不能超过100个字符")

    existing = session.query(Tag).filter(Tag.name.ilike(name.strip())).first()
    if existing:
        return {
            "code": "OK",
            "message": "success",
            "data": {"id": existing.id, "name": existing.name, "duplicate": True},
        }

    tag = Tag(name=name.strip())
    session.add(tag)
    session.commit()
    session.refresh(tag)
    return {
        "code": "OK",
        "message": "success",
        "data": {"id": tag.id, "name": tag.name},
    }


@router.delete("/{tag_id}")
def delete_tag(tag_id: str, session: Session = Depends(get_session)):
    tag = session.get(Tag, tag_id)
    if not tag:
        raise HTTPException(status_code=404, detail="标签不存在")
    session.delete(tag)
    session.commit()
    return {"code": "OK", "message": "success", "data": None}
