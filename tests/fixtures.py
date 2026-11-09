from datetime import datetime, timedelta, timezone

NOW = datetime.now(timezone.utc)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def endpoint(guid, name, manager, status="on", days_ago=0, update="upToDate", edr="enabled", os_name="Windows"):
    item = {
        "agentGuid": guid, "endpointName": name, "type": "server" if "Server" in manager else "desktop",
        "osName": os_name, "osVersion": "10", "ipAddresses": ["10.0.0.%d" % (len(guid) % 250)],
        "edrSensor": {"status": edr, "connectivity": "connected", "lastConnectedDateTime": iso(NOW - timedelta(days=days_ago))},
    }
    if manager:
        item["eppAgent"] = {"protectionManager": manager, "policyName": "Default", "status": status,
                            "lastConnectedDateTime": iso(NOW - timedelta(days=days_ago)),
                            "componentUpdateStatus": update}
    return item


ENDPOINTS = [
    endpoint("g1", "BL-LAP-001", "Standard Endpoint Protection"),
    endpoint("g2", "BL-LAP-002", "Standard Endpoint Protection", status="off", days_ago=10, update="outdated"),
    endpoint("g3", "BL-SRV-DB01", "Server & Workload Protection", os_name="Linux"),
    endpoint("g4", "BL-SRV-WEB01", "Server & Workload Protection"),
    endpoint("g5", "BL-KIOSK", ""),
]
ALERTS = [
    {"id": "WB-1", "severity": "high", "status": "Open"},
    {"id": "WB-2", "severity": "critical", "status": "Closed"},
    {"id": "WB-3", "severity": "medium", "status": "In Progress"},
]
