---
title: Force uninstall the Deep Security Agent on Linux
category: Uninstall
os: Linux
summary: Reset the agent, remove the package with rpm or dpkg, and clean up leftovers when the package scripts fail.
scripts: ds-agent-health-linux.sh
---
Applies to the Deep Security Agent used by Deep Security, Cloud One Workload Security and Server & Workload Protection.

> **Before you start:** agree a reboot window. Kernel modules from the agent are only fully unloaded after a reboot. Note the version with `rpm -qa | grep ds_agent` or `dpkg -l | grep ds-agent`.

## 1. Deactivate and reset
1. In the console, open the computer and run **Actions > Deactivate** (skip if offline).
2. On the server:

```
sudo /opt/ds_agent/dsa_control -r
```

## 2. Remove the package
RHEL, CentOS, Rocky, Alma, Amazon Linux, SUSE:

```
sudo rpm -e ds_agent
```

Ubuntu and Debian:

```
sudo dpkg -r ds-agent || sudo dpkg -r ds_agent
```

## 3. Confirm it is gone

```
systemctl status ds_agent
rpm -qa | grep -i ds_agent     # or: dpkg -l | grep -i ds
lsmod | grep -E 'dsa_filter|bmhook|redirfs|gsch'
ls -d /opt/ds_agent /var/opt/ds_agent 2>/dev/null
```

The service, package and folders should be gone. Agent modules in `lsmod` disappear after a reboot.

## 4. When the package removal fails
> **Warning:** only use these when the normal removal keeps failing, and keep the error output for the Trend case.

1. Stop the agent:

```
sudo systemctl stop ds_agent
sudo systemctl disable ds_agent
```

2. Remove the package without running its scripts:

```
sudo rpm -e --noscripts ds_agent            # RHEL family
sudo dpkg --purge --force-all ds-agent       # Ubuntu / Debian
```

3. Remove what is left, then reboot:

```
sudo rm -rf /opt/ds_agent /var/opt/ds_agent
sudo systemctl daemon-reload
sudo reboot
```

4. After the reboot, run the checks in step 3 again.
