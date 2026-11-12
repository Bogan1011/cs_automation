"""Pull data from every enabled client and store the latest state + a summary snapshot."""
import logging
import re
from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from . import crypto
from .config import settings
from .db import Client, Endpoint, EndpointDetail, SessionLocal, Snapshot, utcnow
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


def has_agent(epp: dict) -> bool:
    v = str(epp.get("version") or "").strip()
    return bool(v) and v.strip("0.") != ""


def classify_protection(item: dict) -> str:
    """Match the console's 'Security deployment' groups: SEP, SWP, Sensor only, or Unmanaged.

    Seen in the console: Standard Endpoint Protection agents report protection manager "APEX ONE"
    (or "Standard Endpoint Protection"); Server & Workload agents report "Server and Workload Protection".
    """
    epp = item.get("eppAgent") or {}
    manager = str(epp.get("protectionManager") or item.get("protectionManager") or "").lower()
    if manager:
        # SWP lists cloud/connector-discovered computers with no agent: version blank or 0.0.0.0.
        # Those are not in the console's "All managed endpoints".
        if not has_agent(epp):
            return "Unmanaged"
        if "server" in manager or "workload" in manager or "deep security" in manager:
            return "SWP"
        if "standard" in manager or "apex" in manager or "officescan" in manager:
            return "SEP"
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
    epp_status = str(epp.get("status") or "").lower()
    status = epp_status if epp_status in ("on", "off") else str(edr.get("connectivity") or epp_status or "").lower()
    online = status in ("on", "online", "connected")
    if not status and last_conn:
        online = now - last_conn < timedelta(hours=1)
    stale = bool(last_conn and now - last_conn > timedelta(days=settings.stale_days))
    agent_version = str(epp.get("version") or epp.get("agentVersion") or "")
    sensor_version = str(edr.get("version") or edr.get("sensorVersion") or "")
    component_version = str(epp.get("componentVersion") or edr.get("componentVersion") or "")
    agent_version_status = str(item.get("agentVersionStatus") or epp.get("agentVersionStatus") or "")
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
        "agent_version_status": agent_version_status[:40],
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


AGENT_BEHIND = ("updaterecommended", "updatefailed", "outdatedversion")


def component_state(e: dict) -> str:
    """latest / outdated / unknown, from Trend's componentVersion verdict (falls back to update status)."""
    v = (e.get("component_version") or "").replace(" ", "").lower()
    if v.startswith("latest") or v in ("uptodate", "up-to-date"):
        return "latest"
    if is_outdated(e):
        return "outdated"
    return "unknown"


def mark_latest(endpoints: list[dict]) -> dict:
    """Latest agent version seen per protection type (SEP and SWP have separate version lines).
    Sets e['behind_latest'] and returns {protection: latest_version}."""
    latest: dict[str, str] = {}
    for e in endpoints:
        v = e.get("agent_version")
        if v and (e["protection"] not in latest or version_key(v) > version_key(latest[e["protection"]])):
            latest[e["protection"]] = v
    for e in endpoints:
        trend = (e.get("agent_version_status") or "").lower()
        if trend:  # Trend's own verdict wins
            e["behind_latest"] = trend in AGENT_BEHIND
        else:
            v = e.get("agent_version")
            e["behind_latest"] = bool(v and version_key(v) < version_key(latest[e["protection"]]))
    return latest


def _component_counts(endpoints: list[dict]) -> dict[str, dict[str, dict[str, int]]]:
    """{protection: {component: {version: count}}}. SEP and SWP are kept apart because the same
    pattern name can use a different version line on each (e.g. Damage Recovery Pattern 17002 vs 1.7.2)."""
    out: dict[str, dict[str, Counter]] = {}
    for e in endpoints:
        for comp, ver in (e.get("components") or {}).items():
            if comp.startswith("_"):
                continue
            out.setdefault(e["protection"], {}).setdefault(comp, Counter())[str(ver)] += 1
    return {p: {c: dict(sorted(v.items(), key=lambda kv: version_key(kv[0]), reverse=True)) for c, v in sorted(comps.items())}
            for p, comps in out.items()}


def _is_new_format(by_component: dict) -> bool:
    return all(isinstance(v, dict) and all(isinstance(x, dict) for x in v.values()) for v in by_component.values())


def latest_components(summaries: list[dict]) -> dict[str, dict[str, str]]:
    """{protection: {component: newest version seen across all clients}}."""
    latest: dict[str, dict[str, str]] = {}
    for s in summaries:
        bc = s.get("by_component") or {}
        if not _is_new_format(bc):
            continue  # snapshot from before per-protection tracking
        for prot, comps in bc.items():
            for comp, versions in comps.items():
                for v in versions:
                    cur = latest.setdefault(prot, {}).get(comp)
                    if cur is None or version_key(v) > version_key(cur):
                        latest[prot][comp] = v
    return latest


