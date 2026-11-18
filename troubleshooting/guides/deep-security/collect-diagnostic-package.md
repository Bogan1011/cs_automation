---
title: Collect a Deep Security diagnostic package
category: Logs & diagnostics
os: Windows, Linux
summary: Create the agent's diagnostic package from the console or locally with dsa_control -d, and the manager's package for on-prem Deep Security.
scripts: ds-collect-diagnostics-windows.ps1, ds-collect-diagnostics-linux.sh
tools: dsa-support-tool
---
Trend support almost always asks for this first. Collect it while the problem is happening, or right after reproducing it.

## From the console (agent is online)
1. Open the computer's details.
2. On the **Overview** page, open **Actions** and choose **Create Diagnostic Package** (under Support).
3. Download the zip when it is ready and attach it to the case.

## On the server (agent is offline or the console times out)
Windows, admin Command Prompt:

```
cd "C:\Program Files\Trend Micro\Deep Security Agent"
dsa_control.cmd -d
```

The zip is written to `C:\ProgramData\Trend Micro\Deep Security Agent\diag`.

Linux:

```
sudo /opt/ds_agent/dsa_control -d
ls -lh /var/opt/ds_agent/diag/
```

Our **ds-collect-diagnostics** scripts run the same command and tell you where the file is.

## Manager package (on-prem Deep Security only)
In the Deep Security Manager console go to **Administration > System Information** and click **Create Diagnostic Package**. Cloud One and Server & Workload Protection managers are run by Trend, so this step isn't needed there.

## What to send with it
- Agent version and OS version.
- Time the problem happened (with time zone).
- Screenshot of the error or event in the console.
- Output of our **ds-agent-health** script.
