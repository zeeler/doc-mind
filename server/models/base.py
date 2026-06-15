"""SQLAlchemy 基类。"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """SQLAlchemy DeclarativeBase — column defaults 由 ORM 原生处理。"""
