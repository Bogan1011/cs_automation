#!/usr/bin/env bash
# Deep Security / Server & Workload Protection agent health check for Linux (read-only).
# Checks the agent service, version and status, kernel modules, the connection to the manager,
# recent errors, disk space and time. Changes nothing on the machine.
# Usage: sudo bash ds-agent-health-linux.sh [manager-host]
# The report is saved to /tmp/dsa-health-<host>-<time>.txt to attach to a case.
MANAGER="${1:-}"
PORTS="443 4120 4122"
REPORT="/tmp/dsa-health-$(hostname)-$(date +%Y%m%d-%H%M).txt"
exec > >(tee "$REPORT") 2>&1
DSA=/opt/ds_agent
sec()  { echo; echo "=== $* ==="; }
ok()   { echo "[ OK ] $*"; }
bad()  { echo "[FAIL] $*"; }
warn() { echo "[WARN] $*"; }
[ "$(id -u)" -eq 0 ] || warn "Not running as root: some checks may be incomplete. Use sudo."

sec "Machine"
echo "$(hostname)  $(grep -m1 PRETTY_NAME /etc/os-release 2>/dev/null | cut -d= -f2 | tr -d '"')  kernel $(uname -r)  up $(uptime -p 2>/dev/null)"

sec "Agent service"
if systemctl list-unit-files 2>/dev/null | grep -q '^ds_agent'; then
  if systemctl is-active --quiet ds_agent; then ok "ds_agent running"; else bad "ds_agent is $(systemctl is-active ds_agent)"; fi
  systemctl status ds_agent --no-pager -n 0 2>/dev/null | sed -n '1,4p'
elif [ -x /etc/init.d/ds_agent ]; then /etc/init.d/ds_agent status
else bad "ds_agent service not found"; fi
pgrep -fa 'ds_agent|ds_am' | sed 's/^/  /' | head -8

sec "Agent version and status"
if [ ! -d "$DSA" ]; then bad "$DSA not found (agent not installed?)"
else
  (rpm -q ds_agent 2>/dev/null || dpkg -l ds-agent 2>/dev/null | awk '/^ii/{print $2" "$3}') | sed 's/^/  package: /'
  if [ -x "$DSA/dsa_query" ]; then
    STATUS=$("$DSA/dsa_query" -c GetAgentStatus 2>&1)
    echo "$STATUS" | grep -Ei 'AgentStatus|dsm|Activat|Heartbeat|Feature|Mode' | sed 's/^/  /'
    [ -z "$MANAGER" ] && MANAGER=$(echo "$STATUS" | grep -Eo 'https?://[^:/ ]+' | head -1 | sed 's#.*://##')
  else warn "dsa_query not found"; fi
fi

sec "Kernel support"
MODS=$(lsmod | awk '{print $1}' | grep -E '^(dsa_filter|dsa_filter_hook|bmhook|redirfs|gsch|tmhook)' | tr '\n' ' ')
if [ -n "$MODS" ]; then ok "Agent kernel modules loaded: $MODS"; else warn "No agent kernel modules loaded. Check that this kernel ($(uname -r)) is supported and that the kernel support package is installed."; fi
command -v getenforce >/dev/null && echo "  SELinux: $(getenforce)"
mokutil --sb-state 2>/dev/null | sed 's/^/  /'

sec "Connection to manager"
if [ -z "$MANAGER" ]; then warn "Manager host unknown. Run again with the host as the first argument."
else
  for p in $PORTS; do
    if timeout 5 bash -c "</dev/tcp/$MANAGER/$p" 2>/dev/null; then ok "$MANAGER:$p reachable"; else bad "$MANAGER:$p NOT reachable"; fi
  done
fi
env | grep -i '_proxy=' | sed 's/^/  proxy: /'

sec "Recent agent errors"
for f in /var/log/messages /var/log/syslog; do
  [ -f "$f" ] && grep -Ei 'ds_agent|dsa|amsp' "$f" | grep -Ei 'error|fail|denied' | tail -10
done
journalctl -u ds_agent --since "-3 days" -p err --no-pager 2>/dev/null | tail -10

sec "Disk and time"
df -h / /opt /var 2>/dev/null | awk 'NR==1 || !seen[$0]++'
timedatectl 2>/dev/null | grep -Ei 'synchronized|NTP|Local time' | sed 's/^/  /'

sec "Next steps"
echo "- Service stopped: sudo systemctl restart ds_agent. If it stops again, collect diagnostics with ds-collect-diagnostics-linux.sh."
echo "- Manager not reachable: check firewall, security groups and proxy for the ports above."
echo "- No kernel modules: check the agent's supported kernel list for $(uname -r)."
echo "- Offline in the console but connected here: send a heartbeat: sudo $DSA/dsa_control -m"
echo; echo "Report saved to $REPORT"
