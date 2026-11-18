import httpx
from fastapi.testclient import TestClient

from app import articles, kb
from app.config import settings
from app.db import KnowledgeCase, SessionLocal, ToolLink, TrendArticle, WorkItem
from app.main import app
from tests.test_app import fresh_db  # noqa: F401  (same clean database per test)

MAIL = """Subject: RE: TM-04118708 [ABC] System compatibility error in RHEL XDR Sensor

Hi Team,

Issue: XDR sensor shows "System compatibility error" on RHEL 8.10 servers.
Endpoint inventory lists the sensor as disconnected.

Root Cause:
Kernel 4.18.0-553 was not yet in the supported list.

Resolution:
1. Trend released a kernel support update.
2. Sensor connected after the update; no reboot needed.

Thanks & Regards,
Trend Support
From: older mail
Issue: old text that must not be used
"""


def login(http, user="admin", pw="pw"):
    assert http.post("/login", data={"username": user, "password": pw}).status_code == 200


def test_closure_email_is_split_into_issue_and_solution():
    got = kb.extract(MAIL)
    assert got["case_no"] == "TM-04118708"
    assert got["title"] == "[ABC] System compatibility error in RHEL XDR Sensor"
    assert got["issue"].startswith('XDR sensor shows "System compatibility error"') and "old text" not in got["issue"]
    assert got["root_cause"] == "Kernel 4.18.0-553 was not yet in the supported list."
    assert got["solution"].startswith("1. Trend released") and "Regards" not in got["solution"]
    assert got["product"] == "xdr"
    assert kb.extract("ntrtscan service stopped on Apex One agent")["product"] == "apex-one"


def test_two_teammates_and_work_board_history(monkeypatch):
    monkeypatch.setattr(settings, "dashboard_users_raw", "akshay:pw-a,ravi:pw-r")
    with TestClient(app) as http:
        assert http.post("/login", data={"username": "akshay", "password": "wrong"}).status_code == 401
        login(http, "Akshay", "pw-a")  # user names are not case-sensitive
        r = http.post("/work", data={"title": "Apex agents not updating patterns", "client": "Acme Corp",
                                     "product": "apex-one", "priority": "P2", "note": "Checked 3 agents, update source unreachable"})
        wid = int(r.url.path.rsplit("/", 1)[1])
        assert "Apex agents not updating patterns" in http.get("/").text  # on Home
        http.post("/logout")
        login(http, "ravi", "pw-r")
        http.post(f"/work/{wid}", data={"note": "Raised with Trend", "status": "Waiting on Trend", "owner": "ravi",
                                        "priority": "P2", "trend_case": "TM-04000001"})
        page = http.get(f"/work/{wid}").text
        assert "Status: Open → Waiting on Trend" in page and "Owner: akshay → ravi" in page and "Raised with Trend" in page
        board = http.get("/work").text
        assert "Acme Corp" in board and "ravi: Raised with Trend" in board
        assert "Apex agents" in http.get("/work?owner=me").text
        r = http.post(f"/work/{wid}", data={"note": "Fixed by pointing agents to the cloud update source", "status": "Resolved",
                                            "owner": "ravi", "priority": "P2", "trend_case": "TM-04000001"})
        assert "resolved=1" in str(r.url)
        assert "Apex agents" not in http.get("/work").text  # open only by default
        form = http.get(f"/library/new?from_work={wid}").text
        assert "TM-04000001" in form and "Fixed by pointing agents" in form


def test_case_library_from_pasted_email():
    with TestClient(app) as http:
        login(http)
        form = http.post("/library/read-email", data={"raw_email": MAIL}).text
        assert "Kernel 4.18.0-553" in form and "TM-04118708" in form
        r = http.post("/library", data={"title": "RHEL sensor compatibility error", "case_no": "TM-04118708",
                                        "client": "Acme Corp", "product": "xdr", "issue": "Sensor shows compatibility error",
                                        "solution": "Install the kernel support update", "closed_on": "2026-11-17"})
        page = r.text
        assert "Install the kernel support update" in page and 'class="card hl solution big"' in page
        lib = http.get("/library?q=kernel").text
        assert "RHEL sensor compatibility error" in lib
        assert "RHEL sensor" not in http.get("/library?q=nothing-like-this").text
    with SessionLocal() as s:
        assert s.query(KnowledgeCase).one().added_by == "admin"


