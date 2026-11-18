"""Internal Customer Success console: troubleshooting scripts, work board, case library, Trend articles, tools.

Nothing here talks to a client's console or API. Client names on the work board and in the case library are
typed by the team and stay in this server's database.
"""
import re
from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from sqlalchemy import func, or_, select

from . import articles, kb, mailfile
from .config import settings
from .db import KnowledgeCase, SessionLocal, ToolLink, TrendArticle, WorkItem, WorkUpdate, utcnow

router = APIRouter()
ROOT = Path(__file__).resolve().parent.parent / "troubleshooting"
PRODUCTS = kb.PRODUCTS
SCRIPT_PRODUCTS = {k: PRODUCTS[k] for k in ("deep-security", "xdr", "apex-one")}
STATUSES = ["Open", "In progress", "Waiting on client", "Waiting on Trend", "Resolved"]
OPEN_STATUSES = STATUSES[:-1]
PRIORITIES = ["P1", "P2", "P3", "P4"]
templates = None  # set by main.py


def _user(request: Request) -> str:
    u = request.session.get("user")
    if not u:
        raise HTTPException(status_code=401)
    return u


def _render(request, name, ctx):
    ctx.update({"PRODUCTS": PRODUCTS, "STATUSES": STATUSES, "PRIORITIES": PRIORITIES, "me": request.session.get("user")})
    return templates.TemplateResponse(request, name, ctx)


# ---------- scripts ----------

