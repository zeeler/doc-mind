"""Shared tag utility functions for auto-tagging and manual tagging."""

import uuid
import logging
from sqlalchemy.orm import Session
from server.models.tag import Tag

logger = logging.getLogger(__name__)

MAX_TAG_NAME_LENGTH = 100


def normalize_tag_name(name: str) -> str:
    cleaned = name.strip()
    return cleaned[:MAX_TAG_NAME_LENGTH] if cleaned else ""


def get_or_create_tag(session: Session, name: str):
    if not name:
        return None
    tag_obj = session.query(Tag).filter(Tag.name.ilike(name)).first()
    if not tag_obj:
        tag_obj = Tag(id=str(uuid.uuid4()), name=name)
        session.add(tag_obj)
        session.flush()
    return tag_obj


def get_tag(session: Session, name: str):
    if not name:
        return None
    return session.query(Tag).filter(Tag.name.ilike(name)).first()
