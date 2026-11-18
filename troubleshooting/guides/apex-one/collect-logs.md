---
title: Collect Apex One agent and server logs
category: Logs & diagnostics
os: Windows
summary: Use Trend's Case Diagnostic Tool (CDT) to capture debug logs while you reproduce the issue.
scripts: apex-agent-health.ps1, apex-agent-connectivity.ps1
---
## 1. Get the Case Diagnostic Tool
The **Case Diagnostic Tool (CDT)** is Trend's official log collector for Apex One. Use the latest version from Trend support. An old copy may not support the current agent.

## 2. Capture debug logs
1. Copy CDT to the affected machine (agent or Apex One server) and run it **as administrator**.
2. Select the product (**Apex One Security Agent** or **Apex One Server**).
3. Start debug logging.
4. **Reproduce the problem** and note the time.
5. Stop debug logging and let CDT build the package.
6. Attach the zip it creates to the Trend case.

> **Note:** debug logging grows quickly. Stop it as soon as you have reproduced the problem.

## 3. Also send
- Output of **apex-agent-health** and **apex-agent-connectivity** (our scripts).
- Agent version (from the agent console or Programs and Features).
- Time the problem happened, with time zone.
