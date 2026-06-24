"""
SQLAlchemy declarative base。

1b 阶段会在这里加 Agent / Version / API Key 三个模型。
"""
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
