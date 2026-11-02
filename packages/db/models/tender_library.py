"""Persistent opt-out for virtual tender-library entries; no file deletion."""

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class TenderLibraryDismissal(Base):
    __tablename__ = 'tender_library_dismissals'

    base_public_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    key: Mapped[str] = mapped_column(String(96), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), server_default=func.now(), nullable=False)
