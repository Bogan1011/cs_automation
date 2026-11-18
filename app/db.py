from datetime import date, datetime, timezone

from sqlalchemy import (JSON, Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint,
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
    report_to: Mapped[str] = mapped_column(String(500), default="")
    # Agent installer share links: {"c1ws:windows": url, "c1ws_xdr:linux": url, ...}
    installers: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Subject tags that mark this client's Trend support cases, e.g. "ABC" for "[ABC] ..." (comma-separated)
    case_tags: Mapped[str] = mapped_column(String(200), default="")
    # Optional product-console APIs for exact pattern versions
    swp_url: Mapped[str] = mapped_column(String(300), default="")
    swp_key_enc: Mapped[str] = mapped_column(Text, default="")
    # Latest Server & Workload computer status list from the SWP API (replaced every poll, not kept as history)
    c1ws: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Latest Apex One (SEP) agent connection list from the Apex Central API (replaced every poll)
    apex_status: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    apex_url: Mapped[str] = mapped_column(String(300), default="")
    apex_app_id: Mapped[str] = mapped_column(String(200), default="")
    apex_key_enc: Mapped[str] = mapped_column(Text, default="")
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
    agent_version: Mapped[str] = mapped_column(String(80), default="")
    sensor_version: Mapped[str] = mapped_column(String(80), default="")
    component_version: Mapped[str] = mapped_column(String(80), default="")
    behind_latest: Mapped[bool] = mapped_column(Boolean, default=False)
    agent_version_status: Mapped[str] = mapped_column(String(40), default="")
    components: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # {"Virus Pattern": "19.123.00", ...}
    edr_status: Mapped[str] = mapped_column(String(40), default="")
    last_connected: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)

    client: Mapped[Client] = relationship(back_populates="endpoints")


class EndpointDetail(Base):
    """Cached exact pattern/engine versions per endpoint (from the single-endpoint API), refreshed on a rolling basis."""
    __tablename__ = "endpoint_details"
    __table_args__ = (UniqueConstraint("client_id", "agent_guid"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    agent_guid: Mapped[str] = mapped_column(String(80))
    components: Mapped[dict] = mapped_column(JSON, default=dict)
    # Detail-only "recommendedActions" (install/deploy problems). NULL = not fetched since this column was added.
    actions: Mapped[str | None] = mapped_column(Text, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SupportCase(Base):
    """A Trend Micro support case (TM-xxxxxxxx), rebuilt from the case emails in the Microsoft 365 mailbox."""
    __tablename__ = "support_cases"
    case_no: Mapped[str] = mapped_column(String(20), primary_key=True)
    client_id: Mapped[int | None] = mapped_column(ForeignKey("clients.id", ondelete="SET NULL"), nullable=True, index=True)
    subject: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(String(80), default="Open")
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_update: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_from: Mapped[str] = mapped_column(String(200), default="")
    last_preview: Mapped[str] = mapped_column(Text, default="")
    last_link: Mapped[str] = mapped_column(Text, default="")
    emails: Mapped[int] = mapped_column(Integer, default=0)


class SyncState(Base):
    """Small key/value store for sync watermarks (e.g. the newest case email already read)."""
    __tablename__ = "sync_state"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class Snapshot(Base):
    """Per-poll summary numbers, kept for trends and reports."""
    __tablename__ = "snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)


# ---- Internal Customer Success console (no client API data) ----

class WorkItem(Base):
    """An issue someone in the team is working on, so whoever picks it up knows where it stands."""
    __tablename__ = "work_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client: Mapped[str] = mapped_column(String(120), default="")
    product: Mapped[str] = mapped_column(String(30), default="other")
    title: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(40), default="Open")
    priority: Mapped[str] = mapped_column(String(10), default="P3")
    owner: Mapped[str] = mapped_column(String(80), default="")
    trend_case: Mapped[str] = mapped_column(String(40), default="")
    created_by: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updates: Mapped[list["WorkUpdate"]] = relationship(back_populates="item", cascade="all, delete-orphan",
                                                       order_by="WorkUpdate.at.desc()")


class WorkUpdate(Base):
    __tablename__ = "work_updates"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("work_items.id", ondelete="CASCADE"), index=True)
    author: Mapped[str] = mapped_column(String(80), default="")
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    note: Mapped[str] = mapped_column(Text, default="")
    change: Mapped[str] = mapped_column(String(200), default="")  # e.g. "Status: Open -> Waiting on Trend"
    item: Mapped[WorkItem] = relationship(back_populates="updates")


class KnowledgeCase(Base):
    """A finished case kept for the future: the issue and how it was solved."""
    __tablename__ = "knowledge_cases"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_no: Mapped[str] = mapped_column(String(40), default="")
    client: Mapped[str] = mapped_column(String(120), default="")
    product: Mapped[str] = mapped_column(String(30), default="other")
    title: Mapped[str] = mapped_column(String(300))
    issue: Mapped[str] = mapped_column(Text, default="")
    solution: Mapped[str] = mapped_column(Text, default="")
    root_cause: Mapped[str] = mapped_column(Text, default="")
    raw_email: Mapped[str] = mapped_column(Text, default="")
    closed_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    added_by: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class ToolLink(Base):
    """Trend Micro's official tools (OneDrive or Trend download links), in one place."""
    __tablename__ = "tool_links"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    url: Mapped[str] = mapped_column(String(1000))
    product: Mapped[str] = mapped_column(String(30), default="other")
    description: Mapped[str] = mapped_column(Text, default="")
    added_by: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TrendArticle(Base):
    """A Trend Business Success knowledge article (KA-xxxxxxx), found automatically."""
    __tablename__ = "trend_articles"
    ka: Mapped[str] = mapped_column(String(20), primary_key=True)
    title: Mapped[str] = mapped_column(String(400), default="")
    url: Mapped[str] = mapped_column(String(500), default="")
    kind: Mapped[str] = mapped_column(String(40), default="Article")
    product: Mapped[str] = mapped_column(String(30), default="xdr")
    summary: Mapped[str] = mapped_column(Text, default="")
    solution: Mapped[str] = mapped_column(Text, default="")
    published: Mapped[str] = mapped_column(String(40), default="")
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Which Business Success product page(s) list it ("vision-one apex-saas"), and whether it is in a page's
    # highlighted "Product advisories" list right now.
    sources: Mapped[str] = mapped_column(String(120), default="")
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    last_listed: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
    _add_missing_columns()


def _add_missing_columns() -> None:
    """Tiny forward-only migration: add columns introduced after a table was first created."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in existing:
                    continue
                ddl_type = col.type.compile(dialect=engine.dialect)
                default = col.default.arg if col.default is not None and not callable(col.default.arg) else None
                if isinstance(default, bool):
                    default_sql = " DEFAULT " + ("TRUE" if default else "FALSE")
                elif isinstance(default, str):
                    default_sql = " DEFAULT '" + default.replace("'", "''") + "'"
                else:
                    default_sql = ""
                conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {col.name} {ddl_type}{default_sql}'))
