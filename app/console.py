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

from . import articles, guides, kb, mailfile, onedrive, users
from .config import settings
from .db import DailyEntry, KnowledgeCase, LogFolder, SessionLocal, ToolLink, TrendArticle, WorkItem, WorkUpdate, utcnow

router = APIRouter()
ROOT = Path(__file__).resolve().parent.parent / "troubleshooting"
PRODUCTS = kb.PRODUCTS
SCRIPT_PRODUCTS = {k: PRODUCTS[k] for k in ("deep-security", "xdr", "apex-one")}
STATUSES = ["Open", "In progress", "Waiting on client", "Waiting on Trend", "Resolved"]
OPEN_STATUSES = STATUSES[:-1]
PRIORITIES = ["P1", "P2", "P3", "P4"]
LOG_KINDS = ["Call", "Email", "Remote session", "Meeting", "Case update", "Other"]
LOG_STATUSES = ["Resolved", "In progress", "Follow-up", "Waiting on client", "Escalated to Trend"]


def _local_tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(settings.timezone)
    except Exception:  # no tz database in the image: fall back to India time
        from datetime import timezone
        return timezone(timedelta(hours=5, minutes=30))


def local_now():
    from datetime import datetime
    return datetime.now(_local_tz())
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

SCRIPT_TITLES = {"agent-health": "Agent health check", "agent-connectivity": "Agent connectivity test",
                 "collect-diagnostics": "Collect diagnostic package", "sensor-health": "Sensor health check"}


SCRIPT_PURPOSE = {
    ("apex-one", "agent-connectivity"): "Agent offline or not updating: tests DNS, ports and HTTPS to the Apex One server",
    ("apex-one", "agent-health"): "Services, server address, pattern versions, listening port and recent errors",
    ("deep-security", "agent-health"): "Agent service, version, manager connection, recent errors and disk space",
    ("deep-security", "collect-diagnostics"): "Runs dsa_control -d and shows where the zip for Trend is saved",
    ("xdr", "sensor-health"): "Services, versions and the connection to your Vision One region, including TLS inspection",
}


