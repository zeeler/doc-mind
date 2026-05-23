"""会话管理路由。"""

import uuid
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from server.database import get_session
from server.models.conversation import Conversation, Message

router = APIRouter(prefix="/api/v1/conversations", tags=["conversations"])


@router.post("")
def create_conversation(session: Session = Depends(get_session)):
    conv = Conversation(id=str(uuid.uuid4()), title="新会话")
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
