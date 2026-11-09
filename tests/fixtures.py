from datetime import datetime, timedelta, timezone

NOW = datetime.now(timezone.utc)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def endpoint(guid, name, manager, status="on", days_ago=0, update="upToDate", edr="enabled", os_name="Windows", version=None):
    item = {
        "agentGuid": guid, "endpointName": name, "type": "server" if "Server" in manager else "desktop",
        "osName": os_name, "osVersion": "10", "ipAddresses": ["10.0.0.%d" % (len(guid) % 250)],
        "edrSensor": {"status": edr, "version": "1.2.0.3456", "connectivity": "connected", "lastConnectedDateTime": iso(NOW - timedelta(days=days_ago))},
    }
    if manager:
        item["eppAgent"] = {"protectionManager": manager, "policyName": "Default", "status": status,
                            "lastConnectedDateTime": iso(NOW - timedelta(days=days_ago)),
                            "componentUpdateStatus": update,
                            "version": version or ("20.0.1.7000" if "Server" in manager else "14.0.13100.1")}
    return item


ENDPOINTS = [
    endpoint("g1", "BL-LAP-001", "Standard Endpoint Protection"),
    endpoint("g2", "BL-LAP-002", "Standard Endpoint Protection", status="off", days_ago=10, update="outdated",
             version="14.0.12900.1"),
    endpoint("g3", "BL-SRV-DB01", "Server & Workload Protection", os_name="Linux"),
    endpoint("g4", "BL-SRV-WEB01", "Server & Workload Protection"),
    endpoint("g5", "BL-KIOSK", ""),
    endpoint("g6", "BL-LAP-APEX", "APEX ONE"),
    endpoint("g7", "BL-SRV-AND", "Server and Workload Protection"),
    # unmanaged (discovered, no agent/sensor) — must not be counted
    {"agentGuid": "u1", "endpointName": "UNMANAGED-PC", "eppAgent": {}, "edrSensor": {}},
    {"agentGuid": "u2", "endpointName": "UNMANAGED-PC2"},
]