def script_groups(scripts: list[dict]) -> list[dict]:
    """Windows and Linux versions of the same check on one row."""
    out: dict[tuple, dict] = {}
    for sc in scripts:
        g = out.setdefault((sc["product"], sc["core"]), {"product": sc["product"], "title": sc["title"],
                                                          "purpose": sc["purpose"], "variants": []})
        g["variants"].append(sc)
        if sc["platform"] == "Windows":
            g["purpose"] = sc["purpose"]
    for (prod, core), g in out.items():
        g["purpose"] = SCRIPT_PURPOSE.get((prod, core), g["purpose"])
    for g in out.values():
        g["variants"].sort(key=lambda v: v["platform"] != "Windows")
        g["purpose"] = re.sub(r"\s+for (Windows|Linux)$", "", g["purpose"])
    return list(out.values())

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
    core = re.sub(r"^(ds|xdr|apex)-|-(windows|linux)$", "", path.stem)
    core = re.sub(r"-(windows|linux)$", "", core)
    title = SCRIPT_TITLES.get(core, core.replace("-", " ").capitalize())
    purpose = re.sub(r"\s*\((read-only|re.*?)\)\.?$|\.$", "", summary).strip()
    return {"name": path.name, "product": path.parent.name, "platform": platform, "summary": summary,
            "core": core, "title": title, "purpose": purpose,
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
    counts = {k: sum(1 for g in script_groups(all_s) if g["product"] == k) for k in SCRIPT_PRODUCTS}
    return _render(request, "console/scripts.html", {
        "scripts": [s for s in all_s if not product or s["product"] == product],
        "groups": script_groups([s for s in all_s if not product or s["product"] == product]),
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


# ---------- troubleshooting guides ----------

@router.get("/troubleshooting", response_class=HTMLResponse)
def guides_page(request: Request, product: str = "", q: str = ""):
    _user(request)
    product = product if product in SCRIPT_PRODUCTS else ""
    every = guides.all_guides()
    counts = {k: sum(1 for g in every if g["product"] == k) for k in SCRIPT_PRODUCTS}
    shown = guides.search([g for g in every if not product or g["product"] == product], q)
    cats = []
    for g in shown:
        if g["category"] not in cats:
            cats.append(g["category"])
    return _render(request, "console/guides.html", {"guides": shown, "cats": cats, "product": product, "q": q,
                                                    "counts": counts, "total": len(every), "SCRIPT_PRODUCTS": SCRIPT_PRODUCTS})


@router.get("/troubleshooting/{product}/{name}", response_class=HTMLResponse)
def guide_page(request: Request, product: str, name: str):
    _user(request)
    g = guides.get(product, name)
    if not g:
        raise HTTPException(404)
    body, toc = guides.render(g["body"])
    scripts = [s for s in list_scripts(product) if s["name"] in g["scripts"]]
    more = [x for x in guides.all_guides() if x["product"] == product and x["slug"] != name]
    with SessionLocal() as s:
        tools = s.scalars(select(ToolLink).where(ToolLink.key.in_(g["tools"]))).all() if g["tools"] else []
    return _render(request, "console/guide.html", {"g": g, "body": body, "toc": toc, "scripts": scripts, "more": more,
                                                   "tools": tools})


# ---------- log upload folders (OneDrive) ----------

@router.get("/uploads", response_class=HTMLResponse)
def uploads_page(request: Request, made: int = 0, err: str = "", product: str = "", host: str = "", client: str = ""):
    _user(request)
    with SessionLocal() as s:
        rows = s.scalars(select(LogFolder).order_by(LogFolder.at.desc()).limit(50)).all()
        new = s.get(LogFolder, made) if made else None
        folders = onedrive.client_folders()
        known = {onedrive._key(f) for f in folders}
        others = sorted({c for c in set(_clients(s)) | {r.client for r in rows if r.client} if onedrive._key(c) not in known})
    msg = onedrive.message(new.client, new.host, new.upload_url or new.folder_url, new.trend_case) if new else ""
    return _render(request, "console/uploads.html", {
        "rows": rows, "new": new, "msg": msg, "err": err[:300], "folders": folders, "clients": others, "product": product,
        "host": host[:200], "client": client[:120], "connected": onedrive.ready(), "via_rclone": onedrive.rclone_ready(),
        "app_ready": onedrive.app_ready(), "remote": settings.onedrive_remote, "root": settings.onedrive_root, "tz": _local_tz()})


def _make_log_folder(me: str, client: str, host: str, trend_case: str, product: str) -> LogFolder:
    client, host, trend_case = client.strip()[:120], host.strip()[:200], trend_case.strip()[:40]
    if not client or not host:
        raise ValueError("Fill in the client and the server")
    r = onedrive.create_log_folder(client, host, trend_case)
    with SessionLocal() as s:
        row = LogFolder(author=me, client=client, host=host, trend_case=trend_case,
                        product=product if product in PRODUCTS else "other", **r)
        s.add(row)
        s.commit()
        s.refresh(row)
        s.expunge(row)
    return row


@router.post("/uploads")
def uploads_create(request: Request, client: str = Form(""), host: str = Form(""), trend_case: str = Form(""),
                   product: str = Form("other")):
    from urllib.parse import quote
    me = _user(request)
    try:
        row = _make_log_folder(me, client, host, trend_case, product)
    except Exception as e:  # not connected, expired sign-in or a OneDrive error: show it on the page
        return RedirectResponse("/uploads?err=" + quote(str(e)[:300]), status_code=303)
    return RedirectResponse(f"/uploads?made={row.id}", status_code=303)


@router.post("/uploads.json")
def uploads_create_json(request: Request, client: str = Form(""), host: str = Form(""), trend_case: str = Form(""),
                        product: str = Form("other")):
    """Same as the form, for the one-click button: returns the link so the page can copy it straight away."""
    from fastapi.responses import JSONResponse
    me = _user(request)
    try:
        row = _make_log_folder(me, client, host, trend_case, product)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)[:300]}, status_code=400)
    return {"ok": True, "id": row.id, "path": row.path, "upload_url": row.upload_url, "folder_url": row.folder_url,
            "link_kind": row.link_kind, "client": row.client, "host": row.host, "trend_case": row.trend_case,
            "author": row.author, "message": onedrive.message(row.client, row.host, row.upload_url or row.folder_url, row.trend_case)}


# ---------- work board ----------

def _clients(s) -> list[str]:
    names = set(s.scalars(select(WorkItem.client).distinct())) | set(s.scalars(select(KnowledgeCase.client).distinct()))
    return sorted(n for n in names if n)


# ---------- quick search (Ctrl+K) ----------

PAGES = [("Home", "/", "Page"), ("Daily log", "/log", "Page"), ("Open issues", "/work", "Page"),
         ("New work item", "/work#new", "Action"), ("Case library", "/library", "Page"),
         ("Add finished case", "/library/new", "Action"), ("Troubleshooting guides", "/troubleshooting", "Page"),
         ("Scripts", "/scripts", "Page"), ("Trend articles", "/articles", "Page"), ("Tools", "/tools", "Page"),
         ("Log upload link (OneDrive)", "/uploads", "Action")]


