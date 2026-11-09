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

from .db import Client, Endpoint, Snapshot, utcnow

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
                       ("Not seen for 7+ days", "stale"), ("Components not up to date", "outdated")]:
        rows.append([label, str(s.get(key, 0)), delta(key)])
    story.append(_table(rows, [90 * mm, 30 * mm, 50 * mm]))

    alerts = s.get("alerts_7d")
    if alerts:
        story += [Spacer(1, 8), Paragraph("Workbench alerts (last 7 days)", styles["Heading2"])]
        arows = [["Total", "Open", "Critical", "High", "Medium", "Low"],
                 [str(alerts.get(k, 0)) for k in ("total", "open", "critical", "high", "medium", "low")]]
        story.append(_table(arows))

    if s.get("by_os"):
        story += [Spacer(1, 8), Paragraph("Operating systems", styles["Heading2"])]
        story.append(_table([["OS", "Endpoints"]] + [[k, str(v)] for k, v in s["by_os"].items()], [90 * mm, 30 * mm]))

    attention = [e for e in endpoints if e.stale or not e.online or (e.update_status and e.update_status.lower() not in ("uptodate", "up-to-date", "up to date", "latest", "normal"))]
    story += [Spacer(1, 8), Paragraph(f"Endpoints needing attention ({len(attention)})", styles["Heading2"])]
    if attention:
        arows = [["Endpoint", "Protection", "Status", "Last connected", "Component update"]]
        for e in attention[:200]:
            arows.append([e.name[:40], e.protection, ("stale" if e.stale else e.agent_status or "offline"),
                          e.last_connected.strftime("%d %b %Y %H:%M") if e.last_connected else "-",
                          e.update_status or "-"])
        story.append(_table(arows, [55 * mm, 22 * mm, 22 * mm, 35 * mm, 45 * mm]))
        if len(attention) > 200:
            story.append(Paragraph(f"…and {len(attention) - 200} more.", styles["Italic"]))
    else:
        story.append(Paragraph("None. All endpoints are online and up to date.", styles["Normal"]))

    story += [Spacer(1, 12), Paragraph(f"Generated {utcnow():%d %b %Y %H:%M} UTC by the Centralised Vision One dashboard.",
                                       styles["Italic"])]
    doc.build(story)
    return buf.getvalue(), s


def report_email_html(client: Client, s: dict) -> str:
    alerts = s.get("alerts_7d") or {}
    alert_line = (f"<li>Workbench alerts in the last 7 days: {alerts.get('total', 0)} "
                  f"({alerts.get('open', 0)} open, {alerts.get('critical', 0)} critical, {alerts.get('high', 0)} high)</li>"
                  if alerts else "")
    return f"""<p>Hi team,</p>
<p>Please find attached the Trend Vision One health report for <b>{html.escape(client.name)}</b>.</p>
<ul>
<li>Total endpoints: {s.get('total', 0)} (Standard Endpoint Protection {s.get('sep', 0)}, Server &amp; Workload Protection {s.get('swp', 0)})</li>
<li>Online: {s.get('online', 0)} &nbsp; Offline: {s.get('offline', 0)} &nbsp; Not seen 7+ days: {s.get('stale', 0)}</li>
<li>Components not up to date: {s.get('outdated', 0)}</li>
{alert_line}
</ul>
<p>Regards,</p>"""
