import httpx
from fastapi.testclient import TestClient

from app import articles, kb
from app.config import settings
from app.db import KnowledgeCase, SessionLocal, ToolLink, TrendArticle, WorkItem
from app.main import app
from tests.test_app import fresh_db  # noqa: F401  (same clean database per test)

MAIL = """Subject: RE: TM-04000005 [ABC] System compatibility error in RHEL XDR Sensor

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
    assert got["case_no"] == "TM-04000005"
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
        assert "Kernel 4.18.0-553" in form and "TM-04000005" in form
        r = http.post("/library", data={"title": "RHEL sensor compatibility error", "case_no": "TM-04000005",
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
        assert set(a.sources.split()) == {"vision-one", "endpoint-workload", "apex-saas"} and a.pinned
    with TestClient(app) as http:
        login(http)
        page = http.get("/articles").text
        assert "Apply the latest Apache patch" in page and "KA-0024996" in page and "Highlighted by Trend now" in page
        assert "Apex One SaaS" in http.get("/articles?src=apex-saas").text
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


THREAD = """Hi Akshay,

As discussed on the call, the issue has been resolved after applying critical patch 14.0.13140 to the Apex One server. Agents are connected and patterns updated.

Please let us know if we can close the case.

Thanks & Regards,
Rahul | Technical Support
Phone: +91 00000

From: Akshay
Sent: Monday
Subject: RE: TM-04000004 ntrtscan not starting

Hi team, logs attached. The cause seems to be due to the TMUMH driver left from the old version.

From: Akshay
Sent: Friday
Subject: TM-04000004 ntrtscan not starting

Hello Team,

Real-time scan service (ntrtscan) is not starting on 12 Windows 2016 servers after upgrading the Apex One agent. Event log shows error 7024.

Regards,
Akshay
"""


def test_thread_without_headings_is_summarised():
    got = kb.summarize(THREAD, "RE: TM-04000004 ntrtscan not starting")
    assert got["case_no"] == "TM-04000004" and got["product"] == "apex-one"
    assert got["issue"].startswith("ntrtscan not starting\n\nReal-time scan service (ntrtscan) is not starting")
    assert "As discussed on the call, the issue has been resolved" in got["solution"] and "Phone" not in got["solution"]
    assert got["root_cause"].startswith("logs attached. The cause seems to be due to the TMUMH driver")


def test_upload_eml_fills_the_form():
    from email.message import EmailMessage
    m = EmailMessage()
    m["Subject"] = "RE: TM-04000004 ntrtscan not starting"
    m["From"] = "support@example.com"
    m["Date"] = "Tue, 17 Nov 2026 10:00:00 +0530"
    m.set_content("<p>" + THREAD.replace("\n\n", "</p><p>").replace("\n", "<br>") + "</p>", subtype="html")
    with TestClient(app) as http:
        login(http)
        page = http.post("/library/read-email", files={"mail": ("case.eml", m.as_bytes(), "message/rfc822")}).text
        assert "Summarised from case.eml" in page
        assert "TM-04000004" in page and "critical patch 14.0.13140" in page and "ntrtscan) is not starting" in page
        assert 'value="2026-11-17"' in page  # closed on = mail date
        bad = http.post("/library/read-email", files={"mail": ("case.pdf", b"%PDF-1.4", "application/pdf")}).text
        assert "Upload an Outlook .msg or .eml file" in bad


def test_outlook_msg_streams_are_read(monkeypatch):
    """.msg is an OLE file; check the MAPI streams we read (subject 0037, body 1000, HTML 1013, date 0E06)."""
    import struct
    from datetime import datetime, timezone
    from app import mailfile
    ft = int((datetime(2026, 11, 17, 4, 30, tzinfo=timezone.utc) - datetime(1601, 1, 1, tzinfo=timezone.utc)).total_seconds() * 10**7)
    props = b"\0" * 32 + struct.pack("<HHIQ", 0x0040, 0x0E06, 0, ft)
    streams = {"__substg1.0_0037001F": "RE: TM-04000004 ntrtscan not starting".encode("utf-16-le"),
               "__substg1.0_1000001F": b"",
               "__substg1.0_10130102": ("<html><body><p>" + THREAD.replace("\n\n", "</p><p>") + "</p></body></html>").encode(),
               "__properties_version1.0": props}

    class FakeOle:
        def __init__(self, *_): pass
        def exists(self, n): return n in streams
        def openstream(self, n): return __import__("io").BytesIO(streams[n])
        def close(self): pass

    import olefile
    monkeypatch.setattr(olefile, "isOleFile", lambda *_: True)
    monkeypatch.setattr(olefile, "OleFileIO", FakeOle)
    out = mailfile.read_upload("case.msg", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 64)
    assert out["subject"] == "RE: TM-04000004 ntrtscan not starting"
    assert "critical patch 14.0.13140" in out["body"] and out["date"].date().isoformat() == "2026-11-17"


TREND_THREAD = """CAUTION: This email originated from outside of the organisation. Do not click links or open attachments unless you recognize the sender and know the content is safe.

