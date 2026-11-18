---
title: Vision One endpoint sensor offline or not sending data
category: Connectivity
os: Windows, Linux
summary: Check the agent services, outbound 443 to the Vision One region, and TLS inspection, which is the most common cause.
scripts: xdr-sensor-health-windows.ps1, xdr-sensor-health-linux.sh
---
## 1. Run the health script
**xdr-sensor-health** lists the Trend services, the agent version, and tests HTTPS to the Vision One hosts for your region. Pass the region, for example `-Region in` for India or `-Region sg` for Singapore.

## 2. Services
All Trend Micro services (Endpoint Basecamp and the sensor services) should be **Running**. If one keeps stopping, note the time and check the Windows Application log for Trend errors.

## 3. Network
- The endpoint needs outbound **HTTPS (443)** to the Vision One hosts for the tenant's region.
- If the script says the certificate is from an unexpected issuer, a proxy or firewall is doing **TLS inspection**. Ask the network team to bypass inspection for Trend hosts. The sensor will not connect through it.
- If a proxy is required, make sure it is set for the agent, not only in the browser.

## 4. In the Vision One console
Open **Endpoint Inventory**, find the endpoint and check its status and last connection time. An endpoint that was re-imaged or cloned may appear twice. Remove the stale one.

## 5. Still offline
Open a case with the health script report attached, the endpoint name as shown in Endpoint Inventory, and the time it last connected.
