"""会话管理路由。"""

import uuid
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from server.database import get_session
from server.models.conversation import Conversation, Message
from server.schemas import CreateConversationRequest, UpdateConversationRequest, BatchDeleteConvsRequest

router = APIRouter(prefix="/api/v1/conversations", tags=["conversations"])


@router.post("")
def create_conversation(req: CreateConversationRequest = CreateConversationRequest(), session: Session = Depends(get_session)):
    title = req.title.strip()
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
def update_conversation(conv_id: str, req: UpdateConversationRequest, session: Session = Depends(get_session)):
    conv = session.get(Conversation, conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="会话不存在")
    if req.title.strip():
        conv.title = req.title.strip()
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
    from server.services.observer import forget_conversation
    forget_conversation(conv_id)
    return {"code": "OK", "message": "success", "data": None}


@router.post("/batch-delete")
def batch_delete_conversations(req: BatchDeleteConvsRequest, session: Session = Depends(get_session)):
    ids = req.ids
    count = session.query(Conversation).filter(Conversation.id.in_(ids)).delete(synchronize_session=False)
    session.commit()
    from server.services.observer import forget_conversation
    for cid in ids:
        forget_conversation(cid)
    return {"code": "OK", "message": "success", "data": {"deleted": count}}


@router.delete("/{conv_id}/messages/{msg_id}")
def delete_message(conv_id: str, msg_id: str, session: Session = Depends(get_session)):
    msg = session.get(Message, msg_id)
    if not msg or msg.conversation_id != conv_id:
        raise HTTPException(status_code=404, detail="消息不存在")
    session.delete(msg)
    session.commit()
    return {"code": "OK", "message": "success", "data": None}


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