Hello Team,

Thank you for your patience, and apologies for the delay. Below is the guidance on the server upgrade and the agent upgrade.

1. Server upgrade (1.0 to 2.0)

The server must be upgraded before any agents, because 2.0 agents cannot communicate with a 1.0 server.

*\tTake a full backup of the database before the upgrade.

2. Agent upgrade

A direct upgrade from agent 1.0 to 2.0 is not supported. The only supported method is to remove the existing agent and install the new version.

*\tGuide: https://docs.example.com/upgrade <https://docs.example.com/upgrade>

Thank you and stay safe.

Regards,
Support Engineer
+00 000 0000

------------------- Original Message -------------------
From: Engineer <eng@example.com>;
Subject: RE: TrendAI™ Technical Support Case TM-04000002 P2 : [ABC] Upgrade guidance CRM:110300000002

Hello team,

Please update on the case.

Thanks & Regards,
Engineer

From: Trend Micro TS Cases <cases@example.com>
Sent: Tuesday, November 3, 2026 4:51 PM
Subject: TrendAI™ Technical Support Case TM-04000002 P2 : [ABC] Upgrade guidance CRM:110300000002

** This is a system generated email from our case tracking system***

Please do not change the email subject when replying to this message.

Dear Customer,

Thank you for contacting TrendAI™ Technical Support.

Case: TM-04000002 [ABC] Upgrade guidance

Product: Endpoint Encryption

We are reviewing your submission and will contact you soon.

The information contained in this electronic message and any attachments are intended for the exclusive use of the addressee(s).
"""


def test_trend_case_thread_title_client_issue_solution():
    subject = "RE: TrendAI™ Technical Support Case TM-04000002 P2 : [ABC] Upgrade guidance\xa0CRM:110300000002"
    got = kb.summarize(TREND_THREAD, subject)
    assert got["title"] == "[ABC] Upgrade guidance" and got["client_tag"] == "ABC" and got["case_no"] == "TM-04000002"
    assert got["product"] == "endpoint-encryption"
    for junk in ("system generated", "do not change the email subject", "CAUTION", "Please update on the case", "Regards"):
        assert junk.lower() not in (got["issue"] + got["solution"]).lower(), junk
    assert got["issue"].startswith("Upgrade guidance (Endpoint Encryption)") and "Below is the guidance" in got["issue"]
    assert got["solution"].startswith("Steps from Trend support:\n1. Server upgrade")
    assert "The server must be upgraded before any agents" in got["solution"]
    assert "A direct upgrade from agent 1.0 to 2.0 is not supported." in got["solution"]
    assert "• Take a full backup" in got["solution"] and "<https://" not in got["solution"]
    with TestClient(app) as http:
        login(http)
        for _ in range(2):
            page = http.post("/library/read-email", data={"raw_email": TREND_THREAD, "subject": subject}).text
            assert 'name="client" list="clients" value="ABC"' in page
            http.post("/library", data={"title": got["title"], "client": "ABC", "product": got["product"],
                                        "issue": got["issue"], "solution": got["solution"]})
        lib = http.get("/library").text
        assert ">ABC (2)</option>" in lib


CHAIN = """Classification: Internal

Hello Team,

We have reinstalled the ds agent on the server and the ds agent is properly working now.

Thanks & Regards,
Engineer One
Security Engineer | Customer Success

From: Engineer One
Sent: Wednesday, August 5, 2026 10:33 AM
To: Client Infosec <infosec@client.example>
Subject: RE: TrendAI™ Technical Support Case TM-04000003 P1 : [XYZ] AMSP service not starting CRM:072900000003

Hello Team,

This is a follow up mail, please take the downtime for the server and we will proceed with repairing the ds agent in safe mode.

Thanks & Regards,
Engineer One

