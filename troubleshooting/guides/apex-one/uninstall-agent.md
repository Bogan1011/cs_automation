---
title: Uninstall or force remove the Apex One Security Agent
category: Uninstall
os: Windows
summary: Remove the agent from the console or locally with the uninstall password, and use Trend's removal tool when that fails.
scripts: apex-agent-health.ps1
tools: scut
---
Applies to Apex One (on-prem) and Apex One as a Service / Standard Endpoint Protection.

> **Before you start:** you need the agent **uninstall password**. It is set in the Apex One console under the agent's privileges and other settings. Confirm with the client before removing protection from a machine.

## 1. From the console (agent is online)
1. Open **Agents > Agent Management**.
2. Select the endpoint and choose the **uninstall agent** task.
3. Wait for the endpoint to disappear from the tree, then reboot it.

## 2. On the endpoint
1. Open **Programs and Features**, select **Trend Micro Apex One Security Agent** and click **Uninstall**.
2. Enter the uninstall password when asked.
3. Reboot.

The agent's own uninstaller is `ntrmv.exe` in the agent folder (normally `C:\Program Files (x86)\Trend Micro\Security Agent`). Running it as administrator does the same as Programs and Features.

## 3. When the normal uninstall fails
Trend publishes a cleanup tool for the Security Agent (**SCUT**). Download the current version from Trend support for the agent's build, run it as administrator, and reboot. Don't reuse an old copy: it must match the agent version.

## 4. Confirm it is gone

```
Get-Service | Where-Object DisplayName -match "Apex One|OfficeScan|Trend Micro"
Test-Path "C:\Program Files (x86)\Trend Micro\Security Agent"
```

No Apex One services should be listed and the folder should be gone. Remove the endpoint from the console if it is still there.
