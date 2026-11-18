---
title: Force uninstall the Deep Security Agent on Windows
category: Uninstall
os: Windows
summary: Turn off self-protection, reset the agent, uninstall it, and clean up by hand only if the normal uninstall fails.
scripts: ds-agent-health-windows.ps1
---
Applies to the Deep Security Agent used by Deep Security, Cloud One Workload Security and Server & Workload Protection.

> **Before you start:** agree a reboot window with the client and make sure the server has a backup or snapshot. Note the agent version (Programs and Features) so you can quote it in a Trend case.

## 1. Try the normal way first
1. In the console, open the computer and run **Actions > Deactivate** (skip if the agent is offline).
2. On the server, open **Programs and Features**, select **Trend Micro Deep Security Agent** and click **Uninstall**.
3. If it finishes, reboot and go to step 5 to confirm.

## 2. Turn off agent self-protection
If the uninstall is blocked ("you do not have permission" or the button is greyed out), self-protection is on.

- **From the console:** open the computer (or its policy) > **Settings > General > Agent Self Protection** and set *Prevent local end-users from uninstalling, stopping, or otherwise modifying the Agent* to **No**. Send the policy and wait for the agent to pick it up.
- **On the server** (the agent is offline), run in an admin Command Prompt with the self-protection password:

```
cd "C:\Program Files\Trend Micro\Deep Security Agent"
dsa_control.cmd --selfprotect=0 -p <password>
```

## 3. Reset the agent
This clears the agent's link to the manager so it can be removed cleanly.

```
cd "C:\Program Files\Trend Micro\Deep Security Agent"
dsa_control.cmd -r
```

## 4. Uninstall from the command line
Run in an admin PowerShell:

```
$app = Get-ItemProperty HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\* | Where-Object DisplayName -like "Trend Micro Deep Security Agent*"
$app | Select-Object DisplayName, DisplayVersion, PSChildName
msiexec /x $app.PSChildName /quiet /norestart /l*v C:\Windows\Temp\dsa_uninstall.log
```

If it fails, open `C:\Windows\Temp\dsa_uninstall.log` and search for `Return value 3`. The lines just above it show the cause. Attach the log to the Trend case.

## 5. Confirm it is gone
After a reboot:

```
sc query ds_agent
sc query Amsp
```

Both should say *The specified service does not exist*. Also remove the computer from the console if it is still listed.

## 6. Manual cleanup (last resort)
> **Warning:** only do this when the uninstaller keeps failing, and preferably with a Trend case open. Some driver names are shared with Apex One, so do not delete Trend keys you are unsure about.

1. Boot the server into **Safe Mode**.
2. Delete the agent services from an admin Command Prompt:

```
sc delete ds_agent
sc delete ds_monitor
sc delete ds_notifier
sc delete Amsp
```

3. Delete these folders if they exist:
   - `C:\Program Files\Trend Micro\Deep Security Agent`
   - `C:\ProgramData\Trend Micro\Deep Security Agent`
   - `C:\Program Files\Trend Micro\AMSP`
   - `C:\ProgramData\Trend Micro\AMSP`
4. Export, then delete, these registry keys:
   - `HKLM\SOFTWARE\TrendMicro\Deep Security Agent`
   - `HKLM\SOFTWARE\TrendMicro\AMSP`
5. Reboot normally and repeat step 5. If services or drivers are still listed, ask Trend support for the cleanup steps for that agent version.
