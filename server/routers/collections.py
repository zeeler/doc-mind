"""集合管理路由。"""
import uuid
from datetime import datetime, timezone
import logging
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from server.database import get_session
from server.models.collection import Collection, collection_documents

logger = logging.getLogger("knowledge-base")

router = APIRouter(prefix="/api/v1/collections", tags=["collections"])


@router.get("")
def list_collections(session: Session = Depends(get_session)):
    collections = session.query(Collection).order_by(Collection.created_at.desc()).all()
    return {
        "code": "OK",
        "message": "success",
        "data": [
            {
                "id": c.id,
                "name": c.name,
                "description": c.description,
                "doc_count": session.query(func.count(collection_documents.c.doc_id))
                .filter(collection_documents.c.collection_id == c.id)
                .scalar(),
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in collections
        ],
    }


@router.post("")
def create_collection(payload: dict, session: Session = Depends(get_session)):
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="集合名不能为空")

    coll = Collection(
        id=str(uuid.uuid4()),
        name=name,
        description=payload.get("description"),
        created_at=datetime.now(timezone.utc),
    )
    session.add(coll)
    session.commit()
    session.refresh(coll)
    return {
        "code": "OK",
        "message": "success",
        "data": {"id": coll.id, "name": coll.name, "description": coll.description},
    }


@router.put("/{collection_id}")
def update_collection(collection_id: str, payload: dict, session: Session = Depends(get_session)):
    coll = session.get(Collection, collection_id)
    if not coll:
        raise HTTPException(status_code=404, detail="集合不存在")
    name = (payload.get("name") or "").strip()
    if name:
        coll.name = name
    if "description" in payload:
        coll.description = payload["description"]
    session.commit()
    return {"code": "OK", "message": "success", "data": None}


@router.delete("/{collection_id}")
def delete_collection(collection_id: str, session: Session = Depends(get_session)):
    coll = session.get(Collection, collection_id)
    if not coll:
        raise HTTPException(status_code=404, detail="集合不存在")
    session.delete(coll)
    session.commit()
    return {"code": "OK", "message": "success", "data": None}
