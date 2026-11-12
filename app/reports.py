"""Build a per-client PDF health report from the stored data."""
import html
import io
from datetime import timedelta

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import select

from .collector import components_behind, is_outdated, latest_components, pattern_status
from .db import Client, Endpoint, Snapshot, utcnow

_PROT = {"SEP": "Standard Endpoint Protection", "SWP": "Server & Workload Protection"}

TABLE_STYLE = TableStyle([
    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2a44")),
    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
    ("FONTSIZE", (0, 0), (-1, -1), 8.5),
    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c8ccd4")),
    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f5f8")]),
    ("VALIGN", (0, 0), (-1, -1), "TOP"),
])


def _table(rows, widths=None):
    t = Table(rows, colWidths=widths, repeatRows=1)
    t.setStyle(TABLE_STYLE)
    return t


def latest_summary(session, client_id: int) -> Snapshot | None:
    return session.scalars(select(Snapshot).where(Snapshot.client_id == client_id)
                           .order_by(Snapshot.taken_at.desc()).limit(1)).first()


def global_latest(session) -> dict[str, dict[str, str]]:
    """{protection: {pattern: newest version found across all enabled clients}}."""
    clients = session.scalars(select(Client).where(Client.enabled.is_(True))).all()
    snaps = [latest_summary(session, c.id) for c in clients]
    return latest_components([sn.summary for sn in snaps if sn])


def endpoints_with_old_patterns(session, client_id: int, latest: dict[str, dict[str, str]]) -> int:
    rows = session.execute(select(Endpoint.components, Endpoint.protection).where(Endpoint.client_id == client_id)).all()
    return sum(1 for c, p in rows if components_behind(c, latest.get(p, {})))


