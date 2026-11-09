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


def _sensor_installed(edr: dict) -> bool:
    if not edr:
        return False
    status = str(edr.get("status") or edr.get("connectivity") or "").replace(" ", "").lower()
    if status in ("notinstalled", "uninstalled", "none"):
        return False
    return any(edr.get(k) for k in ("version", "connectivity", "lastConnectedDateTime", "status"))


def classify_protection(item: dict) -> str:
    """Match the console's 'Security deployment' groups: SEP, SWP, Sensor only, or Unmanaged.

    Seen in the console: Standard Endpoint Protection agents report protection manager "APEX ONE"
    (or "Standard Endpoint Protection"); Server & Workload agents report "Server and Workload Protection".
    """
    epp = item.get("eppAgent") or {}
    manager = str(epp.get("protectionManager") or item.get("protectionManager") or "").lower()
    if "server" in manager or "workload" in manager or "deep security" in manager:
        return "SWP"
    if "standard" in manager or "apex" in manager or "officescan" in manager:
        return "SEP"
    if manager:
        return manager[:40]
    if _sensor_installed(item.get("edrSensor") or {}):
        return "Sensor only"
    return "Unmanaged"


_VERSION_RE = re.compile(r"^\d+(\.\d+)+$")
_NAME_KEYS = ("componentName", "name", "displayName", "component", "type")
_VER_KEYS = ("version", "componentVersion", "currentVersion", "patternVersion")
# Agent/sensor program versions are tracked separately, not as patterns.
_SKIP_PATHS = {"eppAgent.version", "eppAgent.agentVersion", "edrSensor.version", "edrSensor.sensorVersion"}


def _pretty(label: str) -> str:
    label = label.split(".")[-1].replace("[]", "")
    label = re.sub(r"(Version|Ver)$", "", label)
    label = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", label).replace("_", " ").strip()
    return label[:1].upper() + label[1:]


def extract_components(item: dict) -> dict[str, str]:
    """Find pattern/engine versions anywhere in an endpoint record.

    Handles both shapes Vision One may use:
      * lists of {"name"/"componentName": ..., "version": ...}
      * flat keys such as "virusPatternVersion": "19.123.00"
    """
    found: dict[str, str] = {}

    def add(label, ver):
        ver = str(ver).strip()
        if label and _VERSION_RE.match(ver):
            label = str(label).strip()
            if label not in found or version_key(ver) > version_key(found[label]):
                found[label] = ver

    def walk(obj, path=""):
        if isinstance(obj, dict):
            name = next((obj[k] for k in _NAME_KEYS if isinstance(obj.get(k), str) and obj.get(k)), None)
            ver = next((obj[k] for k in _VER_KEYS if obj.get(k) not in (None, "")), None)
            if name and ver is not None and not isinstance(ver, (dict, list)):
                add(name, ver)
                return
            for k, v in obj.items():
                sub = f"{path}.{k}" if path else k
                if isinstance(v, (dict, list)):
                    walk(v, sub)
                elif sub not in _SKIP_PATHS and re.search(r"pattern|engine|signature|rule|filter", k, re.I):
                    add(_pretty(k), v)
        elif isinstance(obj, list):
            for x in obj:
                walk(x, path + "[]")

    walk(item)
    return found


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
        "components": extract_components(item),
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


def _component_counts(endpoints: list[dict]) -> dict[str, dict[str, int]]:
    out: dict[str, Counter] = {}
    for e in endpoints:
        for comp, ver in (e.get("components") or {}).items():
            out.setdefault(comp, Counter())[ver] += 1
    return {c: dict(sorted(v.items(), key=lambda kv: version_key(kv[0]), reverse=True)) for c, v in sorted(out.items())}


def latest_components(summaries: list[dict]) -> dict[str, str]:
    """Newest version of each pattern seen across all clients' latest snapshots."""
    latest: dict[str, str] = {}
    for s in summaries:
        for comp, versions in (s.get("by_component") or {}).items():
            for v in versions:
                if comp not in latest or version_key(v) > version_key(latest[comp]):
                    latest[comp] = v
    return latest


def components_behind(components: dict | None, latest: dict[str, str]) -> list[str]:
    return sorted(c for c, v in (components or {}).items()
                  if c in latest and version_key(v) < version_key(latest[c]))


def pattern_status(summary: dict, latest: dict[str, str]) -> list[dict]:
    """Per pattern: latest version, endpoints on it, endpoints behind."""
    rows = []
    for comp, versions in (summary.get("by_component") or {}).items():
        lv = latest.get(comp)
        current = sum(n for v, n in versions.items() if v == lv)
        total = sum(versions.values())
        rows.append({"name": comp, "latest": lv, "current": current, "behind": total - current, "total": total,
                     "versions": versions})
    return sorted(rows, key=lambda r: (-r["behind"], r["name"]))


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
        "by_component": _component_counts(endpoints),
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
    # Only "All managed endpoints" (SEP + SWP + Sensor only) count, as in the console.
    unmanaged = sum(1 for r in rows if r["protection"] == "Unmanaged")
    rows = [r for r in rows if r["protection"] != "Unmanaged"]
    summary = summarize(rows)
    summary["unmanaged"] = unmanaged

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
