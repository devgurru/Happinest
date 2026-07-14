"""
SQLAlchemy Base — shared by all models.
Import Base from here, never create a second one.
"""
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
