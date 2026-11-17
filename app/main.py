import hmac
from datetime import date, datetime, timedelta, timezone
import logging
import re
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from starlette.middleware.sessions import SessionMiddleware

from . import cases, collector, outlook, reports
from .config import settings
from .db import Client, Endpoint, SessionLocal, Snapshot, SupportCase, init_db, utcnow

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
BASE = Path(__file__).parent
templates = Jinja2Templates(directory=BASE / "templates")


def nav_clients():
    """Client tabs shown in the header on every page."""
    with SessionLocal() as s:
        return [{"id": c.id, "name": c.name, "ok": c.last_poll_ok}
                for c in s.scalars(select(Client).where(Client.enabled.is_(True)).order_by(Client.name))]


templates.env.globals["nav_clients"] = nav_clients


def _asset_version() -> str:
    """Changes whenever a static file changes, so browsers never keep an old stylesheet after an update."""
    import hashlib
    h = hashlib.sha1()
    for f in sorted((BASE / "static").glob("*.*")):
        h.update(f.read_bytes())
    return h.hexdigest()[:10]


templates.env.globals["asset_v"] = _asset_version()
scheduler = BackgroundScheduler(timezone="UTC")
_poll_lock = threading.Lock()


def run_poll():
    if not _poll_lock.acquire(blocking=False):
        return
    try:
        collector.collect_all()
    finally:
        _poll_lock.release()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if settings.enable_scheduler:
        scheduler.add_job(run_poll, "interval", seconds=settings.poll_interval, id="poll",
                          max_instances=1, coalesce=True, next_run_time=datetime.now(timezone.utc))
        scheduler.add_job(cases.run_safely, "interval", seconds=settings.poll_interval, id="cases",
                          max_instances=1, coalesce=True, next_run_time=datetime.now(timezone.utc))
        scheduler.start()
    yield
    if scheduler.running:
        scheduler.shutdown(wait=False)


app = FastAPI(title="Centralised Vision One", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret, same_site="strict",
                   max_age=12 * 3600, https_only=False)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")


def require_login(request: Request):
    if not request.session.get("user"):
        raise HTTPException(status_code=401)


@app.exception_handler(401)
async def unauthorized(request: Request, exc):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "login required"}, status_code=401)
    return RedirectResponse("/login", status_code=303)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    ok = (settings.dashboard_password
          and hmac.compare_digest(username, settings.dashboard_user)
          and hmac.compare_digest(password, settings.dashboard_password))
    if not ok:
        return templates.TemplateResponse(request, "login.html", {"error": "Wrong username or password"},
                                          status_code=401)
    request.session["user"] = username
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


def _client_card(session, c: Client, latest: dict | None = None) -> dict:
    snap = reports.latest_summary(session, c.id)
    patterns_behind = reports.endpoints_with_old_patterns(session, c.id, latest) if latest else None
    return {
        "patterns_behind": patterns_behind,
        "id": c.id, "name": c.name, "region": c.region.upper(),
        "last_poll_at": c.last_poll_at.isoformat() if c.last_poll_at else None,
        "last_poll_ok": c.last_poll_ok, "last_error": c.last_error,
        "summary": snap.summary if snap else None,
    }


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    """Home: a short status card per client. Kept separate from the client tabs so widgets can be added later."""
    require_login(request)
    with SessionLocal() as s:
        cards = []
        for c in s.scalars(select(Client).where(Client.enabled.is_(True)).order_by(Client.name)):
            card = _client_card(s, c)
            eps = s.scalars(select(Endpoint).where(Endpoint.client_id == c.id)).all()
            card["inv"] = collector.inventory(eps, card["summary"])
            card["conn"] = _connectivity(c)
            card["c1ws_top"] = _c1ws_top(c.c1ws, n=50)
            card["sep_trend"] = _sep_trend(s, c.id)
            cards.append(card)
    return templates.TemplateResponse(request, "index.html", {"cards": cards, "fleet": _fleet(cards)})