def test_scripts_listed_by_product_and_downloadable():
    with TestClient(app) as http:
        login(http)
        page = http.get("/scripts").text
        for heading in ("Deep Security", "XDR / Vision One", "Apex One"):
            assert heading in page
        assert "ds-agent-health-linux.sh" in page and "apex-agent-health.ps1" in page and "xdr-sensor-health-windows.ps1" in page
        r = http.get("/scripts/apex-one/apex-agent-health.ps1/raw?download=1")
        assert r.status_code == 200 and "attachment" in r.headers["content-disposition"] and "ntrtscan" in r.text
        assert http.get("/scripts/apex-one/..%2F..%2Fapp%2Fconfig.py/raw").status_code == 404
        assert http.get("/scripts/nope/x.sh").status_code == 404


def test_scripts_are_read_only():
    """Health checks must not change the machine: no service restarts, uninstalls or setting changes."""
    import re
    from app.console import list_scripts, ROOT
    for s in list_scripts():
        code = (ROOT / s["product"] / s["name"]).read_text()
        body = "\n".join(l for l in code.splitlines() if not l.lstrip().startswith(("#", '"', "echo", "\"-")))
        assert not re.search(r"\b(Restart-Service|Stop-Service|Set-Service|Start-Service|Remove-Item|Set-ItemProperty|"
                             r"New-ItemProperty|Restart-Computer|Stop-Process|msiexec|sc(\.exe)? (stop|config|delete)|"
                             r"systemctl (restart|stop|start|disable)|rm -rf?|shutdown [-/]|^\s*reboot)\b",
                             body, re.I | re.M), s["name"]


LIST_HTML = """<html><body><h2>Product advisories</h2>
<a href="/solution/KA-0025062">SECURITY ALERT: Apache HTTP Server mod_http2 RCE</a>
<a href="https://success.trendmicro.com/solution/KA-0024996?language=en_US">Vision One Endpoint Security November Network Upgrade Notice</a>
<a href="/solution/KA-0024996"></a><a href="/other">x</a></body></html>"""
ARTICLE_HTML = """<html><head><title>x</title></head><body><nav>menu</nav><h1>SECURITY ALERT: Apache HTTP Server mod_http2 RCE</h1>
<p>Updated: 14 Nov 2026</p><h2>Summary</h2><p>A remote code execution issue affects Apache HTTP Server used by some products.</p>
<h2>Solution</h2><ol><li>Apply the latest Apache patch.</li><li>Restart the web console service.</li></ol>
<h3>Related articles</h3><p>other</p></body></html>"""


def test_trend_articles_picked_up_automatically():
    calls = []

    def handler(req: httpx.Request):
        calls.append(str(req.url))
        if "/solution/KA-0025062" in req.url.path:
            return httpx.Response(200, html=ARTICLE_HTML)
        if "/solution/" in req.url.path:
            return httpx.Response(200, html="<h1>Notice</h1><p>short</p>")
        return httpx.Response(200, html=LIST_HTML)
    t = httpx.MockTransport(handler)
    assert articles.sync(t) == 2
    assert articles.sync(t) == 0  # already known, and article pages are read only once
    assert sum("KA-0025062" in c for c in calls) == 1
    with SessionLocal() as s:
        a = s.get(TrendArticle, "KA-0025062")
        assert a.kind == "Security alert" and a.published == "14 Nov 2026"
        assert "remote code execution" in a.summary and a.solution.startswith("Apply the latest Apache patch")
        assert "Related" not in a.solution
        assert s.get(TrendArticle, "KA-0024996").kind == "Notice"
    with TestClient(app) as http:
        login(http)
        page = http.get("/articles").text
        assert "Apply the latest Apache patch" in page and "KA-0024996" in page
        assert "Apache HTTP Server" in http.get("/").text


def test_tools_links():
    with TestClient(app) as http:
        login(http)
        http.post("/tools", data={"name": "Case Diagnostic Tool", "url": "https://contoso-my.sharepoint.com/x",
                                  "product": "apex-one", "description": "Collect logs"})
        assert http.post("/tools", data={"name": "bad", "url": "javascript:alert(1)"}).status_code == 400
        page = http.get("/tools").text
        assert "contoso-my.sharepoint.com/x" in page and "OneDrive" in page
        with SessionLocal() as s:
            tid = s.query(ToolLink).one().id
        http.post(f"/tools/{tid}/delete")
        assert "contoso-my.sharepoint.com/x" not in http.get("/tools").text