def build_report(session, client: Client) -> tuple[bytes, dict]:
    snap = latest_summary(session, client.id)
    if not snap:
        raise RuntimeError(f"No data collected yet for {client.name}")
    s = snap.summary
    week_ago = session.scalars(select(Snapshot).where(Snapshot.client_id == client.id,
                                                      Snapshot.taken_at <= snap.taken_at - timedelta(days=7))
                               .order_by(Snapshot.taken_at.desc()).limit(1)).first()
    endpoints = session.scalars(select(Endpoint).where(Endpoint.client_id == client.id)
                                .order_by(Endpoint.name)).all()

    styles = getSampleStyleSheet()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=15 * mm, bottomMargin=15 * mm,
                            title=f"{client.name} - Vision One health report")
    story = [
        Paragraph(f"{client.name}: Trend Vision One health report", styles["Title"]),
        Paragraph(f"Data as of {snap.taken_at:%d %b %Y %H:%M} UTC &nbsp;·&nbsp; Region {client.region.upper()}",
                  styles["Normal"]),
        Spacer(1, 8),
        Paragraph("Summary", styles["Heading2"]),
    ]

    def delta(key):
        if not week_ago or key not in week_ago.summary:
            return ""
        d = s.get(key, 0) - week_ago.summary.get(key, 0)
        return f"{d:+d}" if d else "0"

    rows = [["Metric", "Now", "Change vs 7 days ago"]]
    for label, key in [("Total endpoints", "total"), ("Standard Endpoint Protection", "sep"),
                       ("Server & Workload Protection", "swp"), ("Sensor only (no EPP agent)", "sensor_only"),
                       ("Online", "online"), ("Offline", "offline"),
                       ("Not seen for 7+ days", "stale"), ("Patterns up to date", "up_to_date"),
                       ("Patterns outdated", "outdated"), ("Pattern status unknown", "components_unknown"),
                       ("Agents needing update (Trend)", "behind_latest")]:
        rows.append([label, str(s.get(key, 0)), delta(key)])
    story.append(_table(rows, [90 * mm, 30 * mm, 50 * mm]))

    latest = s.get("latest_versions") or {}
    for prot, versions in (s.get("by_agent_version") or {}).items():
        if not versions:
            continue
        story += [Spacer(1, 8), Paragraph(f"Agent versions: {_PROT.get(prot, prot)}", styles["Heading2"])]
        vrows = [["Agent version", "Endpoints", ""]]
        for v, n in versions.items():
            vrows.append([v, str(n), "latest" if v == latest.get(prot) else ""])
        story.append(_table(vrows, [70 * mm, 30 * mm, 30 * mm]))

    latest_all = global_latest(session)
    cstat = s.get("component_status") or {}
    if cstat:
        story += [Spacer(1, 8), Paragraph("Pattern status by protection type", styles["Heading2"])]
        crow = [["Protection", "Latest", "Outdated", "Unknown"]]
        for prot, st in cstat.items():
            crow.append([_PROT.get(prot, prot), str(st.get("latest", 0)), str(st.get("outdated", 0)), str(st.get("unknown", 0))])
        story.append(_table(crow, [75 * mm, 30 * mm, 30 * mm, 30 * mm]))
    avs = s.get("agent_version_status") or {}
    if avs:
        labels = {"latestVersion": "Latest", "updateRecommended": "Update recommended", "updateFailed": "Update failed",
                  "osNotSupported": "OS not supported", "unknownVersions": "Unknown"}
        story += [Spacer(1, 8), Paragraph("Agent version status", styles["Heading2"])]
        story.append(_table([["Status", "Endpoints"]] + [[labels.get(k, k), str(v)] for k, v in avs.items()], [75 * mm, 30 * mm]))
    for prot, prows in pattern_status(s, latest_all).items():
        story += [Spacer(1, 8), Paragraph(f"Exact pattern & engine versions: {_PROT.get(prot, prot)}", styles["Heading2"]),
                  Paragraph("Latest = newest version found across all managed clients.", styles["Italic"])]
        prow = [["Component", "Latest version", "On latest", "Behind"]]
        for r in prows:
            prow.append([r["name"][:45], r["latest"] or "-", str(r["current"]), str(r["behind"])])
        story.append(_table(prow, [75 * mm, 40 * mm, 25 * mm, 25 * mm]))

    if s.get("by_os"):
        story += [Spacer(1, 8), Paragraph("Operating systems", styles["Heading2"])]
        story.append(_table([["OS", "Endpoints"]] + [[k, str(v)] for k, v in s["by_os"].items()], [90 * mm, 30 * mm]))

    attention = [e for e in endpoints if e.stale or not e.online or e.behind_latest
                 or components_behind(e.components, latest_all.get(e.protection, {}))
                 or is_outdated({"update_status": e.update_status, "component_version": e.component_version})]
    story += [Spacer(1, 8), Paragraph(f"Endpoints needing attention ({len(attention)})", styles["Heading2"])]
    if attention:
        arows = [["Endpoint", "Protection", "Status", "Last connected", "Agent version", "Patterns behind"]]
        for e in attention[:200]:
            behind = components_behind(e.components, latest_all.get(e.protection, {}))
            arows.append([e.name[:36], e.protection, ("stale" if e.stale else e.agent_status or "offline"),
                          e.last_connected.strftime("%d %b %Y %H:%M") if e.last_connected else "-",
                          (e.agent_version or "-") + (" (old)" if e.behind_latest else ""),
                          str(len(behind)) if behind else (e.update_status or "-")])
        story.append(_table(arows, [48 * mm, 18 * mm, 18 * mm, 32 * mm, 34 * mm, 30 * mm]))
        if len(attention) > 200:
            story.append(Paragraph(f"…and {len(attention) - 200} more.", styles["Italic"]))
    else:
        story.append(Paragraph("None. All endpoints are online and up to date.", styles["Normal"]))

    story += [Spacer(1, 12), Paragraph(f"Generated {utcnow():%d %b %Y %H:%M} UTC by the Centralised Vision One dashboard.",
                                       styles["Italic"])]
    doc.build(story)
    return buf.getvalue(), s


def report_email_html(client: Client, s: dict, old_patterns: int | None = None) -> str:
    latest = s.get("latest_versions") or {}
    latest_line = "".join(f"<li>Latest {_PROT.get(p, p)} agent version: {html.escape(v)}</li>" for p, v in latest.items())
    return f"""<p>Hi team,</p>
<p>Please find attached the Trend Vision One endpoint health report for <b>{html.escape(client.name)}</b>.</p>
<ul>
<li>Total endpoints: {s.get('total', 0)} (Standard Endpoint Protection {s.get('sep', 0)}, Server &amp; Workload Protection {s.get('swp', 0)})</li>
<li>Online: {s.get('online', 0)} &nbsp; Offline: {s.get('offline', 0)} &nbsp; Not seen 7+ days: {s.get('stale', 0)}</li>
<li>Patterns up to date: {s.get('up_to_date', 0)} &nbsp; Outdated: {s.get('outdated', 0)} &nbsp; Unknown: {s.get('components_unknown', 0)}</li>
<li>Agents needing an update: {s.get('behind_latest', 0)}</li>
{f"<li>Endpoints with at least one pattern behind latest: {old_patterns}</li>" if old_patterns is not None else ""}
{latest_line}
</ul>
<p>Regards,</p>"""
