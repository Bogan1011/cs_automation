import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import collector, crypto, outlook
from app.config import settings
from app.db import Base, Client, Endpoint, SessionLocal, Snapshot, engine
from app.main import app
from app.reports import build_report
from app.visionone import VisionOneClient, VisionOneError
from tests.fixtures import ALERTS, ENDPOINTS


def v1_transport(fail_alerts=False):
    def handler(req: httpx.Request):
        assert req.headers["Authorization"] == "Bearer secret-key"
        if req.url.path == "/v3.0/endpointSecurity/endpoints":
            if "skip" in req.url.params:
                return httpx.Response(200, json={"items": ENDPOINTS[3:]})
            return httpx.Response(200, json={"items": ENDPOINTS[:3],
                                             "nextLink": str(req.url.copy_with(params={"skip": "x"}))})
        if req.url.path == "/v3.0/workbench/alerts":
            if fail_alerts:
                return httpx.Response(403, json={"error": "no permission"})
            return httpx.Response(200, json={"items": ALERTS})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


def make_client(name="Bandhan Life", region="in"):
    with SessionLocal() as s:
        c = Client(name=name, region=region, api_key_enc=crypto.encrypt("secret-key"), report_to="soc@example.com")
        s.add(c)
        s.commit()
        return c.id


def collect(cid, **kw):
    with SessionLocal() as s:
        c = s.get(Client, cid)
        with VisionOneClient("secret-key", c.region, transport=v1_transport(**kw)) as v1:
            summary = collector.collect_client(s, c, v1)
        s.commit()
    return summary


def test_crypto_roundtrip():
    tok = crypto.encrypt("abc")
    assert tok != "abc" and crypto.decrypt(tok) == "abc"


def test_unknown_region_rejected():
    with pytest.raises(ValueError):
        VisionOneClient("k", "mars")


def test_collect_paginates_and_summarizes():
    cid = make_client()
    s = collect(cid)
    assert s["total"] == 5
    assert (s["sep"], s["swp"], s["sensor_only"]) == (2, 2, 1)
    assert s["offline"] == 1 and s["stale"] == 1 and s["outdated"] == 1
    assert s["alerts_7d"]["total"] == 3 and s["alerts_7d"]["open"] == 2
    with SessionLocal() as db:
        assert db.query(Endpoint).count() == 5
        assert db.get(Client, cid).last_poll_ok is True


def test_repoll_replaces_endpoints_and_keeps_history():
    cid = make_client()
    collect(cid)
    collect(cid)
    with SessionLocal() as db:
        assert db.query(Endpoint).count() == 5
        assert db.query(Snapshot).count() == 2


def test_missing_alert_permission_still_collects_endpoints():
    cid = make_client()
    s = collect(cid, fail_alerts=True)
    assert s["total"] == 5 and "alerts_7d" not in s


def test_auth_error_is_clear():
    t = httpx.MockTransport(lambda r: httpx.Response(401))
    with VisionOneClient("bad", "sg", transport=t) as v1, pytest.raises(VisionOneError, match="API key"):
        v1.list_endpoints()


def test_report_pdf():
    cid = make_client()
    collect(cid)
    with SessionLocal() as s:
        pdf, summary = build_report(s, s.get(Client, cid))
    assert pdf.startswith(b"%PDF") and summary["total"] == 5


def test_outlook_draft_payload(monkeypatch):
    for k, v in dict(graph_tenant_id="t", graph_client_id="c", graph_client_secret="s", graph_mailbox="me@x.com").items():
        monkeypatch.setattr(settings, k, v)
    seen = {}

    def handler(req):
        if "oauth2" in req.url.path:
            return httpx.Response(200, json={"access_token": "tok"})
        seen["path"] = req.url.path
        seen["body"] = json.loads(req.content)
        assert req.headers["Authorization"] == "Bearer tok"
        return httpx.Response(201, json={"webLink": "https://outlook.office365.com/owa/?ItemID=1"})

    link = outlook.create_draft("Subj", "<p>x</p>", ["a@b.com"], "r.pdf", b"%PDF-1", transport=httpx.MockTransport(handler))
    assert link.startswith("https://outlook")
    assert seen["path"] == "/v1.0/users/me@x.com/messages"  # creates a draft; never calls /send
    assert seen["body"]["attachments"][0]["name"] == "r.pdf"


def test_web_requires_login_and_renders():
    cid = make_client()
    collect(cid)
    with TestClient(app) as http:
        assert http.get("/api/overview").status_code == 401
        assert http.get("/", follow_redirects=False).status_code == 303
        assert http.post("/login", data={"username": "admin", "password": "nope"}).status_code == 401
        assert http.post("/login", data={"username": "admin", "password": "pw"}).status_code == 200
        data = http.get("/api/overview").json()
        assert data["clients"][0]["summary"]["total"] == 5
        page = http.get(f"/clients/{cid}")
        assert page.status_code == 200 and "BL-SRV-DB01" in page.text
        assert http.get(f"/clients/{cid}/report.pdf").content.startswith(b"%PDF")
        r = http.post(f"/api/clients/{cid}/report")
        assert r.status_code == 400 and r.json()["download"]  # Outlook not configured -> PDF fallback
