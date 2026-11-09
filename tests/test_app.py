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
from tests.fixtures import ENDPOINTS


def v1_transport():
    def handler(req: httpx.Request):
        assert req.headers["Authorization"] == "Bearer secret-key"
        if req.url.path == "/v3.0/endpointSecurity/endpoints":
            if "skip" in req.url.params:
                return httpx.Response(200, json={"items": ENDPOINTS[3:]})
            return httpx.Response(200, json={"items": ENDPOINTS[:3],  # page 1
                                             "nextLink": str(req.url.copy_with(params={"skip": "x"}))})
        assert "workbench" not in req.url.path and "search" not in req.url.path  # no telemetry/detections
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
    assert s["total"] == 7 and s["unmanaged"] == 4  # matches console "All managed endpoints"
    assert (s["sep"], s["swp"], s["sensor_only"]) == (3, 3, 1)  # APEX ONE -> SEP, "Server and Workload" -> SWP
    assert s["offline"] == 1 and s["stale"] == 1
    assert (s["up_to_date"], s["outdated"], s["components_unknown"]) == (5, 1, 1)  # Trend's componentVersion verdict
    assert s["component_status"]["SEP"] == {"latest": 2, "outdated": 1}
    assert s["latest_versions"] == {"SEP": "14.0.13100.1", "SWP": "20.0.1.7000"}
    assert s["behind_latest"] == 1  # BL-LAP-002: agentVersionStatus updateRecommended
    assert s["by_agent_version"]["SEP"] == {"14.0.13100.1": 2, "14.0.12900.1": 1}
    with SessionLocal() as db:
        assert db.query(Endpoint).count() == 7
        assert db.get(Client, cid).last_poll_ok is True


def test_repoll_replaces_endpoints_and_keeps_history():
    cid = make_client()
    collect(cid)
    collect(cid)
    with SessionLocal() as db:
        assert db.query(Endpoint).count() == 7
        assert db.query(Snapshot).count() == 2


def test_version_key_orders_numerically():
    assert collector.version_key("14.0.13100.1") > collector.version_key("14.0.9999.1")


def test_auth_error_is_clear():
    t = httpx.MockTransport(lambda r: httpx.Response(401))
    with VisionOneClient("bad", "sg", transport=t) as v1, pytest.raises(VisionOneError, match="API key"):
        v1.list_endpoints()


def test_report_pdf():
    cid = make_client()
    collect(cid)
    with SessionLocal() as s:
        pdf, summary = build_report(s, s.get(Client, cid))
    assert pdf.startswith(b"%PDF") and summary["total"] == 7


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
        assert data["clients"][0]["summary"]["total"] == 7
        page = http.get(f"/clients/{cid}")
        assert page.status_code == 200 and "BL-SRV-DB01" in page.text and "Agent versions" in page.text and "Virus Pattern" in page.text
        assert http.get(f"/clients/{cid}/report.pdf").content.startswith(b"%PDF")
        r = http.post(f"/api/clients/{cid}/report")
        assert r.status_code == 400 and r.json()["download"]  # Outlook not configured -> PDF fallback


def test_naive_timestamps_are_treated_as_utc():
    from tests.fixtures import endpoint
    item = endpoint("n1", "NAIVE", "Standard Endpoint Protection")
    item["eppAgent"]["lastConnectedDateTime"] = "2026-11-01T10:00:00"
    row = collector.normalize_endpoint(item)
    assert row["last_connected"].tzinfo is not None and row["stale"] is True


def test_patterns_latest_across_clients_and_behind_counts():
    from app import reports
    a = make_client("Bandhan Life")
    b = make_client("Clix Capital", "sg")
    collect(a)
    collect(b)
    with SessionLocal() as s:
        latest = reports.global_latest(s)
        assert latest == {"Virus Pattern": "19.123.00", "Digital Signature Pattern": "1.987.00"}
        assert reports.endpoints_with_old_patterns(s, a, latest) == 1  # BL-LAP-APEX on 19.121.00
        rows = collector.pattern_status(reports.latest_summary(s, a).summary, latest)
        virus = next(r for r in rows if r["name"] == "Virus Pattern")
        assert (virus["current"], virus["behind"]) == (5, 1)