def components_behind(components: dict | None, latest: dict[str, str]) -> list[str]:
    """latest is the {component: version} map for this endpoint's protection type."""
    comps = components or {}
    behind = {c for c, v in comps.items()
              if not c.startswith("_") and c in latest and version_key(str(v)) < version_key(latest[c])}
    behind.update(comps.get("_outdated") or [])  # SWP console's own latest=false flags
    return sorted(behind)


def pattern_status(summary: dict, latest: dict[str, dict[str, str]]) -> dict[str, list[dict]]:
    """{protection: [{name, latest, current, behind, total, versions}]}."""
    out = {}
    bc = summary.get("by_component") or {}
    if not _is_new_format(bc):
        return out
    for prot, comps in bc.items():
        rows = []
        for comp, versions in comps.items():
            lv = (latest.get(prot) or {}).get(comp)
            current = sum(n for v, n in versions.items() if v == lv)
            total = sum(versions.values())
            rows.append({"name": comp, "latest": lv, "current": current, "behind": total - current,
                         "total": total, "versions": versions})
        out[prot] = sorted(rows, key=lambda r: (-r["behind"], r["name"]))
    return out


def detail_components(detail: dict) -> dict[str, str]:
    """Exact versions from GET /v3.0/endpointSecurity/endpoints/{agentGuid}:
    eppAgent.patterns[] and eppAgent.engines[] = {id, name, version, updatedDateTime}."""
    epp = detail.get("eppAgent") or {}
    out = {}
    for section in ("patterns", "engines"):
        for p in epp.get(section) or []:
            if isinstance(p, dict) and p.get("name") and p.get("version") not in (None, ""):
                out[str(p["name"]).strip()] = str(p["version"]).strip()
    return out