def _sep_trend(s, client_id: int, days: int = 7, points: int = 28) -> list[float]:
    """SEP compliance % over the last week, thinned to about `points` values for a sparkline."""
    snaps = s.scalars(select(Snapshot).where(Snapshot.client_id == client_id,
                                             Snapshot.taken_at >= utcnow() - timedelta(days=days))
                      .order_by(Snapshot.taken_at)).all()
    vals = [x for x in (((sn.summary or {}).get("sep_widget") or {}).get("pct") for sn in snaps) if x is not None]
    step = max(1, len(vals) // points)
    return vals[::step][-points:] if len(vals) > 1 else []


def _fleet(cards: list[dict]) -> dict:
    """Cross-client numbers for the Home widgets. Online/offline comes from each product's own console."""
    from collections import Counter, defaultdict
    f = {"managed": 0, "types": Counter(), "sep": Counter(), "c1ws": Counter(), "attention": Counter(),
         "online": Counter(), "offline": Counter(), "issues": defaultdict(list), "clients": len(cards)}
    for c in cards:
        dep = {k: n for k, _, n in c["inv"]["deployment"]}
        f["managed"] += dep.get("all", 0)
        for k in ("SEP", "SWP", "Sensor only"):
            f["types"][k] += dep.get(k, 0)
        sw = (c["summary"] or {}).get("sep_widget") or {}
        for k in ("compliant", "outdated", "offline7"):
            f["sep"][k] += sw.get(k) or 0
        for k, n in ((c["summary"] or {}).get("c1ws") or {}).items():
            f["c1ws"][k] += n or 0
        for k, _, n in c["inv"]["immediate"] + c["inv"]["recommended"]:
            if k != "unmanaged":
                f["attention"][k] += n
        for prod, v in c["conn"].items():
            if v.get("total") is not None:
                f["online"][prod] += v["online"]
                f["offline"][prod] += v["offline"]
        for issue, n in c["c1ws_top"]:
            f["issues"][issue].append((c["id"], c["name"], n))
    rated = f["sep"]["compliant"] + f["sep"]["outdated"]
    f["sep_pct"] = round(100 * f["sep"]["compliant"] / rated, 1) if rated else None
    f["top_issues"] = sorted(((i, sum(n for *_, n in v), v) for i, v in f["issues"].items()),
                             key=lambda x: -x[1])[:6]
    f["attention_total"] = sum(f["attention"].values())
    return f


@app.get("/api/overview")
def api_overview(request: Request):
    require_login(request)
    with SessionLocal() as s:
        clients = s.scalars(select(Client).where(Client.enabled.is_(True)).order_by(Client.name)).all()
        latest = reports.global_latest(s)
        return {"clients": [_client_card(s, c, latest) for c in clients], "latest_patterns": latest}


@app.get("/clients/{client_id}", response_class=HTMLResponse)
def client_page(request: Request, client_id: int):
    require_login(request)
    with SessionLocal() as s:
        c = s.get(Client, client_id) or _404()
        latest = reports.global_latest(s)
        endpoints = s.scalars(select(Endpoint).where(Endpoint.client_id == c.id).order_by(Endpoint.name)).all()
        history = s.scalars(select(Snapshot).where(Snapshot.client_id == c.id)
                            .order_by(Snapshot.taken_at.desc()).limit(288)).all()
        card = _client_card(s, c, latest)
        sep = dict((card["summary"] or {}).get("sep_widget") or {})
        if sep:
            week = s.scalars(select(Snapshot).where(Snapshot.client_id == c.id,
                                                    Snapshot.taken_at <= utcnow() - timedelta(days=7))
                             .order_by(Snapshot.taken_at.desc()).limit(1)).first()
            old_pct = ((week.summary if week else {}).get("sep_widget") or {}).get("pct")
            sep["change"] = round(sep["pct"] - old_pct, 1) if sep.get("pct") is not None and old_pct is not None else None
        return templates.TemplateResponse(request, "client.html", {
            "client": card, "endpoints": endpoints,
            "inv": collector.inventory(endpoints, card["summary"]),
            "sep": sep,
            "c1ws": c.c1ws, "c1ws_top": _c1ws_top(c.c1ws), "swp_connected": bool(c.swp_url and c.swp_key_enc),
            "conn": _connectivity(c),
            "open_cases": sum(1 for x in s.scalars(select(SupportCase.status).where(SupportCase.client_id == c.id))
                              if cases.is_open(x)),
            "flags": {e.id: collector.inventory_flags(e) for e in endpoints},
            "patterns": collector.pattern_status(card["summary"] or {}, latest),
            "behind": {e.id: collector.components_behind(e.components, latest.get(e.protection, {})) for e in endpoints},
            "history": [{"t": h.taken_at.isoformat(), "total": h.summary.get("total", 0),
                         "online": h.summary.get("online", 0)} for h in reversed(history)],
            "graph_configured": outlook.configured(), "report_to": c.report_to,
        })


def _c1ws_top(data: dict | None, n: int = 4) -> list[tuple[str, int]]:
    """Most common problems on critical and warning computers."""
    from collections import Counter
    comps = (data or {}).get("computers") or []
    return Counter(i for x in comps if x["status"] in ("critical", "warning") for i in x.get("issues") or []).most_common(n)


def _connectivity(c: Client) -> dict:
    """Online / offline per product, from each product's own console (not Vision One's sensor view)."""
    from datetime import datetime as _dt
    out = {}
    apex = c.apex_status or {}
    if apex.get("agents") is not None:
        agents = apex["agents"]
        on = sum(1 for a in agents if a["online"])
        out["SEP"] = {"source": "Apex One console", "total": len(agents), "online": on, "offline": len(agents) - on,
                      "stale": None, "error": apex.get("error")}
    else:
        out["SEP"] = {"source": None, "error": apex.get("error")}
    c1 = c.c1ws or {}
    if c1.get("computers") is not None:
        managed = [x for x in c1["computers"] if x["status"] != "unmanaged"]
        online = [x for x in managed if x.get("online", collector.c1ws_online(x["status"], x.get("issues") or []))]
        cutoff = utcnow() - timedelta(days=settings.stale_days)
        stale = sum(1 for x in managed if x not in online and x.get("last_seen")
                    and _dt.fromisoformat(x["last_seen"]) < cutoff)
        out["SWP"] = {"source": "Server & Workload console", "total": len(managed), "online": len(online),
                      "offline": len(managed) - len(online), "stale": stale, "error": c1.get("error")}
    else:
        out["SWP"] = {"source": None, "error": c1.get("error")}
    return out


C1WS_LABELS = {"critical": "Critical", "warning": "Warning", "managed": "Managed", "unmanaged": "Unmanaged"}
C1WS_TABS = {**C1WS_LABELS, "online": "Online", "offline": "Offline", "all": "All"}


@app.get("/clients/{client_id}/c1ws", response_class=HTMLResponse)
def c1ws_page(request: Request, client_id: int, status: str = "critical", issue: str = ""):
    """Server & Workload computers by status: one compact row each, with the problem on that computer."""
    require_login(request)
    with SessionLocal() as s:
        c = s.get(Client, client_id) or _404()
        data = c.c1ws or {}
    computers = [dict(x, online=x.get("online", collector.c1ws_online(x["status"], x.get("issues") or [])))
                 for x in data.get("computers") or []]
    status = status if status in C1WS_TABS else "critical"
    tab_counts = {k: sum(1 for x in computers if _c1ws_match(x, k)) for k in C1WS_TABS}
    rows = [x for x in computers if _c1ws_match(x, status)]
    if issue:
        rows = [x for x in rows if issue in (x.get("issues") or [])]
    order = {"critical": 0, "warning": 1, "managed": 2, "unmanaged": 3}
    rows.sort(key=lambda x: (order.get(x["status"], 9), x["name"].lower()))
    return templates.TemplateResponse(request, "c1ws.html", {
        "client": {"id": c.id, "name": c.name}, "rows": rows, "status": status, "issue": issue,
        "tabs": C1WS_TABS, "tab_counts": tab_counts, "labels": C1WS_LABELS,
        "fetched_at": data.get("fetched_at"), "error": data.get("error"),
    })


def _c1ws_match(x: dict, tab: str) -> bool:
    if tab == "all":
        return True
    if tab == "online":
        return x["online"]
    if tab == "offline":
        return x["status"] != "unmanaged" and not x["online"]  # managed agents that are offline
    return x["status"] == tab


INSTALLER_KINDS = {
    "c1ws": ("Server & Workload agent only", "Installs the Server & Workload Protection (C1WS) agent."),
    "c1ws_xdr": ("Server & Workload agent + XDR sensor", "Installs the C1WS agent with the Vision One XDR sensor."),
    "xdr": ("XDR sensor only", "Installs only the Vision One XDR sensor."),
}
INSTALLER_OS = {"windows": "Windows", "linux": "Linux"}


def _valid_share_link(url: str) -> bool:
    from urllib.parse import urlparse
    u = urlparse(url.strip())
    return u.scheme == "https" and bool(u.netloc) and (u.netloc.endswith(".sharepoint.com")
                                                     or u.netloc.endswith("onedrive.live.com")
                                                     or u.netloc == "1drv.ms")


CASE_TABS = {"open": "Open", "closed": "Resolved / closed", "all": "All"}


@app.get("/clients/{client_id}/cases", response_class=HTMLResponse)
def cases_page(request: Request, client_id: int, tab: str = "open", synced: str = ""):
    """Trend support cases for this client, rebuilt from the case emails (read-only)."""
    require_login(request)
    tab = tab if tab in CASE_TABS else "open"
    with SessionLocal() as s:
        c = s.get(Client, client_id) or _404()
        rows = s.scalars(select(SupportCase).where(SupportCase.client_id == c.id)
                         .order_by(SupportCase.last_update.desc())).all()
        counts = {"open": sum(1 for r in rows if cases.is_open(r.status)), "all": len(rows)}
        counts["closed"] = counts["all"] - counts["open"]
        shown = [r for r in rows if tab == "all" or (tab == "open") == cases.is_open(r.status)]
        state = s.get(cases.SyncState, "cases_since")
        return templates.TemplateResponse(request, "cases.html", {
            "client": {"id": c.id, "name": c.name}, "rows": shown, "tab": tab, "tabs": CASE_TABS, "counts": counts,
            "tags": ", ".join(cases.client_tags(c)), "custom_tags": c.case_tags, "synced": synced,
            "configured": outlook.configured(),
            "last_mail": state.value if state else None, "is_open": cases.is_open,
        })


@app.post("/clients/{client_id}/cases/tags")
def cases_tags(request: Request, client_id: int, tags: str = Form("")):
    require_login(request)
    with SessionLocal() as s:
        c = s.get(Client, client_id) or _404()
        c.case_tags = ", ".join(t.strip().strip("[]") for t in tags.split(",") if t.strip())[:200]
        # re-match this client's tags against unassigned cases
        for sc in s.scalars(select(SupportCase).where(SupportCase.client_id.is_(None))):
            sc.client_id = cases.match_client(sc.subject, [c])
        s.commit()
    return RedirectResponse(f"/clients/{client_id}/cases", status_code=303)


@app.post("/clients/{client_id}/cases/sync")
def cases_sync(request: Request, client_id: int):
    require_login(request)
    try:
        r = cases.sync_cases()
        msg = f"{r['emails']} new case emails" if r.get("ok") else r.get("error", "failed")
    except Exception as exc:
        msg = f"failed: {str(exc)[:200]}"
    from urllib.parse import quote
    return RedirectResponse(f"/clients/{client_id}/cases?synced=" + quote(msg), status_code=303)


@app.get("/clients/{client_id}/installer", response_class=HTMLResponse)
def installer_page(request: Request, client_id: int, saved: int = 0, error: str = ""):
    """Agent installer: pick a script, pick Windows/Linux, copy or email its OneDrive share link."""
    require_login(request)
    with SessionLocal() as s:
        c = s.get(Client, client_id) or _404()
        return templates.TemplateResponse(request, "installer.html", {
            "client": {"id": c.id, "name": c.name}, "links": c.installers or {}, "report_to": c.report_to,
            "kinds": INSTALLER_KINDS, "oses": INSTALLER_OS, "saved": saved, "error": error,
        })


@app.post("/clients/{client_id}/installer")
async def installer_save(request: Request, client_id: int):
    require_login(request)
    form = await request.form()
    links, bad = {}, []
    for k in INSTALLER_KINDS:
        for o in INSTALLER_OS:
            url = str(form.get(f"{k}:{o}") or "").strip()
            if not url:
                continue
            if _valid_share_link(url):
                links[f"{k}:{o}"] = url[:2000]
            else:
                bad.append(f"{INSTALLER_KINDS[k][0]} ({INSTALLER_OS[o]})")
    with SessionLocal() as s:
        c = s.get(Client, client_id) or _404()
        if bad:
            from urllib.parse import quote
            return RedirectResponse(f"/clients/{c.id}/installer?error=" + quote("Not a OneDrive/SharePoint https link: " + ", ".join(bad)),
                                    status_code=303)
        c.installers = links
        s.commit()
    return RedirectResponse(f"/clients/{client_id}/installer?saved=1", status_code=303)


def _404():
    raise HTTPException(status_code=404, detail="Client not found")


@app.post("/api/clients/{client_id}/refresh")
def refresh_client(request: Request, client_id: int):
    require_login(request)
    with SessionLocal() as s:
        c = s.get(Client, client_id) or _404()
        try:
            with _poll_lock, collector.poll_guard():  # don't race the scheduler or a CLI run
                summary = collector.collect_client(s, c)
                s.commit()
        except Exception as exc:
            s.rollback()
            return JSONResponse({"ok": False, "error": str(exc)[:500]}, status_code=502)
    return {"ok": True, "summary": summary}


@app.post("/api/sync")
def sync_all(request: Request):
    """'Sync now' on the Home page: run the 5-minute job immediately for every client."""
    require_login(request)
    with _poll_lock:
        results = collector.collect_all()
    return {"ok": not any(r.startswith("FAILED") for _, r in results),
            "results": [{"client": n, "result": r} for n, r in results]}


def _filename(client: Client) -> str:
    return f"{re.sub(r'[^A-Za-z0-9]+', '_', client.name).strip('_')}_VisionOne_Report_{date.today():%Y-%m-%d}.pdf"


@app.get("/clients/{client_id}/report.pdf")
def download_report(request: Request, client_id: int):
    require_login(request)
    with SessionLocal() as s:
        c = s.get(Client, client_id) or _404()
        try:
            pdf, _ = reports.build_report(s, c)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{_filename(c)}"'})


@app.post("/api/clients/{client_id}/report")
def draft_report(request: Request, client_id: int):
    """Build the PDF and create an Outlook draft with it attached. Nothing is sent."""
    require_login(request)
    with SessionLocal() as s:
        c = s.get(Client, client_id) or _404()
        try:
            pdf, summary = reports.build_report(s, c)
        except RuntimeError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=409)
        latest = reports.global_latest(s)
        old_patterns = reports.endpoints_with_old_patterns(s, c.id, latest) if latest else None
    try:
        link = outlook.create_draft(
            subject=f"{c.name} | Trend Vision One health report | {date.today():%d %b %Y}",
            html_body=reports.report_email_html(c, summary, old_patterns),
            to=[a.strip() for a in c.report_to.split(",")],
            attachment_name=_filename(c), attachment=pdf,
        )
    except outlook.OutlookNotConfigured as exc:
        return JSONResponse({"ok": False, "error": f"Outlook is not set up yet: {exc}",
                             "download": f"/clients/{c.id}/report.pdf"}, status_code=400)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:500],
                             "download": f"/clients/{c.id}/report.pdf"}, status_code=502)
    return {"ok": True, "web_link": link}


