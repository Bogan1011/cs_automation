#!/usr/bin/env bash
# Creates the Deep Security Agent diagnostic package for Trend support (Linux).
# Runs the agent's own "dsa_control -d", which writes a zip of agent logs and settings. Changes no setting.
# Usage: sudo bash ds-collect-diagnostics-linux.sh
DSA=/opt/ds_agent
[ -x "$DSA/dsa_control" ] || { echo "$DSA/dsa_control not found"; exit 1; }
echo "Creating the diagnostic package. This can take a few minutes..."
"$DSA/dsa_control" -d
echo; echo "Newest packages:"
ls -t /var/opt/ds_agent/diag/*.zip 2>/dev/null | head -3
