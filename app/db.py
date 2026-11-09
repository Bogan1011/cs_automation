from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint,
                        create_engine)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from .config import settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Client(Base):
    __tablename__ = "clients"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    region: Mapped[str] = mapped_column(String(10))
    api_key_enc: Mapped[str] = mapped_column(Text)
    report_to: Mapped[str] = mapped_column(String(500), default="")  # comma-separated recipients for the draft
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_poll_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    endpoints: Mapped[list["Endpoint"]] = relationship(back_populates="client", cascade="all, delete-orphan")


class Endpoint(Base):
    """Latest known state of each endpoint, replaced on every successful poll."""
    __tablename__ = "endpoints"
    __table_args__ = (UniqueConstraint("client_id", "agent_guid"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    agent_guid: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(255), default="")
    endpoint_type: Mapped[str] = mapped_column(String(40), default="")
    os: Mapped[str] = mapped_column(String(255), default="")
    ip: Mapped[str] = mapped_column(String(255), default="")
    protection: Mapped[str] = mapped_column(String(40), default="")  # SEP / SWP / Sensor only
    policy: Mapped[str] = mapped_column(String(255), default="")
    agent_status: Mapped[str] = mapped_column(String(40), default="")
    online: Mapped[bool] = mapped_column(Boolean, default=False)
    stale: Mapped[bool] = mapped_column(Boolean, default=False)
    update_status: Mapped[str] = mapped_column(String(80), default="")
    edr_status: Mapped[str] = mapped_column(String(40), default="")
    last_connected: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)

    client: Mapped[Client] = relationship(back_populates="endpoints")


class Snapshot(Base):
    """Per-poll summary numbers, kept for trends and reports."""
    __tablename__ = "snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)


def _engine():
    url = settings.database_url
    kwargs = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        import os
        path = url.split("///", 1)[-1]
        if path and path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(url, **kwargs)


engine = _engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)
