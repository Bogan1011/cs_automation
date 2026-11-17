"""Trend Micro support cases, read (never changed) from the case emails in a Microsoft 365 mailbox.

The Business Success Portal has no API and is only reachable through Vision One SSO, so instead of
scraping it we rebuild each case from the emails Trend sends for it: every message whose subject has a
case number (TM-12345678) updates that case's subject, status, last activity and sender.
"""
import logging
import re
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select

from .config import settings
from .db import Client, SessionLocal, SupportCase, SyncState
from . import outlook
from .outlook import GRAPH, _token

log = logging.getLogger(__name__)

CASE_RE = re.compile(r"\bTM-(\d{6,10})\b", re.I)
TAG_RE = re.compile(r"\[([A-Za-z0-9 &._-]{2,30})\]")
# Business Success Portal case states, longest first so "Waiting for Customer Verification" beats "Waiting".
STATUSES = sorted([
    "Waiting for Customer Verification", "Waiting for Customer Response", "Waiting for Customer",
    "Pending Customer Action", "Pending Customer", "Waiting for Trend Micro", "In Progress", "Work in Progress",
    "Escalated", "Reopened", "Re-opened", "Resolved", "Closed", "Cancelled", "New", "Open", "Submitted",
], key=len, reverse=True)
CLOSED = {"Resolved", "Closed", "Cancelled"}
STATUS_HINT = re.compile(r"status\W{0,20}(?:has been |was )?(?:changed |updated |set )?(?:to|:)", re.I)
SELECT = "subject,from,receivedDateTime,bodyPreview,webLink"


def is_open(status: str) -> bool:
    return status not in CLOSED


def case_number(subject: str) -> str | None:
    m = CASE_RE.search(subject or "")
    return f"TM-{m.group(1)}" if m else None


def find_status(text: str) -> str | None:
    """The case state named in an email, preferring one right after 'status ... to'."""
    text = text or ""
    hint = STATUS_HINT.search(text)
    for chunk in ([text[hint.end():hint.end() + 80]] if hint else []) + [text]:
        low = chunk.lower()
        hits = [(low.find(s.lower()), s) for s in STATUSES if s.lower() in low]
        if hits:
            s = min(hits, key=lambda h: (h[0], -len(h[1])))[1]
            return {"Re-opened": "Reopened", "Work in Progress": "In Progress", "Open": "In Progress",
                    "Submitted": "New"}.get(s, s)
    return None


def clean_subject(subject: str) -> str:
    """Drop RE:/FW:, the case number and Trend's wrapper words, keep the case title (with its [TAG])."""
    s = re.sub(r"^(\s*(re|fw|fwd|aw)\s*:\s*)+", "", subject or "", flags=re.I)
    s = CASE_RE.sub("", s)
    s = re.sub(r"(?i)\b(case( number| no\.?| #)?|trend ?micro|trendai|support)\b\s*[:#-]?", " ", s, count=2)
    return re.sub(r"\s{2,}", " ", s).strip(" -:|·") or (subject or "").strip()


def client_tags(c: Client) -> list[str]:
    tags = [t.strip().lower() for t in (c.case_tags or "").split(",") if t.strip()]
    if not tags:  # default: "IFL - India First Life" -> "ifl"; "Bandhan Life" -> "bandhan life"
        tags = [c.name.split(" - ")[0].strip().lower()]
    return tags


def match_client(subject: str, clients: list[Client]) -> int | None:
    found = {t.strip().lower() for t in TAG_RE.findall(subject or "")}
    low = (subject or "").lower()
    for c in clients:
        tags = client_tags(c)
        if found & set(tags) or any(len(t) > 4 and t in low for t in tags):
            return c.id
    return None