From: Trend Micro TS Cases <TrendMicroTSCases@service.trendmicro.com>
Sent: Tuesday, August 4, 2026 6:43 PM
Subject: RE: TrendAI™ Technical Support Case TM-04000003 P1 : [XYZ] AMSP service not starting CRM:072900000003

Hello Team,

A pleasant day to you.

I am sending this email to check and follow up on this case.

Support Person
Sr. Technical Support Engineer, AMEA

From: Trend Micro TS Cases <TrendMicroTSCases@service.trendmicro.com>
Sent: Friday, July 31, 2026 10:40 AM
Subject: RE: TrendAI™ Technical Support Case TM-04000003 P1 : [XYZ] AMSP service not starting CRM:072900000003

Hello,

1.\tBoot the machine into Safe Mode.
2.\tSet the following services to start manually:
*\tTrend Micro Deep Security Agent
3.\tRun the following command to reset the agent:
\tdsa_control.exe -r

Support Person
Sr. Technical Support Engineer, AMEA
One Example Tower, Mumbai

From: Trend Micro TS Cases <TrendMicroTSCases@service.trendmicro.com>
Sent: Thursday, July 30, 2026 10:13 AM
Subject: RE: TrendAI™ Technical Support Case TM-04000003 P1 : [XYZ] AMSP service not starting CRM:072900000003

This is Support Person from TrendAI Support Team and I will handle this case with you. As per the shared screenshot the service is not starting because of the required file is missing on the path.

Support Person
Sr. Technical Support Engineer, AMEA

From: Client Infosec <infosec@client.example>
Sent: Wednesday, July 29, 2026 5:00 PM
Subject: RE: TrendAI™ Technical Support Case TM-04000003 P1 : [XYZ] AMSP service not starting CRM:072900000003

Hello,

We attempted to repair the ds agent; however, the issue persists and the AMSP service still fails to start after a reboot.