FILTERS = {
    "all": ("All endpoints", lambda e: True),
    "online": ("Online", lambda e: e.online),
    "offline": ("Offline", lambda e: not e.online),
    "stale": ("Not seen for 7+ days", lambda e: e.stale),
    "patterns_latest": ("Patterns up to date", lambda e: _cstate(e) == "latest"),
    "patterns_outdated": ("Patterns outdated", lambda e: _cstate(e) == "outdated"),
    "patterns_unknown": ("Pattern status unknown", lambda e: _cstate(e) == "unknown"),
    "agent_behind": ("Agents needing an update", lambda e: e.behind_latest),
    "sep_compliant": ("With compliant virus patterns", None),
    "sep_outdated": ("With outdated virus patterns", None),
    "sep_offline7": ("Offline for 7 days", None),
    **{k: (label, (lambda k: lambda e: k in collector.inventory_flags(e))(k))
       for k, label in collector.INVENTORY_FLAGS.items()},
}
PROT_NAMES = {"SEP": "Standard Endpoint Protection", "SWP": "Server & Workload Protection", "Sensor only": "Sensor only"}


def _cstate(e: Endpoint) -> str:
    return collector.component_state({"component_version": e.component_version, "update_status": e.update_status})


def _filtered(s, client_id: int | None, protection: str, flt: str, agent_status: str, version: str,
              component: str = ""):
    stmt = select(Endpoint, Client.name).join(Client).where(Client.enabled.is_(True))
    if client_id:
        stmt = stmt.where(Endpoint.client_id == client_id)
    if protection:
        stmt = stmt.where(Endpoint.protection == protection)
    if agent_status:
        stmt = stmt.where(Endpoint.agent_version_status == agent_status)
    if version:
        stmt = stmt.where(Endpoint.agent_version == version)
    pred = FILTERS.get(flt, FILTERS["all"])[1]
    if flt.startswith("sep_"):
        latest_sep = reports.global_latest(s).get("SEP", {})
        want = flt[4:]
        stmt = stmt.where(Endpoint.protection == "SEP")
        pred = lambda e: collector.virus_state(e, latest_sep) == want
    if component:
        latest = reports.global_latest(s)
        def behind(e):
            return collector.components_behind(e.components, latest.get(e.protection, {}))
        if flt == "component_current":
            pred = lambda e: component in (e.components or {}) and component not in behind(e)
        else:
            pred = lambda e: component in behind(e)
    rows = [(e, cname) for e, cname in s.execute(stmt.order_by(Client.name, Endpoint.name)).all() if pred(e)]
    return rows


