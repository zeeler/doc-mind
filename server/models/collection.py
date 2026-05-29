"""Collection 模型。"""

import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Column, ForeignKey, Table, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from server.models.base import Base


collection_documents = Table(
    "collection_documents",
    Base.metadata,
    Column("doc_id", String(36), ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True),
    Column("collection_id", String(36), ForeignKey("collections.id", ondelete="CASCADE"), primary_key=True),
    Column("added_at", DateTime, default=lambda: datetime.now(timezone.utc)),
)


class Collection(Base):
    __tablename__ = "collections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    documents: Mapped[list["Document"]] = relationship(
        "Document", secondary=collection_documents, back_populates="collections"
    )
