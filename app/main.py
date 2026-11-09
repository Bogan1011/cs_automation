import hmac
from datetime import date, datetime, timezone
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

from . import collector, outlook, reports
from .config import settings
from .db import Client, Endpoint, SessionLocal, Snapshot, init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
BASE = Path(__file__).parent
templates = Jinja2Templates(directory=BASE / "templates")
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
    require_login(request)
    return templates.TemplateResponse(request, "index.html", {"poll_interval": settings.poll_interval})


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
        return templates.TemplateResponse(request, "client.html", {
            "client": card, "endpoints": endpoints,
            "patterns": collector.pattern_status(card["summary"] or {}, latest),
            "behind": {e.id: collector.components_behind(e.components, latest) for e in endpoints},
            "history": [{"t": h.taken_at.isoformat(), "total": h.summary.get("total", 0),
                         "online": h.summary.get("online", 0)} for h in reversed(history)],
            "graph_configured": settings.graph_configured, "report_to": c.report_to,
        })


def _404():
    raise HTTPException(status_code=404, detail="Client not found")


@app.post("/api/clients/{client_id}/refresh")
def refresh_client(request: Request, client_id: int):
    require_login(request)
    with SessionLocal() as s:
        c = s.get(Client, client_id) or _404()
        try:
            with _poll_lock:  # don't race the 5-minute scheduler
                summary = collector.collect_client(s, c)
                s.commit()
        except Exception as exc:
            s.rollback()
            return JSONResponse({"ok": False, "error": str(exc)[:500]}, status_code=502)
    return {"ok": True, "summary": summary}


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


@app.get("/healthz")
def healthz():
    return {"ok": True}