Regards,
Client
"""


def test_solution_comes_from_trend_and_our_team_across_the_chain():
    subject = "RE: TrendAI™ Technical Support Case TM-04000003 P1 : [XYZ] AMSP service not starting\xa0CRM:072900000003"
    got = kb.summarize(CHAIN, subject, "Engineer One")
    assert got["title"] == "[XYZ] AMSP service not starting" and got["product"] == "deep-security"
    sol = got["solution"]
    assert sol.startswith("Outcome (Engineer One):\nWe have reinstalled the ds agent")
    assert "Steps from Trend support (Trend Micro TS Cases · July 31, 2026):\n1. Boot the machine into Safe Mode." in sol
    assert "• Trend Micro Deep Security Agent" in sol and "dsa_control.exe -r" in sol
    for junk in ("Sr. Technical Support Engineer", "One Example Tower", "pleasant day", "follow up on this case",
                 "take the downtime", "I will handle this case", "Classification"):
        assert junk not in sol, junk
    assert got["issue"] == ("AMSP service not starting\n\nWe attempted to repair the ds agent; however, the issue persists "
                            "and the AMSP service still fails to start after a reboot.")
    assert got["root_cause"].startswith("As per the shared screenshot the service is not starting because of")


def test_full_mail_trail_keeps_every_message():
    msgs = kb.trail(CHAIN)
    assert len(msgs) >= 3
    assert any(m["kind"] == "trend" for m in msgs)
    assert all(m["body"] for m in msgs)
    assert not any(l.lower().startswith(("from:", "sent:", "subject:")) for m in msgs for l in m["body"].splitlines()[:1])
    with TestClient(app) as http:
        login(http)
        r = http.post("/library", data={"title": "[ABC] Mail trail check", "product": "xdr", "issue": "x",
                                        "solution": "y", "raw_email": CHAIN, "closed_on": "2026-11-17"})
        cid = r.url.path.rsplit("/", 1)[-1]
        assert 'data-mail="/library/%s/mail"' % cid in r.text
        assert 'data-mail="/library/%s/mail"' % cid in http.get("/library").text
        pop = http.get(f"/library/{cid}/mail").text
        assert "Full mail trail" in pop and "Trend support" in pop and pop.count('class="mail m-') == len(msgs)
        assert http.get("/library/999/mail").status_code == 404


def test_troubleshooting_guides_list_search_and_render():
    from app import guides
    every = guides.all_guides()
    assert {g["product"] for g in every} == {"deep-security", "apex-one", "xdr"}
    for g in every:  # every guide has a header, renders, and only links scripts that exist
        assert g["title"] and g["summary"] and g["category"] in guides.CATEGORY_ORDER
        body, toc = guides.render(g["body"])
        assert toc and "<script" not in body
        names = {s["name"] for s in __import__("app.console", fromlist=["list_scripts"]).list_scripts(g["product"])}
        assert set(g["scripts"]) <= names, g["slug"]
    html, _ = guides.render("Use `<b>x</b>` and **bold** <img src=x onerror=alert(1)>\n\n```\nrm -rf <dir>\n```")
    assert "&lt;img" in html and "<img" not in html and "&lt;dir&gt;" in html and "<b>bold</b>" in html
    with TestClient(app) as http:
        login(http)
        page = http.get("/troubleshooting?q=force+uninstall").text
        assert "Force uninstall the Deep Security Agent on Windows" in page and "Collect Apex One" not in page
        assert "Apex One" in http.get("/troubleshooting?product=apex-one").text
        g = http.get("/troubleshooting/deep-security/force-uninstall-windows").text
        assert "dsa_control.cmd -r" in g and "On this page" in g and "ds-agent-health-windows.ps1" in g
        assert http.get("/troubleshooting/deep-security/..%2Fsecret").status_code == 404
        assert http.get("/troubleshooting/nope/x").status_code == 404


def test_article_parser_skips_page_metadata():
    page = """<html><head><title>CUSTOMER ADVISORY: Kernel flaw | Trend</title></head><body><h1>CUSTOMER ADVISORY: Kernel flaw</h1>
    <p>Under Investigation</p><p>Keywords: CUSTOMER ADVISORY: Kernel flaw</p><p>Product / Version includes: Vision One All</p>
    <p>Last updated: 2026/11/10</p><h2>Summary</h2><p>A use-after-free in the IPv4 TCP stack lets a remote attacker crash the host.</p>
    <h2>Solution</h2><p>Apply the vendor kernel update and restart the agent.</p><p>ID: KA-0099999</p><p>Category: Advisory</p></body></html>"""
    d = articles.read_article(page)
    assert d["summary"].startswith("A use-after-free") and "Keywords" not in d["summary"]
    assert "kernel update" in d["solution"] and "ID:" not in d["solution"]
    assert articles.is_junk("ID: KA-0025062\nCategory:") and articles.is_junk("Under Investigation Keywords: x")
    assert not articles.is_junk("Apply the vendor kernel update and restart the agent.")


def test_full_article_list_read_from_page_data():
    """Trend's product pages carry the whole (paged-in-the-browser) list as data, not as links."""
    items = ",".join('{"articlenumber":"KA-00%05d","title":"Agent issue number %d on Linux","knowledgearticleviews":%d,'
                     '"modifiedon":"2026-10-%02dT00:00:00Z"}' % (23000 + i, i, i * 10, 1 + i % 28) for i in range(1, 41))
    page = LIST_HTML.replace("</body>", f"<script>var list=[{items}];</script></body>")

    def handler(req: httpx.Request):
        if "/solution/" in req.url.path:
            return httpx.Response(200, html="<h1>x</h1><h2>Solution</h2><p>Do the thing that fixes it properly.</p>")
        return httpx.Response(200, html=page)
    assert articles.sync(httpx.MockTransport(handler)) == 42
    with SessionLocal() as s:
        a = s.get(TrendArticle, "KA-0023040")
        assert a.title == "Agent issue number 40 on Linux" and a.views == 400 and not a.pinned
        assert s.get(TrendArticle, "KA-0025062").pinned
    assert "40 with a view count" in articles.check(httpx.MockTransport(handler))
    with TestClient(app) as http:
        login(http)
        page = http.get("/articles?sort=views").text
        assert page.index("Agent issue number 40") < page.index("Agent issue number 39") and "Top viewed" in page


def test_encoded_article_titles_are_shown_readable():
    with SessionLocal() as s:
        s.add(TrendArticle(ka="KA-0024909", title="AWS+PAYG+Upgrade+from+Cloud+One+to+Vision+One", sources="vision-one"))
        s.commit()
    with TestClient(app) as http:
        login(http)
        assert "AWS PAYG Upgrade from Cloud One to Vision One" in http.get("/articles").text


