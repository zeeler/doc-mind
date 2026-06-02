"""会话管理路由。"""

import uuid
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from server.database import get_session
from server.models.conversation import Conversation, Message

router = APIRouter(prefix="/api/v1/conversations", tags=["conversations"])


@router.post("")
def create_conversation(body: dict = {}, session: Session = Depends(get_session)):
    title = body.get("title", "").strip() if body else ""
    conv = Conversation(id=str(uuid.uuid4()), title=title or "新会话")
    session.add(conv)
    session.commit()
    return {
        "code": "OK",
        "message": "success",
        "data": {
            "id": conv.id,
            "title": conv.title,
            "status": conv.status,
            "created_at": conv.created_at.isoformat(),
        },
    }


@router.get("")
def list_conversations(session: Session = Depends(get_session)):
    convs = session.query(Conversation).order_by(Conversation.created_at.desc()).all()
    return {
        "code": "OK",
        "message": "success",
        "data": [
            {
                "id": c.id,
                "title": c.title,
                "status": c.status,
                "created_at": c.created_at.isoformat(),
                "message_count": len(c.messages),
            }
            for c in convs
        ],
    }


@router.put("/{conv_id}")
def update_conversation(conv_id: str, body: dict, session: Session = Depends(get_session)):
    conv = session.get(Conversation, conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="会话不存在")
    if "title" in body and body["title"].strip():
        conv.title = body["title"].strip()
        session.commit()
    return {
        "code": "OK",
        "message": "success",
        "data": {
            "id": conv.id,
            "title": conv.title,
            "status": conv.status,
            "created_at": conv.created_at.isoformat(),
        },
    }


@router.delete("/{conv_id}")
def delete_conversation(conv_id: str, session: Session = Depends(get_session)):
    conv = session.get(Conversation, conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="会话不存在")
    session.delete(conv)
    session.commit()
    return {"code": "OK", "message": "success", "data": None}


@router.post("/batch-delete")
def batch_delete_conversations(body: dict, session: Session = Depends(get_session)):
    ids = body.get("ids") or []
    if not ids:
        raise HTTPException(status_code=400, detail="ids 不能为空")
    count = session.query(Conversation).filter(Conversation.id.in_(ids)).delete(synchronize_session=False)
    session.commit()
    return {"code": "OK", "message": "success", "data": {"deleted": count}}


@router.get("/{conv_id}")
def get_conversation(conv_id: str, session: Session = Depends(get_session)):
    conv = session.get(Conversation, conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {
        "code": "OK",
        "message": "success",
        "data": {
            "id": conv.id,
            "title": conv.title,
            "status": conv.status,
            "created_at": conv.created_at.isoformat(),
            "messages": [
                {
                    "id": m.id,
                    "role": m.role,
                    "content": m.content,
                    "citations": m.citations_json,
                    "created_at": m.created_at.isoformat(),
                }
                for m in conv.messages
            ],
        },
    }
