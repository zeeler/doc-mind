"""SQLAlchemy 基类。"""

import functools
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    def __init_subclass__(cls, **kw):
        had_init = "__init__" in cls.__dict__
        super().__init_subclass__(**kw)
        if had_init:
            return

        original_init = cls.__init__

        @functools.wraps(original_init)
        def _init_with_defaults(self, **kwargs):
            for attr in cls.__mapper__.column_attrs:
                col = attr.expression
                if attr.key in kwargs:
                    continue
                default = col.default
                if default is not None:
                    if default.is_scalar:
                        kwargs.setdefault(attr.key, default.arg)
                    elif default.is_callable:
                        kwargs.setdefault(attr.key, default.arg(None))
            original_init(self, **kwargs)

        cls.__init__ = _init_with_defaults
