#!/usr/bin/bash
set -u

problem=0

if command -v tailscale >/dev/null 2>&1; then
    echo "tailscale: $(tailscale version | head -n1)"
else
    echo "tailscale: NOT INSTALLED"
    problem=1
fi

if systemctl is-enabled --quiet tailscaled.service; then
    echo "tailscaled enabled: yes"
else
    echo "tailscaled enabled: no"
    problem=1
fi

if systemctl is-active --quiet tailscaled.service; then
    echo "tailscaled active: yes"
else
    echo "tailscaled active: no"
    problem=1
fi

state_path=/var/lib/tailscale/tailscaled.state
if sudo test -s "$state_path"; then
    echo "persistent state: $state_path exists and is non-empty"
else
    echo "persistent state: MISSING or empty at $state_path"
    echo "Check 'systemctl cat tailscaled' for a custom --state path or --state=mem:."
    problem=1
fi

if tailscale status --json >/dev/null 2>&1; then
    echo "local API access: yes"
else
    echo "local API access: no"
    echo "Fix with: sudo tailscale set --operator=$USER"
    problem=1
fi

if ((problem)); then
    echo
    echo "One or more checks need attention. No settings were changed."
    exit 1
fi

echo
echo "The daemon, persistent state, and local API access look healthy."