def _list_title(client_name, protection, flt, agent_status, version, component=""):
    if component:
        parts = [f"{component}: " + ("on latest" if flt == "component_current" else "behind latest")]
    else:
        parts = [FILTERS.get(flt, FILTERS["all"])[0]]
    if protection:
        parts.append(PROT_NAMES.get(protection, protection))
    if agent_status:
        parts.append(f"agent status {agent_status}")
    if version:
        parts.append(f"agent version {version}")
    return " · ".join(parts), (client_name or "All clients")


@app.get("/endpoints", response_class=HTMLResponse)
def endpoint_list(request: Request, client: int | None = None, protection: str = "", filter: str = "all",
                  agent_status: str = "", version: str = "", component: str = ""):
    """Drill-down: every count on the dashboard links here with its filter."""
    require_login(request)
    with SessionLocal() as s:
        c = s.get(Client, client) if client else None
        rows = _filtered(s, client, protection, filter, agent_status, version, component)
        title, scope = _list_title(c.name if c else None, protection, filter, agent_status, version, component)
        return templates.TemplateResponse(request, "endpoints.html", {
            "rows": rows, "title": title, "scope": scope, "client": c, "multi": c is None,
            "csv_url": "/endpoints.csv?" + str(request.query_params),
            "cstate": _cstate,
        })


@app.get("/endpoints.csv")
def endpoint_list_csv(request: Request, client: int | None = None, protection: str = "", filter: str = "all",
                      agent_status: str = "", version: str = "", component: str = ""):
    require_login(request)
    import csv
    import io
    with SessionLocal() as s:
        rows = _filtered(s, client, protection, filter, agent_status, version, component)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Client", "Endpoint", "Protection", "Status", "Agent version", "Agent version status", "Patterns",
                "Sensor version", "OS", "IP", "Policy", "Last connected"])
    for e, cname in rows:
        w.writerow([cname, e.name, e.protection, "online" if e.online else "offline", e.agent_version,
                    e.agent_version_status, _cstate(e), e.sensor_version, e.os, e.ip, e.policy,
                    e.last_connected.isoformat() if e.last_connected else ""])
    fname = f"endpoints_{filter}_{date.today():%Y-%m-%d}.csv"
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@app.get("/healthz")
def healthz():
    return {"ok": True}