def test_seed_standard_tools_and_show_them_in_guides():
    from app import cli
    link = "https://contoso-my.sharepoint.com/:f:/g/personal/x/Tools?e=1"
    cli.main(["seed-tools", "--url", link])
    cli.main(["seed-tools", "--url", link])  # running it again updates, never duplicates
    with SessionLocal() as s:
        assert s.query(ToolLink).count() == 5
    cli.main(["seed-tools", "--url", "https://contoso-my.sharepoint.com/:u:/g/scut.zip", "--only", "scut"])
    with TestClient(app) as http:
        login(http)
        page = http.get("/tools").text
        assert "Apex One Security Agent Cleanup Tool (SCUT)" in page and "cdt-26-win-en-1270.zip" in page
        assert "/:u:/g/scut.zip" in page
        g = http.get("/troubleshooting/apex-one/uninstall-agent").text
        assert "Trend tools for this" in g and "/:u:/g/scut.zip" in g


def test_daily_log_is_dated_automatically_and_grouped_by_day():
    from datetime import timedelta
    from app.console import local_now
    from app.db import DailyEntry
    with TestClient(app) as http:
        login(http)
        assert "Daily log" in http.get("/log").text
        http.post("/log", data={"kind": "Call", "client": "ABC", "product": "deep-security", "status": "Resolved",
                                "summary": "DS agents offline after cert renewal, reactivated 3 Linux servers"})
        http.post("/log", data={"kind": "Email", "client": "XYZ", "product": "apex-one", "status": "Follow-up",
                                "summary": "Pattern update failing on 12 agents", "trend_case": "TM-04000009"})
        with SessionLocal() as s:
            assert {e.day for e in s.query(DailyEntry)} == {local_now().date()}
            old = s.query(DailyEntry).filter_by(client="XYZ").one()
            old.day = local_now().date() - timedelta(days=1)
            eid = old.id
            s.commit()
        page = http.get("/log").text
        assert page.index("Today ·") < page.index("Yesterday ·") and "reactivated 3 Linux servers" in page
        assert "Pattern update failing" not in http.get("/log?range=today").text
        assert "reactivated 3 Linux" not in http.get("/log?client=XYZ").text
        http.post("/log", data={"edit_id": eid, "summary": "Pattern update fixed", "status": "Resolved", "client": "XYZ"})
        csv = http.get("/log.csv").text
        assert csv.startswith("Date,Time,Who") and "Pattern update fixed" in csv and "admin" in csv
        http.post(f"/log/{eid}/delete")
        assert "Pattern update fixed" not in http.get("/log").text


def test_users_with_hashed_passwords_can_sign_in():
    from app import cli, users
    from app.db import User
    assert users.set_password("Rutav", "s3cret-pass") is True
    assert users.set_password("rutav", "n3w-pass-123") is False
    with SessionLocal() as s:
        u = s.query(User).filter_by(name="rutav").one()
        assert u.role == "admin" and "n3w-pass" not in u.pw_hash
    with TestClient(app) as http:
        assert http.post("/login", data={"username": "rutav", "password": "s3cret-pass"}).status_code == 401
        r = http.post("/login", data={"username": "RUTAV", "password": "n3w-pass-123"}, follow_redirects=False)
        assert r.status_code == 303
        assert "rutav" in http.get("/work").text  # shows up as an owner choice
    cli.main(["remove-user", "rutav"])
    with TestClient(app) as http:
        assert http.post("/login", data={"username": "rutav", "password": "n3w-pass-123"}).status_code == 401


def test_scripts_grouped_and_quick_search():
    with TestClient(app) as http:
        assert http.get("/find?q=x", follow_redirects=False).status_code in (302, 303, 401)
        login(http)
        page = http.get("/scripts").text
        assert page.count('class="srow') == 5            # Windows and Linux pairs share a row
        with SessionLocal() as s:
            s.add(KnowledgeCase(title="[ABC] Sensor offline after upgrade", client="ABC", product="xdr",
                                case_no="TM-04000009", issue="Sensor offline"))
            s.commit()
        items = http.get("/find?q=uninstall").json()
        groups = {i["group"] for i in items}
        assert "Guides" in groups and all(i["url"].startswith("/") for i in items)
        assert any("Deep Security" in i["title"] for i in items)
        hits = http.get("/find?q=TM-04000009").json()
        assert hits and hits[0]["url"].startswith("/library")
        assert http.get("/find?q=scripts").json()[0]["url"] == "/scripts"
        assert http.get("/find?q=").json()