@router.get("/find")
def find(request: Request, q: str = ""):
    from fastapi.responses import JSONResponse
    _user(request)
    words = [w.lower() for w in q.split() if w.strip()][:6]
    out = []

    def hit(*texts):
        blob = " ".join(t or "" for t in texts).lower()
        return all(w in blob for w in words)

    def add(group, title, sub, url, limit):
        if sum(1 for x in out if x["group"] == group) < limit:
            out.append({"group": group, "title": title, "sub": sub, "url": url})

    for title, url, kind in PAGES:
        if not words or hit(title):
            add("Go to", title, kind, url, 10 if not words else 4)
    if not words:
        return JSONResponse(out)
    for g in guides.all_guides():
        if hit(g["title"], g["summary"], g["product"], g["os"], g["category"]):
            add("Guides", g["title"], f'{PRODUCTS.get(g["product"], "")} · {g["os"]}', f'/troubleshooting/{g["product"]}/{g["slug"]}', 6)
    for sc in list_scripts():
        if hit(sc["title"], sc["name"], sc["summary"], PRODUCTS.get(sc["product"], "")):
            add("Scripts", f'{sc["title"]} ({sc["platform"]})', f'{PRODUCTS.get(sc["product"], "")} · {sc["name"]}',
                f'/scripts/{sc["product"]}/{sc["name"]}', 6)
    like = [f"%{w}%" for w in words]
    with SessionLocal() as s:
        for t in s.scalars(select(ToolLink)):
            if hit(t.name, t.file_name, t.description, PRODUCTS.get(t.product, "")):
                add("Tools", t.name, t.file_name or PRODUCTS.get(t.product, ""), t.url, 5)
        st = select(KnowledgeCase)
        for l in like:
            st = st.where(or_(KnowledgeCase.title.ilike(l), KnowledgeCase.client.ilike(l), KnowledgeCase.case_no.ilike(l),
                              KnowledgeCase.issue.ilike(l), KnowledgeCase.solution.ilike(l)))
        for c in s.scalars(st.order_by(KnowledgeCase.id.desc()).limit(6)):
            add("Cases", c.title, " · ".join(x for x in (c.client, c.case_no) if x), f"/library/{c.id}", 6)
        st = select(TrendArticle)
        for l in like:
            st = st.where(or_(TrendArticle.title.ilike(l), TrendArticle.ka.ilike(l), TrendArticle.summary.ilike(l)))
        for a in s.scalars(st.order_by(TrendArticle.ka.desc()).limit(6)):
            add("Trend articles", articles._plain(a.title) or a.ka, f"{a.ka} · {a.kind}", a.url, 6)
        st = select(WorkItem)
        for l in like:
            st = st.where(or_(WorkItem.title.ilike(l), WorkItem.client.ilike(l), WorkItem.trend_case.ilike(l)))
        for w in s.scalars(st.order_by(WorkItem.updated_at.desc()).limit(5)):
            add("Open issues", w.title, f"{w.client or 'No client'} · {w.status}", f"/work/{w.id}", 5)
        st = select(DailyEntry)
        for l in like:
            st = st.where(or_(DailyEntry.summary.ilike(l), DailyEntry.client.ilike(l), DailyEntry.trend_case.ilike(l)))
        for e in s.scalars(st.order_by(DailyEntry.day.desc(), DailyEntry.at.desc()).limit(5)):
            add("Daily log", e.summary[:120], f"{e.day.strftime('%d %b')} · {e.client or '–'} · {e.author}",
                f"/log?q={'+'.join(words)}&range=quarter", 5)
    return JSONResponse(out)


# ---------- daily log ----------

RANGES = {"today": 0, "week": 6, "month": 29, "quarter": 89}


@router.get("/log", response_class=HTMLResponse)
def daily_log(request: Request, who: str = "", client: str = "", q: str = "", range: str = "week", edit: int = 0):
    me = _user(request)
    today = local_now().date()
    days = RANGES.get(range, 6)
    with SessionLocal() as s:
        stmt = select(DailyEntry).where(DailyEntry.day >= today - timedelta(days=days))
        if who:
            stmt = stmt.where(DailyEntry.author == (me if who == "me" else who))
        if client:
            stmt = stmt.where(DailyEntry.client == client)
        for word in q.split():
            like = f"%{word}%"
            stmt = stmt.where(or_(DailyEntry.summary.ilike(like), DailyEntry.client.ilike(like), DailyEntry.trend_case.ilike(like)))
        rows = s.scalars(stmt.order_by(DailyEntry.day.desc(), DailyEntry.at.desc())).all()
        groups = []
        for r in rows:
            if not groups or groups[-1][0] != r.day:
                groups.append((r.day, []))
            groups[-1][1].append(r)
        mine_today = s.scalar(select(func.count()).select_from(DailyEntry).where(DailyEntry.day == today, DailyEntry.author == me))
        clients = sorted(set(_clients(s)) | set(s.scalars(select(DailyEntry.client).distinct())) - {""})
        return _render(request, "console/log.html", {
            "groups": groups, "total": len(rows), "today": today, "who": who, "client": client, "q": q, "range": range,
            "clients": clients, "people": users.all_names(), "KINDS": LOG_KINDS, "LSTAT": LOG_STATUSES, "edit": edit,
            "mine_today": mine_today, "tz": _local_tz(), "utc": __import__("datetime").timezone.utc})