def _script_info(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix == ".ps1":
        m = re.search(r"\.SYNOPSIS\s*\n\s*(.+)", text)
        desc = re.search(r"\.DESCRIPTION\s*\n(.*?)(?:\n\s*\.[A-Z]+|\n#>)", text, re.S)
        ex = re.search(r"\.EXAMPLE\s*\n\s*(.+)", text)
        platform, run = "Windows", (ex.group(1).strip() if ex else f"powershell -ExecutionPolicy Bypass -File .\\{path.name}")
        summary = m.group(1).strip() if m else path.stem
        details = re.sub(r"\n\s+", " ", desc.group(1)).strip() if desc else ""
    else:
        comments = [l[1:].strip() for l in text.splitlines()[1:12] if l.startswith("#")]
        usage = next((c.split(":", 1)[1].strip() for c in comments if c.lower().startswith("usage")), f"sudo bash {path.name}")
        platform, run = "Linux", usage
        summary = comments[0] if comments else path.stem
        details = " ".join(c for c in comments[1:] if not c.lower().startswith(("usage", "region:", "  ")))
    return {"name": path.name, "product": path.parent.name, "platform": platform, "summary": summary,
            "details": details, "run": run, "size": len(text), "lines": text.count("\n") + 1}


def list_scripts(product: str | None = None) -> list[dict]:
    out = []
    for p in sorted(ROOT.glob("*/*")):
        if p.suffix in (".ps1", ".sh") and p.parent.name in SCRIPT_PRODUCTS and (not product or p.parent.name == product):
            out.append(_script_info(p))
    return out


def _script_path(product: str, name: str) -> Path:
    if product not in SCRIPT_PRODUCTS or not re.fullmatch(r"[\w.-]+\.(ps1|sh)", name):
        raise HTTPException(404)
    p = ROOT / product / name
    if not p.is_file():
        raise HTTPException(404)
    return p


@router.get("/scripts", response_class=HTMLResponse)
def scripts_page(request: Request, product: str = ""):
    _user(request)
    product = product if product in SCRIPT_PRODUCTS else ""
    all_s = list_scripts()
    counts = {k: sum(1 for s in all_s if s["product"] == k) for k in SCRIPT_PRODUCTS}
    return _render(request, "console/scripts.html", {
        "scripts": [s for s in all_s if not product or s["product"] == product],
        "product": product, "counts": counts, "SCRIPT_PRODUCTS": SCRIPT_PRODUCTS})


@router.get("/scripts/{product}/{name}", response_class=HTMLResponse)
def script_view(request: Request, product: str, name: str):
    _user(request)
    p = _script_path(product, name)
    return _render(request, "console/script.html", {"s": _script_info(p), "code": p.read_text(encoding="utf-8")})


@router.get("/scripts/{product}/{name}/raw")
def script_raw(request: Request, product: str, name: str, download: int = 0):
    _user(request)
    p = _script_path(product, name)
    headers = {"Content-Disposition": f'attachment; filename="{p.name}"'} if download else {}
    return PlainTextResponse(p.read_text(encoding="utf-8"), headers=headers)


# ---------- work board ----------

def _clients(s) -> list[str]:
    names = set(s.scalars(select(WorkItem.client).distinct())) | set(s.scalars(select(KnowledgeCase.client).distinct()))
    return sorted(n for n in names if n)


@router.get("/work", response_class=HTMLResponse)
def work_board(request: Request, status: str = "open", product: str = "", owner: str = "", q: str = ""):
    me = _user(request)
    with SessionLocal() as s:
        stmt = select(WorkItem)
        if status == "open":
            stmt = stmt.where(WorkItem.status.in_(OPEN_STATUSES))
        elif status in STATUSES:
            stmt = stmt.where(WorkItem.status == status)
        if product in PRODUCTS:
            stmt = stmt.where(WorkItem.product == product)
        if owner:
            stmt = stmt.where(WorkItem.owner == (me if owner == "me" else owner))
        if q:
            like = f"%{q}%"
            stmt = stmt.where(or_(WorkItem.title.ilike(like), WorkItem.client.ilike(like), WorkItem.trend_case.ilike(like)))
        items = s.scalars(stmt.order_by(WorkItem.priority, WorkItem.updated_at.desc())).all()
        last = {i.id: (i.updates[0] if i.updates else None) for i in items}
        counts = dict(s.execute(select(WorkItem.status, func.count()).group_by(WorkItem.status)).all())
        return _render(request, "console/work.html", {
            "items": items, "last": last, "counts": counts, "open_total": sum(counts.get(x, 0) for x in OPEN_STATUSES), "status": status, "product": product, "owner": owner,
            "q": q, "clients": _clients(s), "users": sorted(settings.users) or [me]})


@router.post("/work")
def work_create(request: Request, title: str = Form(...), client: str = Form(""), product: str = Form("other"),
                priority: str = Form("P3"), owner: str = Form(""), trend_case: str = Form(""), note: str = Form("")):
    me = _user(request)
    with SessionLocal() as s:
        w = WorkItem(title=title.strip()[:300], client=client.strip()[:120], product=product if product in PRODUCTS else "other",
                     priority=priority if priority in PRIORITIES else "P3", owner=(owner or me).strip()[:80],
                     trend_case=trend_case.strip()[:40], created_by=me)
        s.add(w)
        s.flush()
        s.add(WorkUpdate(item_id=w.id, author=me, note=note.strip(), change="Created"))
        s.commit()
        return RedirectResponse(f"/work/{w.id}", status_code=303)


@router.get("/work/{item_id}", response_class=HTMLResponse)
def work_item(request: Request, item_id: int):
    me = _user(request)
    with SessionLocal() as s:
        w = s.get(WorkItem, item_id)
        if not w:
            raise HTTPException(404)
        _ = w.updates
        return _render(request, "console/work_item.html", {"w": w, "users": sorted(set(settings.users) | {w.owner, me} - {""}),
                                                            "clients": _clients(s)})


@router.post("/work/{item_id}")
def work_update(request: Request, item_id: int, note: str = Form(""), status: str = Form(""), owner: str = Form(""),
                priority: str = Form(""), trend_case: str = Form(""), client: str = Form(None), title: str = Form(None)):
    me = _user(request)
    with SessionLocal() as s:
        w = s.get(WorkItem, item_id)
        if not w:
            raise HTTPException(404)
        changes = []
        for field, new, allowed in (("status", status, STATUSES), ("owner", owner.strip(), None),
                                    ("priority", priority, PRIORITIES), ("trend_case", trend_case.strip(), None),
                                    ("client", (client or "").strip() if client is not None else None, None),
                                    ("title", (title or "").strip() if title is not None else None, None)):
            if new is None or (allowed and new not in allowed):
                continue
            old = getattr(w, field)
            if new != old and (new or field in ("trend_case",)):
                label = {"trend_case": "Trend case", "status": "Status", "owner": "Owner", "priority": "Priority",
                         "client": "Client", "title": "Title"}[field]
                changes.append(f"{label}: {old or '–'} → {new or '–'}")
                setattr(w, field, new)
        if note.strip() or changes:
            s.add(WorkUpdate(item_id=w.id, author=me, note=note.strip(), change="; ".join(changes)))
            w.updated_at = utcnow()
        s.commit()
        if w.status == "Resolved" and "Status" in " ".join(changes):
            return RedirectResponse(f"/work/{w.id}?resolved=1", status_code=303)
    return RedirectResponse(f"/work/{item_id}", status_code=303)


@router.post("/work/{item_id}/delete")
def work_delete(request: Request, item_id: int):
    _user(request)
    with SessionLocal() as s:
        w = s.get(WorkItem, item_id)
        if w:
            s.delete(w)
            s.commit()
    return RedirectResponse("/work", status_code=303)


# ---------- case library ----------

@router.get("/library", response_class=HTMLResponse)
def library(request: Request, q: str = "", product: str = "", client: str = ""):
    _user(request)
    with SessionLocal() as s:
        stmt = select(KnowledgeCase)
        if product in PRODUCTS:
            stmt = stmt.where(KnowledgeCase.product == product)
        if client:
            stmt = stmt.where(KnowledgeCase.client == client)
        for word in q.split():
            like = f"%{word}%"
            stmt = stmt.where(or_(KnowledgeCase.title.ilike(like), KnowledgeCase.issue.ilike(like),
                                  KnowledgeCase.solution.ilike(like), KnowledgeCase.root_cause.ilike(like),
                                  KnowledgeCase.case_no.ilike(like), KnowledgeCase.client.ilike(like)))
        rows = s.scalars(stmt.order_by(KnowledgeCase.closed_on.desc().nullslast(), KnowledgeCase.created_at.desc())).all()
        counts = dict(s.execute(select(KnowledgeCase.product, func.count()).group_by(KnowledgeCase.product)).all())
        by_client = s.execute(select(KnowledgeCase.client, func.count()).group_by(KnowledgeCase.client)
                              .order_by(KnowledgeCase.client)).all()
        return _render(request, "console/library.html", {"rows": rows, "q": q, "product": product, "client": client,
                                                          "counts": counts, "clients": _clients(s), "by_client": by_client,
                                                          "total": sum(n for _, n in by_client)})


def _blank_case() -> dict:
    return {"case_no": "", "client": "", "product": "other", "title": "", "issue": "", "root_cause": "",
            "solution": "", "raw_email": "", "closed_on": date.today().isoformat()}


@router.get("/library/new", response_class=HTMLResponse)
def library_new(request: Request, from_work: int = 0):
    _user(request)
    c = _blank_case()
    with SessionLocal() as s:
        w = s.get(WorkItem, from_work) if from_work else None
        if w:
            notes = [u.note for u in reversed(w.updates) if u.note]
            c.update(case_no=w.trend_case, client=w.client, product=w.product, title=w.title,
                     issue=notes[0] if notes else w.title, solution=notes[-1] if len(notes) > 1 else "")
        return _render(request, "console/library_form.html", {"c": c, "edit_id": None, "clients": _clients(s),
                                                               "from_work": from_work})


@router.post("/library/read-email", response_class=HTMLResponse)
async def library_read_email(request: Request, raw_email: str = Form(""), subject: str = Form(""), client: str = Form(""),
                             mail: UploadFile | None = File(None)):
    """Fill the form from an uploaded mail file (.msg / .eml) or pasted text. Nothing is saved yet."""
    _user(request)
    c = _blank_case()
    error, upload = None, None
    if mail is not None and mail.filename:
        try:
            upload = mailfile.read_upload(mail.filename, await mail.read())
            subject = upload["subject"] or subject
            raw_email = (f"Subject: {subject}\n" if subject else "") + upload["body"]
        except mailfile.MailFileError as exc:
            error = str(exc)
        except Exception:  # a damaged file should never break the page
            error = "Couldn't read that file. Try saving the mail again from Outlook, or paste the text instead."
    got = kb.summarize(raw_email, subject, (upload or {}).get("sender", "")) if raw_email.strip() else None
    if got:
        c.update({k: v for k, v in got.items() if v})
    if upload and upload.get("date"):
        c["closed_on"] = upload["date"].date().isoformat()
    c.pop("client_tag", None)
    with SessionLocal() as s:
        known = _clients(s)
        tag = (got or {}).get("client_tag", "")
        if not client and tag:  # [IFL] in the subject -> the existing client "IFL" (or "IFL - ...") else "IFL"
            client = next((k for k in known if k.lower() == tag.lower() or k.lower().startswith(tag.lower() + " ")), tag)
        c.update(raw_email=raw_email, client=client)
        return _render(request, "console/library_form.html", {"c": c, "edit_id": None, "clients": known,
                                                               "read": got, "from_work": 0, "error": error,
                                                               "filename": mail.filename if mail else ""})


def _parse_date(v: str):
    try:
        return date.fromisoformat(v) if v else None
    except ValueError:
        return None


@router.post("/library")
def library_save(request: Request, title: str = Form(...), case_no: str = Form(""), client: str = Form(""),
                 product: str = Form("other"), issue: str = Form(""), root_cause: str = Form(""),
                 solution: str = Form(""), raw_email: str = Form(""), closed_on: str = Form(""),
                 edit_id: int = Form(0), from_work: int = Form(0)):
    me = _user(request)
    with SessionLocal() as s:
        c = s.get(KnowledgeCase, edit_id) if edit_id else None
        if c is None:
            c = KnowledgeCase(added_by=me)
            s.add(c)
        c.title, c.case_no, c.client = title.strip()[:300], case_no.strip()[:40], client.strip()[:120]
        c.product = product if product in PRODUCTS else "other"
        c.issue, c.root_cause, c.solution = issue.strip(), root_cause.strip(), solution.strip()
        c.raw_email, c.closed_on = raw_email, _parse_date(closed_on)
        if from_work:
            w = s.get(WorkItem, from_work)
            if w:
                s.add(WorkUpdate(item_id=w.id, author=me, change="Saved to the case library"))
        s.commit()
        return RedirectResponse(f"/library/{c.id}", status_code=303)


@router.get("/library/{case_id}", response_class=HTMLResponse)
def library_item(request: Request, case_id: int, edit: int = 0):
    _user(request)
    with SessionLocal() as s:
        c = s.get(KnowledgeCase, case_id)
        if not c:
            raise HTTPException(404)
        if edit:
            d = {k: getattr(c, k) or "" for k in _blank_case()}
            d["closed_on"] = c.closed_on.isoformat() if c.closed_on else ""
            return _render(request, "console/library_form.html", {"c": d, "edit_id": c.id, "clients": _clients(s), "from_work": 0})
        similar = []
        words = [w for w in re.findall(r"[A-Za-z][\w-]{4,}", c.title) if w.lower() not in {"error", "issue", "agent", "client"}][:4]
        if words:
            similar = s.scalars(select(KnowledgeCase).where(KnowledgeCase.id != c.id,
                                                             or_(*[KnowledgeCase.title.ilike(f"%{w}%") for w in words])).limit(5)).all()
        return _render(request, "console/library_item.html", {"c": c, "similar": similar})


@router.get("/library/{case_id}/mail", response_class=HTMLResponse)
def library_mail(request: Request, case_id: int):
    """The full mail trail of a case, shown in a pop-up."""
    _user(request)
    with SessionLocal() as s:
        c = s.get(KnowledgeCase, case_id)
        if not c:
            raise HTTPException(404)
        return _render(request, "console/_mail.html", {"c": c, "msgs": kb.trail(c.raw_email or "")})


@router.post("/library/{case_id}/delete")
def library_delete(request: Request, case_id: int):
    _user(request)
    with SessionLocal() as s:
        c = s.get(KnowledgeCase, case_id)
        if c:
            s.delete(c)
            s.commit()
    return RedirectResponse("/library", status_code=303)


# ---------- Trend articles ----------

@router.get("/articles", response_class=HTMLResponse)
def articles_page(request: Request, kind: str = "", q: str = "", synced: str = ""):
    _user(request)
    with SessionLocal() as s:
        stmt = select(TrendArticle)
        if kind:
            stmt = stmt.where(TrendArticle.kind == kind)
        for word in q.split():
            like = f"%{word}%"
            stmt = stmt.where(or_(TrendArticle.title.ilike(like), TrendArticle.summary.ilike(like),
                                  TrendArticle.solution.ilike(like), TrendArticle.ka.ilike(like)))
        rows = s.scalars(stmt.order_by(TrendArticle.ka.desc()).limit(200)).all()
        kinds = dict(s.execute(select(TrendArticle.kind, func.count()).group_by(TrendArticle.kind)).all())
        last = s.scalar(select(func.max(TrendArticle.first_seen)))
        week = utcnow() - timedelta(days=7)
        return _render(request, "console/articles.html", {"rows": rows, "kind": kind, "q": q, "kinds": kinds,
                                                           "synced": synced, "last": last, "week": week,
                                                           "sources": settings.article_sources})


@router.post("/articles/sync")
def articles_sync(request: Request):
    _user(request)
    try:
        n = articles.sync()
        msg = f"{n} new article{'s' if n != 1 else ''}"
    except Exception as exc:  # shown on the page, not raised
        msg = f"Could not read Trend's site: {exc}"
    return RedirectResponse(f"/articles?synced={msg}", status_code=303)


# ---------- tools ----------

@router.get("/tools", response_class=HTMLResponse)
def tools_page(request: Request):
    _user(request)
    with SessionLocal() as s:
        rows = s.scalars(select(ToolLink).order_by(ToolLink.product, ToolLink.name)).all()
        return _render(request, "console/tools.html", {"rows": rows})


@router.post("/tools")
def tools_add(request: Request, name: str = Form(...), url: str = Form(...), product: str = Form("other"),
              description: str = Form(""), edit_id: int = Form(0)):
    me = _user(request)
    if not re.match(r"^https?://", url.strip(), re.I):
        raise HTTPException(400, "The link must start with https://")
    with SessionLocal() as s:
        t = s.get(ToolLink, edit_id) if edit_id else None
        if t is None:
            t = ToolLink(added_by=me)
            s.add(t)
        t.name, t.url = name.strip()[:160], url.strip()[:1000]
        t.product = product if product in PRODUCTS else "other"
        t.description = description.strip()
        s.commit()
    return RedirectResponse("/tools", status_code=303)


@router.post("/tools/{tool_id}/delete")
def tools_delete(request: Request, tool_id: int):
    _user(request)
    with SessionLocal() as s:
        t = s.get(ToolLink, tool_id)
        if t:
            s.delete(t)
            s.commit()
    return RedirectResponse("/tools", status_code=303)


# ---------- overview ----------

def overview(request: Request):
    me = _user(request)
    with SessionLocal() as s:
        open_items = s.scalars(select(WorkItem).where(WorkItem.status.in_(OPEN_STATUSES))
                               .order_by(WorkItem.priority, WorkItem.updated_at.desc())).all()
        last = {i.id: (i.updates[0] if i.updates else None) for i in open_items}
        by_status = {st: sum(1 for i in open_items if i.status == st) for st in OPEN_STATUSES}
        by_product = {p: sum(1 for i in open_items if i.product == p) for p in PRODUCTS}
        stale = [i for i in open_items if (utcnow() - _aware(i.updated_at)).days >= 3]
        latest = s.scalars(select(TrendArticle).order_by(TrendArticle.ka.desc()).limit(6)).all()
        alerts = s.scalars(select(TrendArticle).where(TrendArticle.kind.in_(["Security alert", "Advisory"]))
                           .order_by(TrendArticle.ka.desc()).limit(4)).all()
        recent_cases = s.scalars(select(KnowledgeCase).order_by(KnowledgeCase.created_at.desc()).limit(5)).all()
        n_cases = s.scalar(select(func.count()).select_from(KnowledgeCase))
        n_tools = s.scalar(select(func.count()).select_from(ToolLink))
        scripts = list_scripts()
        return _render(request, "console/home.html", {
            "mine": [i for i in open_items if i.owner == me], "open_items": open_items, "last": last,
            "by_status": by_status, "by_product": by_product, "stale": stale, "latest": latest, "alerts": alerts,
            "recent_cases": recent_cases, "n_cases": n_cases, "n_tools": n_tools,
            "script_counts": {k: sum(1 for x in scripts if x["product"] == k) for k in SCRIPT_PRODUCTS},
            "SCRIPT_PRODUCTS": SCRIPT_PRODUCTS})


def _aware(dt):
    from datetime import timezone
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)
