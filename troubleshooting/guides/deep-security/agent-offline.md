---
title: Deep Security Agent shows Offline or Communication problem
category: Connectivity
os: Windows, Linux
summary: Check the service, the ports and the heartbeat, then reset and re-activate the agent if it still won't report in.
scripts: ds-agent-health-windows.ps1, ds-agent-health-linux.sh
---
## 1. Is the agent running?
Windows: `sc query ds_agent` should be **RUNNING**. Linux: `systemctl status ds_agent` should be **active (running)**. If it is stopped, start it and check whether it stays up.

## 2. Can it reach the manager?
| Manager | Port the agent needs |
|---|---|
| On-prem Deep Security Manager | 4120 (heartbeat), 4122 (relay updates) |
| Cloud One / Server & Workload Protection | 443 to the regional agent and relay hosts |

Windows test:

```
Test-NetConnection <manager-host> -Port 4120
```

Linux test:

```
curl -vk https://<manager-host>:4120/ 2>&1 | head -20
```

The **ds-agent-health** script tests these ports for you. If the port is blocked, the firewall or proxy team needs to open it. HTTPS inspection on these hosts also breaks the connection.

## 3. Send a heartbeat now

```
dsa_control.cmd -m             (Windows, in the agent folder)
sudo /opt/ds_agent/dsa_control -m   (Linux)
```

Refresh the computer in the console after a minute.

## 4. Reset and re-activate
Use this when the agent was moved, cloned from an image, or the manager certificate changed.

1. Reset: `dsa_control -r`
2. Activate again with the command from the console: **Support > Deployment Scripts** (Cloud One / Server & Workload) or `dsa_control -a dsm://<manager-host>:4120/` (on-prem).
3. Confirm the computer shows **Managed (Online)**.

## 5. Still offline
Collect a diagnostic package (see the guide) and open a case with the output of the health script.
