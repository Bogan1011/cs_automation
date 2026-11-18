---
title: Remove the Vision One endpoint agent and collect its logs
category: Uninstall
os: Windows, Linux
summary: Remove the agent from Endpoint Inventory in the console, and what to collect for Trend before you do.
scripts: xdr-sensor-health-windows.ps1, xdr-sensor-health-linux.sh
---
> **Before you start:** removing the agent removes detection and response for that endpoint. Confirm with the client in writing.

## 1. Collect logs first
If you are removing the agent because of a problem, collect evidence first so the case can continue:
1. Run **xdr-sensor-health** and save the report.
2. Ask Trend support for the current log collection steps for the agent version shown in the report, and attach the result to the case.

## 2. Remove from the console (recommended)
1. In Vision One, open **Endpoint Inventory**.
2. Select the endpoint and use the action to **uninstall** or **remove** the agent.
3. Wait for the endpoint to go, then confirm on the machine (step 4).

Removing it from the console is preferred because the agent is protected and a local uninstall may be blocked.

## 3. If the console can't reach the endpoint
Ask Trend support for the local removal tool for that agent version. Don't reuse an old copy.

## 4. Confirm it is gone
Windows:

```
Get-Service | Where-Object DisplayName -match "Trend Micro|Basecamp|Endpoint Sensor"
```

Linux:

```
systemctl list-units | grep -i -E "trend|basecamp|tmxbc"
```

No Trend agent services should be left.
