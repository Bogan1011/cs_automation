#!/usr/bin/env bash
# Vision One endpoint agent (XDR sensor / Endpoint Basecamp) health check for Linux (read-only).
# Finds the Trend Micro services and processes, the install folders, tests the connection to the
# Vision One region (DNS, TCP, proxy, TLS inspection) and lists recent errors. Changes nothing.
# Usage: sudo bash xdr-sensor-health-linux.sh [region] [extra-host ...]
#   region: in, sg, us, eu, jp, au, mea (default in)
# The report is saved to /tmp/xdr-health-<host>-<time>.txt to attach to a case.
REGION="${1:-in}"; shift 2>/dev/null
REPORT="/tmp/xdr-health-$(hostname)-$(date +%Y%m%d-%H%M).txt"
exec > >(tee "$REPORT") 2>&1
sec()  { echo; echo "=== $* ==="; }
ok()   { echo "[ OK ] $*"; }
bad()  { echo "[FAIL] $*"; }
warn() { echo "[WARN] $*"; }
[ "$(id -u)" -eq 0 ] || warn "Not running as root: some checks may be incomplete. Use sudo."

sec "Machine"
echo "$(hostname)  $(grep -m1 PRETTY_NAME /etc/os-release 2>/dev/null | cut -d= -f2 | tr -d '"')  kernel $(uname -r)  arch $(uname -m)"

sec "Trend Micro services"
UNITS=$(systemctl list-units --all --type=service --no-legend 2>/dev/null | awk '{print $1}' | grep -Ei 'tmxbc|basecamp|trend|tm_|ds_agent' )
if [ -z "$UNITS" ]; then bad "No Trend Micro services found"; fi
for u in $UNITS; do
  st=$(systemctl is-active "$u"); if [ "$st" = active ]; then ok "$u running"; else bad "$u is $st"; fi
done
pgrep -fa -i 'basecamp|tmxbc|xbc|trend' | grep -v pgrep | sed 's/^/  process: /' | head -10

sec "Install folders"
for d in /opt/TrendMicro /opt/trendmicro /opt/ds_agent; do [ -d "$d" ] && { echo "  $d"; ls "$d" 2>/dev/null | sed 's/^/    /'; }; done
(rpm -qa 2>/dev/null; dpkg -l 2>/dev/null | awk '/^ii/{print $2" "$3}') | grep -Ei 'trend|tmxbc|basecamp|ds.agent' | sed 's/^/  package: /'

sec "Connection to Vision One ($REGION)"
if [ "$REGION" = us ]; then SUF=""; else SUF=".$REGION"; fi
for h in "portal$SUF.xdr.trendmicro.com" "api$SUF.xdr.trendmicro.com" "$@"; do
  if ! getent hosts "$h" >/dev/null; then bad "$h does not resolve (DNS)"; continue; fi
  if ! timeout 5 bash -c "</dev/tcp/$h/443" 2>/dev/null; then bad "$h:443 NOT reachable"; continue; fi
  if command -v openssl >/dev/null; then
    ISSUER=$(echo | timeout 8 openssl s_client -connect "$h:443" -servername "$h" 2>/dev/null | openssl x509 -noout -issuer 2>/dev/null)
    if echo "$ISSUER" | grep -Eqi "DigiCert|Amazon|Trend|GlobalSign|Sectigo|Let's Encrypt|Microsoft"; then ok "$h:443 reachable, $ISSUER"
    elif [ -n "$ISSUER" ]; then warn "$h:443 reachable but $ISSUER. TLS inspection is likely; ask for a bypass for Trend hosts."
    else warn "$h:443 reachable but the TLS handshake failed"; fi
  else ok "$h:443 reachable (openssl missing, TLS not checked)"; fi
done
env | grep -i '_proxy=' | sed 's/^/  proxy: /'

sec "Time"
timedatectl 2>/dev/null | grep -Ei 'Local time|synchronized|NTP' | sed 's/^/  /'

sec "Recent errors"
journalctl --since "-3 days" -p err --no-pager 2>/dev/null | grep -Ei 'tmxbc|basecamp|trend' | tail -15
for f in /var/log/messages /var/log/syslog; do [ -f "$f" ] && grep -Ei 'tmxbc|basecamp|trend' "$f" | grep -Ei 'error|fail' | tail -10; done

sec "Disk"
df -h / /opt /var 2>/dev/null

sec "Next steps"
echo "- Host not reachable or TLS inspected: allow the Vision One hosts for this region, without TLS inspection."
echo "- Service stopped: sudo systemctl restart <service>. If it keeps stopping, collect logs and open a case."
echo "- Sensor not supported: check the kernel ($(uname -r)) against the supported list."
echo; echo "Report saved to $REPORT"
