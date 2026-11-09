"""Pull data from every enabled client and store the latest state + a summary snapshot."""
import logging
import re
from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from . import crypto
from .config import settings
from .db import Client, Endpoint, SessionLocal, Snapshot, utcnow
from .visionone import VisionOneClient

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
    agent_version = str(epp.get("version") or epp.get("agentVersion") or "")
    sensor_version = str(edr.get("version") or edr.get("sensorVersion") or "")
    component_version = str(epp.get("componentVersion") or edr.get("componentVersion") or "")
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
        "agent_version": agent_version[:80],
        "sensor_version": sensor_version[:80],
        "component_version": component_version[:80],
        "edr_status": str(edr.get("status") or ""),
        "last_connected": last_conn,
        "raw": item,
    }


UP_TO_DATE = ("uptodate", "up-to-date", "up to date", "latest", "latestversion", "normal", "ok")


def version_key(v: str) -> tuple:
    """'14.0.12345.1' -> (14, 0, 12345, 1); non-numeric parts sort lowest."""
    return tuple(int(p) if p.isdigit() else -1 for p in re.split(r"[.\-_ ]", v.strip()) if p)


def is_outdated(e: dict) -> bool:
    for field in ("update_status", "component_version"):
        val = (e.get(field) or "").replace(" ", "").lower()
        if val and ("outdated" in val or val in ("notuptodate", "failed", "updatefailed")):
            return True
    return False


def mark_latest(endpoints: list[dict]) -> dict:
    """Latest agent version seen per protection type (SEP and SWP have separate version lines).
    Sets e['behind_latest'] and returns {protection: latest_version}."""
    latest: dict[str, str] = {}
    for e in endpoints:
        v = e.get("agent_version")
        if v and (e["protection"] not in latest or version_key(v) > version_key(latest[e["protection"]])):
            latest[e["protection"]] = v
    for e in endpoints:
        v = e.get("agent_version")
        e["behind_latest"] = bool(v and version_key(v) < version_key(latest[e["protection"]]))
    return latest


def summarize(endpoints: list[dict]) -> dict:
    prot = Counter(e["protection"] for e in endpoints)
    os_counter = Counter((e["os"].split(" ")[0] if e["os"] else "Unknown") for e in endpoints)
    latest = mark_latest(endpoints)
    by_version: dict[str, dict[str, int]] = {}
    for e in endpoints:
        by_version.setdefault(e["protection"], Counter())[e.get("agent_version") or "Unknown"] += 1
    sensor_versions = Counter(e.get("sensor_version") or "Unknown" for e in endpoints)
    outdated = sum(1 for e in endpoints if is_outdated(e))
    return {
        "total": len(endpoints),
        "sep": prot.get("SEP", 0),
        "swp": prot.get("SWP", 0),
        "sensor_only": prot.get("Sensor only", 0),
        "unprotected": prot.get("None", 0),
        "online": sum(1 for e in endpoints if e["online"]),
        "offline": sum(1 for e in endpoints if not e["online"]),
        "stale": sum(1 for e in endpoints if e["stale"]),
        "outdated": outdated,
        "up_to_date": len(endpoints) - outdated,
        "behind_latest": sum(1 for e in endpoints if e.get("behind_latest")),
        "latest_versions": latest,
        "by_agent_version": {p: dict(sorted(c.items(), key=lambda kv: version_key(kv[0]), reverse=True))
                             for p, c in by_version.items()},
        "by_sensor_version": dict(sorted(sensor_versions.items(), key=lambda kv: version_key(kv[0]), reverse=True)[:15]),
        "edr_enabled": sum(1 for e in endpoints if e["edr_status"].lower() in ("enabled", "on", "connected")),
        "by_protection": dict(prot),
        "by_os": dict(os_counter.most_common(8)),
    }


def collect_client(session, client: Client, v1: VisionOneClient | None = None) -> dict:
    own = v1 is None
    v1 = v1 or VisionOneClient(crypto.decrypt(client.api_key_enc), client.region)
    try:
        raw_endpoints = v1.list_endpoints()
    finally:
        if own:
            v1.close()

    now = utcnow()
    rows = [normalize_endpoint(i, now) for i in raw_endpoints]
    # de-duplicate by guid
    rows = list({r["agent_guid"]: r for r in rows}.values())
    summary = summarize(rows)

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
