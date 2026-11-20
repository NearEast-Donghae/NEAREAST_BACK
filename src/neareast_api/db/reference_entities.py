from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from neareast_api.db.base import Base
from neareast_api.db.entities import JSON_VALUE


class DataReferenceEntity(Base):
    __tablename__ = "data_reference_records"
    collection: Mapped[str] = mapped_column(String(80), primary_key=True)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON_VALUE)
