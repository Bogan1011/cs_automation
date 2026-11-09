"""Pull data from every enabled client and store the latest state + a summary snapshot."""
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from . import crypto
from .config import settings
from .db import Client, Endpoint, SessionLocal, Snapshot, utcnow
from .visionone import VisionOneClient, VisionOneError

log = logging.getLogger("collector")


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    # Vision One sometimes returns timestamps without an offset; they are UTC.
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def classify_protection(item: dict) -> str:
    epp = item.get("eppAgent") or {}
    manager = str(epp.get("protectionManager") or item.get("protectionManager") or "").lower()
    if "server" in manager or "workload" in manager:
        return "SWP"
    if "standard" in manager:
        return "SEP"
    if manager:
        return manager[:40]
    return "Sensor only" if item.get("edrSensor") else "None"


def normalize_endpoint(item: dict, now: datetime | None = None) -> dict:
    now = now or utcnow()
    epp = item.get("eppAgent") or {}
    edr = item.get("edrSensor") or {}
    ips = item.get("ipAddresses") or item.get("lastUsedIp") or []
    if isinstance(ips, str):
        ips = [ips]
    last_conn = _parse_dt(epp.get("lastConnectedDateTime")) or _parse_dt(edr.get("lastConnectedDateTime"))
    status = str(epp.get("status") or edr.get("connectivity") or edr.get("status") or "").lower()
    online = status in ("on", "online", "connected", "enabled", "normal")
    if not status and last_conn:
        online = now - last_conn < timedelta(hours=1)
    stale = bool(last_conn and now - last_conn > timedelta(days=settings.stale_days))
    os_name = " ".join(str(x) for x in [item.get("osName"), item.get("osVersion")] if x)
    return {
        "agent_guid": str(item.get("agentGuid") or item.get("endpointGuid") or item.get("endpointName")),
        "name": str(item.get("endpointName") or item.get("displayName") or ""),
        "endpoint_type": str(item.get("type") or item.get("osPlatform") or ""),
        "os": os_name,
        "ip": ", ".join(ips)[:255],
        "protection": classify_protection(item),
        "policy": str(epp.get("policyName") or ""),
        "agent_status": status,
        "online": online,
        "stale": stale,
        "update_status": str(epp.get("componentUpdateStatus") or ""),
        "edr_status": str(edr.get("status") or ""),
        "last_connected": last_conn,
        "raw": item,
    }


def summarize(endpoints: list[dict], alerts: list[dict] | None) -> dict:
    prot = Counter(e["protection"] for e in endpoints)
    os_counter = Counter((e["os"].split(" ")[0] if e["os"] else "Unknown") for e in endpoints)
    outdated = [e for e in endpoints if e["update_status"] and e["update_status"].lower() not in ("uptodate", "up-to-date", "up to date", "latest", "normal")]
    summary = {
        "total": len(endpoints),
        "sep": prot.get("SEP", 0),
        "swp": prot.get("SWP", 0),
        "sensor_only": prot.get("Sensor only", 0),
        "unprotected": prot.get("None", 0),
        "online": sum(1 for e in endpoints if e["online"]),
        "offline": sum(1 for e in endpoints if not e["online"]),
        "stale": sum(1 for e in endpoints if e["stale"]),
        "outdated": len(outdated),
        "edr_enabled": sum(1 for e in endpoints if e["edr_status"].lower() in ("enabled", "on", "connected")),
        "by_protection": dict(prot),
        "by_os": dict(os_counter.most_common(8)),
    }
    if alerts is not None:
        sev = Counter(str(a.get("severity", "unknown")).lower() for a in alerts)
        open_alerts = [a for a in alerts if str(a.get("status") or a.get("investigationStatus") or "").lower() not in ("closed", "true positive", "false positive", "benign true positive")]
        summary["alerts_7d"] = {"total": len(alerts), "open": len(open_alerts), **dict(sev)}
    return summary


def collect_client(session, client: Client, v1: VisionOneClient | None = None) -> dict:
    own = v1 is None
    v1 = v1 or VisionOneClient(crypto.decrypt(client.api_key_enc), client.region)
    try:
        raw_endpoints = v1.list_endpoints()
        try:
            alerts = v1.list_alerts(days=7)
        except VisionOneError as exc:  # key may lack Workbench permission; keep endpoints anyway
            log.warning("%s: alerts unavailable: %s", client.name, exc)
            alerts = None
    finally:
        if own:
            v1.close()

    now = utcnow()
    rows = [normalize_endpoint(i, now) for i in raw_endpoints]
    # de-duplicate by guid
    rows = list({r["agent_guid"]: r for r in rows}.values())
    summary = summarize(rows, alerts)

    session.execute(delete(Endpoint).where(Endpoint.client_id == client.id))
    session.add_all(Endpoint(client_id=client.id, **r) for r in rows)
    session.add(Snapshot(client_id=client.id, taken_at=now, summary=summary))
    client.last_poll_at, client.last_poll_ok, client.last_error = now, True, None
    return summary


def collect_all() -> None:
    with SessionLocal() as session:
        clients = session.scalars(select(Client).where(Client.enabled.is_(True))).all()
        for client in clients:
            try:
                summary = collect_client(session, client)
                session.commit()
                log.info("%s: %s endpoints", client.name, summary["total"])
            except Exception as exc:  # one client failing must not stop the others
                session.rollback()
                client = session.get(Client, client.id)
                client.last_poll_at, client.last_poll_ok, client.last_error = utcnow(), False, str(exc)[:1000]
                session.commit()
                log.exception("%s: poll failed", client.name)
        cutoff = datetime.now(timezone.utc) - timedelta(days=settings.retention_days)
        session.execute(delete(Snapshot).where(Snapshot.taken_at < cutoff))
        session.commit()
