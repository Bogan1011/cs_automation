---
title: Apex One agent offline or not getting updates
category: Connectivity
os: Windows
summary: Check the agent services, the server address and port the agent uses, and the network path to the server.
scripts: apex-agent-health.ps1, apex-agent-connectivity.ps1
tools: cdt, easyfix-cert
---
## 1. Services
Run **apex-agent-health**. These should be running: Apex One NT RealTime Scan (ntrtscan), Apex One NT Listener (tmlisten) and Apex One Common Client Solution Framework (TmCCSF). Start any that are stopped and check that they stay up.

## 2. Which server is the agent pointing at?
**apex-agent-connectivity** reads the server name and port from the agent's settings and tests them. If the server name is old or wrong, the agent needs to be moved to the right server from the console or reinstalled.

## 3. Network path
- The agent talks to the server over HTTP or HTTPS on the server port shown by the script.
- The server also connects back to the agent on the agent's listening port. A host firewall on the endpoint can block that, so the agent shows offline while it still updates.
- For Apex One as a Service, the agent needs HTTPS (443) to Trend's cloud hosts, and HTTPS inspection must be bypassed for them.

## 4. Still offline
Collect logs with CDT (see that guide) while reproducing, and open a case with the script output.