def test_extract_components_flat_keys():
    comps = collector.extract_components({"eppAgent": {"version": "14.0.1.1", "spywarePatternVersion": "2.871.00"},
                                          "edrSensor": {"version": "1.2.3.4"}})
    assert comps == {"Spyware Pattern": "2.871.00"}


def test_drilldown_lists_and_csv():
    cid = make_client()
    collect(cid)
    with TestClient(app) as http:
        http.post("/login", data={"username": "admin", "password": "pw"})
        page = http.get(f"/endpoints?client={cid}&protection=SEP&filter=patterns_outdated")
        assert page.status_code == 200 and "BL-LAP-002" in page.text and "BL-LAP-001" not in page.text
        assert "<b>1</b> endpoints" in page.text
        allp = http.get("/endpoints?filter=agent_behind")
        assert "BL-LAP-002" in allp.text and "Bandhan Life" in allp.text
        csv = http.get(f"/endpoints.csv?client={cid}&protection=SWP").text
        assert csv.count("\n") == 1 + 3 and "BL-SRV-DB01" in csv


def test_swp_client_paginates_and_falls_back_to_legacy_header(monkeypatch):
    from app import swp
    monkeypatch.setattr(swp, "PAGE", 2)
    seen = []

    def handler(req):
        if "Authorization" in req.headers:
            return httpx.Response(500)  # what Bandhan's tenant returned for the ApiKey header
        assert req.headers["api-secret-key"] == "k" and req.headers["api-version"] == "v1"
        body = json.loads(req.content)
        last = body["searchCriteria"][0]["idValue"]
        seen.append(last)
        comps = [{"ID": i, "hostName": f"srv{i}.corp.local",
                  "securityUpdates": {"antiMalware": [{"name": "Virus Pattern", "version": "21.431.00"}]}}
                 for i in range(last + 1, min(last + 3, 4))]
        return httpx.Response(200, json={"computers": comps})

    with swp.SWPClient("https://workload.example", "k", transport=httpx.MockTransport(handler)) as c:
        comps = c.list_computers()
    assert [x["ID"] for x in comps] == [1, 2, 3] and seen == [0, 2]
    assert collector.extract_components(comps[0]["securityUpdates"]) == {"Virus Pattern": "21.431.00"}
    assert swp.computer_key(comps[0]) == ["srv1.corp.local", "srv1"]


def test_apex_jwt_is_valid_hs256():
    import base64, hashlib, hmac as h
    from app.apexone import make_jwt
    tok = make_jwt("APPID", "secret", "get", "/WebApp/API/AgentResource/ProductAgents?host_name=X")
    head, payload, sig = tok.split(".")
    pad = lambda s: s + "=" * (-len(s) % 4)
    claims = json.loads(base64.urlsafe_b64decode(pad(payload)))
    assert claims["appid"] == "APPID" and claims["version"] == "V1"
    expected = base64.b64encode(hashlib.sha256(
        b"GET|/webapp/api/agentresource/productagents?host_name=x||").digest()).decode()
    assert claims["checksum"] == expected
    good = base64.urlsafe_b64encode(h.new(b"secret", f"{head}.{payload}".encode(), hashlib.sha256).digest()).rstrip(b"=").decode()
    assert sig == good


def test_exact_patterns_from_console_api_are_merged(monkeypatch):
    from app import reports
    cid = make_client()
    with SessionLocal() as s:
        c = s.get(Client, cid)
        c.swp_url = "https://x"
        s.commit()
    monkeypatch.setattr(collector, "fetch_swp_components", lambda client: {
        "bl-srv-db01": {"Smart Scan Agent Pattern": "21.431.00"},
        "bl-srv-web01": {"Smart Scan Agent Pattern": "21.421.00"}})
    collect(cid)
    with SessionLocal() as s:
        latest = reports.global_latest(s)
        assert latest["Smart Scan Agent Pattern"] == "21.431.00"
        rows = collector.pattern_status(reports.latest_summary(s, cid).summary, latest)
        ssa = next(r for r in rows if r["name"] == "Smart Scan Agent Pattern")
        assert (ssa["current"], ssa["behind"]) == (1, 1)