def test_one_click_onedrive_log_folder(monkeypatch):
    from app import onedrive
    from app.db import LogFolder
    monkeypatch.setattr(settings, "graph_tenant_id", "t")
    monkeypatch.setattr(settings, "graph_client_id", "c")
    monkeypatch.setattr(settings, "onedrive_root", "Client logs")
    seen, made = [], {}

    def handler(req: httpx.Request):
        seen.append((req.method, req.url.path))
        if req.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "A", "refresh_token": "R2"})
        if req.method == "GET" and "/root:/" in req.url.raw_path.decode():
            path = req.url.raw_path.decode().split("/root:/")[1]
            return httpx.Response(200, json=made[path]) if path in made else httpx.Response(404)
        if req.method == "POST" and req.url.path.endswith("/children"):
            from urllib.parse import quote
            name = __import__("json").loads(req.content)["name"]
            pid = req.url.path.split("/items/")[1].split("/")[0] if "/items/" in req.url.path else ""
            parent = next((k for k, v in made.items() if v["id"] == pid), "")
            item = {"id": f"id{len(made)}", "name": name, "webUrl": f"https://od/{name}"}
            made[(parent + "/" if parent else "") + quote(name)] = item
            return httpx.Response(201, json=item)
        if req.url.path.endswith("/createLink"):
            body = __import__("json").loads(req.content)
            if body["type"] == "createOnly":
                return httpx.Response(200, json={"link": {"webUrl": "https://od/upload-only"}})
        return httpx.Response(500)

    real = onedrive.create_log_folder
    monkeypatch.setattr(onedrive, "create_log_folder",
                        lambda *a, **k: real(*a, transport=httpx.MockTransport(handler), **k))
    assert onedrive.folder_path("ABC", "10.1.1.1", "TM-04000009")[1:] == ["ABC", onedrive.folder_path("x", "")[2] + " 10.1.1.1 TM-04000009"]
    assert onedrive._safe('a:b*c?/d') == "a-b-c-d"
    with TestClient(app) as http:
        login(http)
        page = http.get("/uploads").text
        assert "isn't connected yet" in page
        onedrive._store("R1")
        r = http.post("/uploads", data={"client": "ABC", "host": "10.1.1.1", "trend_case": "TM-04000009"}, follow_redirects=False)
        assert r.status_code == 303 and "made=" in r.headers["location"], r.headers["location"]
        page = http.get(r.headers["location"]).text
        assert "https://od/upload-only" in page and "Upload only" in page and "10.1.1.1" in page
        assert "Please upload the diagnostic logs from 10.1.1.1 for Trend case TM-04000009" in page
        assert len([m for m in seen if m[1].endswith("/children")]) == 3
        # same folder again: no new folders, still one link
        http.post("/uploads", data={"client": "ABC", "host": "10.1.1.1", "trend_case": "TM-04000009"})
        assert len([m for m in seen if m[1].endswith("/children")]) == 3
        with SessionLocal() as s:
            assert s.query(LogFolder).count() == 2
        assert onedrive._stored() == "R2"                       # rotated refresh token kept
        bad = http.post("/uploads", data={"client": "", "host": "x"}, follow_redirects=False)
        assert "err=" in bad.headers["location"]


def test_log_folder_through_rclone():
    import subprocess
    from app import onedrive
    calls = []

    def run(args):
        calls.append(args)
        out = {"anonymous": "https://od/anyone-edit\n", "organization": "https://od/mine\n"}
        scope = args[args.index("--onedrive-link-scope") + 1] if "--onedrive-link-scope" in args else ""
        return subprocess.CompletedProcess(args, 0, out.get(scope, ""), "")

    r = onedrive.create_log_folder("ABC", "10.1.1.1", "", run=run)
    assert calls[0][0] == "mkdir" and calls[0][1].startswith("onedrive:Client logs/ABC/") and calls[0][1].endswith(" 10.1.1.1")
    assert r["upload_url"] == "https://od/anyone-edit" and r["folder_url"] == "https://od/mine" and r["link_kind"] == "anyone can upload"

    def blocked(args):
        if args[0] == "link" and "anonymous" in args:
            return subprocess.CompletedProcess(args, 1, "", "sharing is disabled for this tenant")
        return subprocess.CompletedProcess(args, 0, "https://od/mine\n" if args[0] == "link" else "", "")
    r = onedrive.create_log_folder("ABC", "10.1.1.1", "", run=blocked)
    assert r["upload_url"] == "" and r["folder_url"] == "https://od/mine"
    try:
        onedrive.create_log_folder("ABC", "h", "", run=lambda a: subprocess.CompletedProcess(a, 1, "", "couldn't find token"))
        raise AssertionError("should fail")
    except RuntimeError as e:
        assert "couldn't find token" in str(e)