def _messages(http: httpx.Client, mailbox: str, since: datetime):
    token = _token(http)
    url = f"{GRAPH}{outlook.mailbox_path(mailbox)}/messages"
    params = {"$filter": f"receivedDateTime ge {since.strftime('%Y-%m-%dT%H:%M:%SZ')}",
              "$orderby": "receivedDateTime asc", "$select": SELECT, "$top": "100"}
    while url:
        r = http.get(url, params=params, headers={"Authorization": f"Bearer {token}"})
        if r.status_code >= 400:
            raise RuntimeError(f"Graph error {r.status_code}: {r.text[:300]}")
        data = r.json()
        yield from data.get("value", [])
        url, params = data.get("@odata.nextLink"), None


def _dt(s: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None
    except ValueError:
        return None


def _aware(d: datetime | None) -> datetime | None:
    return d.replace(tzinfo=timezone.utc) if d and d.tzinfo is None else d


def apply_message(session, m: dict, clients: list[Client]) -> str | None:
    """Fold one email into its case. Returns the case number, or None if the email isn't about a case."""
    subject = m.get("subject") or ""
    no = case_number(subject)
    if not no:
        return None
    when = _dt(m.get("receivedDateTime"))
    sender = ((m.get("from") or {}).get("emailAddress") or {})
    c = session.get(SupportCase, no)
    if c is None:
        c = SupportCase(case_no=no, status="New", opened_at=when, emails=0)
        session.add(c)
    c.emails = (c.emails or 0) + 1
    c.opened_at, c.last_update = _aware(c.opened_at), _aware(c.last_update)
    title = clean_subject(subject)
    if title and (not c.subject or TAG_RE.search(title)):
        c.subject = title[:500]
    if c.client_id is None:
        c.client_id = match_client(subject, clients)
    if c.opened_at is None or (when and when < c.opened_at):
        c.opened_at = when
    if when and (c.last_update is None or when >= c.last_update):
        c.last_update = when
        c.last_from = (sender.get("name") or sender.get("address") or "")[:200]
        c.last_preview = (m.get("bodyPreview") or "")[:600]
        c.last_link = m.get("webLink") or ""
        st = find_status(subject) or find_status(m.get("bodyPreview") or "")
        if st:
            c.status = st
        elif c.status in CLOSED and "trend" not in (sender.get("address") or "").lower():
            pass  # our own follow-up on a closed case doesn't reopen it; Trend's reply will say so
    return no


def sync_cases(transport=None) -> dict:
    """Read new case emails since the last run (first run: CASES_BACKFILL_DAYS back). Read-only on the mailbox."""
    mailbox = settings.cases_mailbox or settings.graph_mailbox
    if not outlook.configured() and not (settings.graph_client_secret and mailbox):
        return {"ok": False, "error": "Microsoft 365 not connected (see README section 3)"}
    with SessionLocal() as session:
        state = session.get(SyncState, "cases_since") or SyncState(key="cases_since", value="")
        since = _dt(state.value) or datetime.now(timezone.utc) - timedelta(days=settings.cases_backfill_days)
        clients = session.scalars(select(Client)).all()
        seen, newest = 0, since
        with httpx.Client(transport=transport, timeout=60) as http:
            for m in _messages(http, mailbox, since - timedelta(minutes=10)):
                when = _dt(m.get("receivedDateTime"))
                if when and when <= since:
                    continue  # already folded in on the previous run (we re-ask a 10-minute overlap)
                if apply_message(session, m, clients):
                    seen += 1
                if when and when > newest:
                    newest = when
        state.value = newest.isoformat()
        session.merge(state)
        # cases that arrived before their client's tag was set get matched later
        for c in session.scalars(select(SupportCase).where(SupportCase.client_id.is_(None))):
            c.client_id = match_client(c.subject, clients)
        session.commit()
    return {"ok": True, "emails": seen}


def run_safely() -> None:
    try:
        r = sync_cases()
        if r.get("ok") and r.get("emails"):
            log.info("support cases: %s new case emails", r["emails"])
    except Exception:
        log.exception("support case sync failed")