def summarize(endpoints: list[dict]) -> dict:
    prot = Counter(e["protection"] for e in endpoints)
    os_counter = Counter((e["os"].split(" ")[0] if e["os"] else "Unknown") for e in endpoints)
    latest = mark_latest(endpoints)
    by_version: dict[str, dict[str, int]] = {}
    for e in endpoints:
        by_version.setdefault(e["protection"], Counter())[e.get("agent_version") or "Unknown"] += 1
    sensor_versions = Counter(e.get("sensor_version") or "Unknown" for e in endpoints)
    # Trend's per-endpoint verdicts (eppAgent.componentVersion / agentVersionStatus)
    comp_status: dict[str, Counter] = {}
    for e in endpoints:
        comp_status.setdefault(e["protection"], Counter())[component_state(e)] += 1
    agent_status = Counter(e.get("agent_version_status") or "unknown" for e in endpoints)
    outdated = sum(1 for e in endpoints if component_state(e) == "outdated")
    up_to_date = sum(1 for e in endpoints if component_state(e) == "latest")
    return {
        "total": len(endpoints),
        "sep": prot.get("SEP", 0),
        "swp": prot.get("SWP", 0),
        "sensor_only": prot.get("Sensor only", 0),
        "online": sum(1 for e in endpoints if e["online"]),
        "offline": sum(1 for e in endpoints if not e["online"]),
        "stale": sum(1 for e in endpoints if e["stale"]),
        "outdated": outdated,
        "up_to_date": up_to_date,
        "components_unknown": len(endpoints) - outdated - up_to_date,
        "component_status": {p: dict(c) for p, c in comp_status.items()},
        "agent_version_status": dict(agent_status.most_common()),
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


def refresh_details(session, client: Client, v1, rows: list[dict], now: datetime) -> dict[str, dict]:
    """Return {agent_guid: components} for managed SEP/SWP endpoints, re-fetching the stalest ones."""
    from concurrent.futures import ThreadPoolExecutor

    cache = {d.agent_guid: d for d in session.scalars(select(EndpointDetail).where(EndpointDetail.client_id == client.id))}
    wanted = [r["agent_guid"] for r in rows if r["protection"] in ("SEP", "SWP")]
    ttl = timedelta(minutes=settings.detail_ttl_minutes)

    def age(g):
        d = cache.get(g)
        if not d or not d.fetched_at:
            return timedelta.max
        fa = d.fetched_at if d.fetched_at.tzinfo else d.fetched_at.replace(tzinfo=timezone.utc)
        return now - fa

    stale = sorted((g for g in wanted if age(g) > ttl), key=age, reverse=True)[: settings.detail_batch]

    def fetch(g):
        try:
            return g, detail_components(v1.get_endpoint(g))
        except Exception as exc:
            log.debug("%s: detail %s failed: %s", client.name, g, exc)
            return g, None

    if stale:
        with ThreadPoolExecutor(max_workers=max(1, settings.detail_workers)) as pool:
            for g, comps in pool.map(fetch, stale):
                if comps is None:
                    continue
                d = cache.get(g)
                if d is None:
                    d = EndpointDetail(client_id=client.id, agent_guid=g)
                    session.add(d)
                    cache[g] = d
                d.components, d.fetched_at = comps, now
        log.info("%s: refreshed exact versions for %d endpoints", client.name, len(stale))
    # forget endpoints that disappeared
    gone = set(cache) - set(wanted)
    if gone:
        session.execute(delete(EndpointDetail).where(EndpointDetail.client_id == client.id,
                                                     EndpointDetail.agent_guid.in_(gone)))
    return {g: cache[g].components for g in wanted if g in cache and cache[g].components}


def collect_client(session, client: Client, v1: VisionOneClient | None = None) -> dict:
    own = v1 is None
    v1 = v1 or VisionOneClient(crypto.decrypt(client.api_key_enc), client.region)
    try:
        raw_endpoints = v1.list_endpoints()
        now = utcnow()
        rows = [normalize_endpoint(i, now) for i in raw_endpoints]
        rows = list({r["agent_guid"]: r for r in rows}.values())  # de-duplicate by guid
        # Only "All managed endpoints" (SEP + SWP + Sensor only) count, as in the console.
        unmanaged = sum(1 for r in rows if r["protection"] == "Unmanaged")
        rows = [r for r in rows if r["protection"] != "Unmanaged"]
        exact = refresh_details(session, client, v1, rows, now)
    finally:
        if own:
            v1.close()

    for r in rows:
        if r["agent_guid"] in exact:
            r["components"] = exact[r["agent_guid"]]
    summary = summarize(rows)
    summary["unmanaged"] = unmanaged
    summary["exact_versions"] = {"have": len(exact), "of": sum(1 for r in rows if r["protection"] in ("SEP", "SWP"))}

    session.execute(delete(Endpoint).where(Endpoint.client_id == client.id))
    session.add_all(Endpoint(client_id=client.id, **r) for r in rows)
    session.add(Snapshot(client_id=client.id, taken_at=now, summary=summary))
    client.last_poll_at, client.last_poll_ok, client.last_error = now, True, None
    return summary


def fetch_swp_components(client: Client) -> dict[str, dict[str, str]]:
    """{lowercase host name: {component: version}} from the SWP console API, if configured."""
    if not (client.swp_url and client.swp_key_enc):
        return {}
    from .swp import SWPClient, computer_key
    out = {}
    with SWPClient(client.swp_url, crypto.decrypt(client.swp_key_enc)) as swp:
        for comp in swp.list_computers():
            versions = swp_versions(comp)
            if versions:
                for k in computer_key(comp):
                    out[k] = versions
    return out


def swp_versions(computer: dict) -> dict:
    """securityUpdates.antiMalware[] = {name, version, platform, latest} -> {label: version}.

    Platform-specific components get the platform in the label (Windows and Linux builds of the
    same pattern have different version lines). Components SWP itself flags latest=false are
    listed under "_outdated" so they count as behind even before another client shows a newer one.
    """
    out: dict = {}
    outdated = []
    su = computer.get("securityUpdates") or {}
    for section, entries in su.items():
        if not isinstance(entries, list):
            continue
        for e in entries:
            if not isinstance(e, dict) or not e.get("name") or e.get("version") in (None, ""):
                continue
            plat = str(e.get("platform") or "")
            label = str(e["name"]).strip() + (f" ({plat})" if plat and plat != "All Platforms" else "")
            out[label] = str(e["version"])
            if e.get("latest") is False or str(e.get("latest")).lower() == "false":
                outdated.append(label)
    if outdated:
        out["_outdated"] = sorted(outdated)
    return out


def fetch_apex_components(client: Client) -> dict[str, dict[str, str]]:
    if not (client.apex_url and client.apex_app_id and client.apex_key_enc):
        return {}
    from .apexone import ApexClient
    out = {}
    with ApexClient(client.apex_url, client.apex_app_id, crypto.decrypt(client.apex_key_enc)) as apex:
        for agent in apex.product_agents():
            versions = extract_components(agent)
            host = str(agent.get("host_name") or agent.get("hostName") or "").strip().lower()
            if host and versions:
                out[host] = versions
                out[host.split(".")[0]] = versions
    return out


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