@router.post("/log")
def daily_log_add(request: Request, summary: str = Form(...), client: str = Form(""), product: str = Form("other"),
                  kind: str = Form("Call"), status: str = Form("Resolved"), trend_case: str = Form(""),
                  edit_id: int = Form(0), back: str = Form("/log")):
    me = _user(request)
    if not summary.strip():
        raise HTTPException(400, "Write what it was about")
    with SessionLocal() as s:
        e = s.get(DailyEntry, edit_id) if edit_id else None
        if e is None:
            e = DailyEntry(author=me, day=local_now().date())
            s.add(e)
        e.summary, e.client = summary.strip()[:2000], client.strip()[:120]
        e.product = product if product in PRODUCTS else "other"
        e.kind = kind if kind in LOG_KINDS else "Other"
        e.status = status if status in LOG_STATUSES else "Resolved"
        e.trend_case = trend_case.strip()[:40]
        s.commit()
    return RedirectResponse(back if back.startswith("/log") else "/log", status_code=303)


@router.post("/log/{entry_id}/delete")
def daily_log_delete(request: Request, entry_id: int):
    _user(request)
    with SessionLocal() as s:
        e = s.get(DailyEntry, entry_id)
        if e:
            s.delete(e)
            s.commit()
    return RedirectResponse("/log", status_code=303)


@router.get("/log.csv")
def daily_log_csv(request: Request, range: str = "month"):
    import csv
    import io
    _user(request)
    today = local_now().date()
    with SessionLocal() as s:
        rows = s.scalars(select(DailyEntry).where(DailyEntry.day >= today - timedelta(days=RANGES.get(range, 29)))
                         .order_by(DailyEntry.day, DailyEntry.at)).all()
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["Date", "Time", "Who", "Type", "Client", "Product", "What", "Status", "Trend case"])
        for r in rows:
            at = r.at if r.at.tzinfo else r.at.replace(tzinfo=__import__("datetime").timezone.utc)
            w.writerow([r.day.isoformat(), at.astimezone(_local_tz()).strftime("%H:%M"), r.author, r.kind, r.client,
                        PRODUCTS.get(r.product, r.product), r.summary, r.status, r.trend_case])
    return PlainTextResponse(buf.getvalue(), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="daily-log-{today}.csv"'})


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
            "q": q, "clients": _clients(s), "users": users.all_names() or [me]})


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
        return _render(request, "console/work_item.html", {"w": w, "users": sorted(set(users.all_names()) | {w.owner, me} - {""}),
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
def articles_page(request: Request, kind: str = "", q: str = "", synced: str = "", src: str = "", sort: str = ""):
    _user(request)
    with SessionLocal() as s:
        stmt = select(TrendArticle)
        if kind:
            stmt = stmt.where(TrendArticle.kind == kind)
        if src in articles.SOURCE_LABELS:
            stmt = stmt.where(TrendArticle.sources.ilike(f"%{src}%"))
        for word in q.split():
            like = f"%{word}%"
            stmt = stmt.where(or_(TrendArticle.title.ilike(like), TrendArticle.summary.ilike(like),
                                  TrendArticle.solution.ilike(like), TrendArticle.ka.ilike(like)))
        rows = s.scalars(stmt.order_by(TrendArticle.ka.desc()).limit(1000)).all()
        if sort == "views":
            rows.sort(key=lambda r: -(r.views or 0))
        ranked = sorted((r for r in rows if r.views), key=lambda r: -r.views)[:10]
        top = {r.ka for r in ranked}
        every = s.scalars(select(TrendArticle.sources)).all()
        src_counts = {k: sum(1 for x in every if k in (x or "").split()) for k in articles.SOURCE_LABELS}
        kinds = dict(s.execute(select(TrendArticle.kind, func.count()).group_by(TrendArticle.kind)).all())
        last = s.scalar(select(func.max(TrendArticle.first_seen)))
        week = utcnow() - timedelta(days=7)
        return _render(request, "console/articles.html", {
            "pinned": [r for r in rows if r.pinned], "rows": [r for r in rows if not r.pinned], "kind": kind, "q": q,
            "src": src, "sort": sort, "top": top, "kinds": kinds, "src_counts": src_counts, "total": len(every), "SOURCES": articles.SOURCE_LABELS,
            "synced": synced, "last": last, "week": week, "sources": settings.article_sources,
            "junk": articles.is_junk, "plain": articles._plain, "every_min": settings.article_interval_minutes})


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
              description: str = Form(""), file_name: str = Form(""), edit_id: int = Form(0)):
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
        t.file_name = file_name.strip()[:200]
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
