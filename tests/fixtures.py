from datetime import datetime, timedelta, timezone

NOW = datetime.now(timezone.utc)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def endpoint(guid, name, manager, status="on", days_ago=0, update="upToDate", edr="enabled", os_name="Windows", version=None, virus="19.123.00"):
    item = {
        "agentGuid": guid, "endpointName": name, "type": "server" if "Server" in manager else "desktop",
        "osName": os_name, "osVersion": "10", "ipAddresses": ["10.0.0.%d" % (len(guid) % 250)],
        "edrSensor": {"status": edr, "version": "1.2.0.3456", "connectivity": "connected", "lastConnectedDateTime": iso(NOW - timedelta(days=days_ago))},
    }
    item["agentVersionStatus"] = "updateRecommended" if version and version.startswith("14.0.129") else "latestVersion"
    if manager:
        item["eppAgent"] = {"protectionManager": manager, "policyName": "Default", "status": status,
                            "lastConnectedDateTime": iso(NOW - timedelta(days=days_ago)),
                            "componentUpdateStatus": "onSchedule",
                            "componentVersion": "outdatedVersion" if update == "outdated" else "latestVersion",
                            "version": version or ("20.0.1.7000" if "Server" in manager else "14.0.13100.1"),
                            "components": [{"name": "Virus Pattern", "version": virus},
                                           {"name": "Digital Signature Pattern", "version": "1.987.00"}]}
    return item


ENDPOINTS = [
    endpoint("g1", "BL-LAP-001", "Standard Endpoint Protection"),
    endpoint("g2", "BL-LAP-002", "Standard Endpoint Protection", status="off", days_ago=10, update="outdated",
             version="14.0.12900.1"),
    endpoint("g3", "BL-SRV-DB01", "Server & Workload Protection", os_name="Linux"),
    endpoint("g4", "BL-SRV-WEB01", "Server & Workload Protection"),
    endpoint("g5", "BL-KIOSK", ""),
    endpoint("g6", "BL-LAP-APEX", "APEX ONE", virus="19.121.00"),
    endpoint("g7", "BL-SRV-AND", "Server and Workload Protection Manager - 752139992548"),
    # unmanaged (discovered, no agent/sensor) — must not be counted
    {"agentGuid": "u1", "endpointName": "UNMANAGED-PC", "eppAgent": {}, "edrSensor": {}},
    {"agentGuid": "u2", "endpointName": "UNMANAGED-PC2"},
    # SWP connector-discovered VM with no agent installed (console does not count it)
    {"agentGuid": "u3", "endpointName": "EC2-NOAGENT", "agentVersionStatus": "unknownVersions",
     "eppAgent": {"protectionManager": "Server and Workload Protection Manager - 752139992548",
                  "version": "0.0.0.0", "status": "unknown", "componentVersion": "unknownVersions"}},
    {"agentGuid": "u4", "endpointName": "AZ-NOAGENT",
     "eppAgent": {"protectionManager": "Server and Workload Protection Manager - 752139992548"}},
]
