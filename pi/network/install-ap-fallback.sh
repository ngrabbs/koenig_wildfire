#!/usr/bin/env bash
#
# install-ap-fallback.sh — Pi joins known wifi, falls back to its own AP.
#
# Creates (or replaces) a NetworkManager wifi profile called 'satnet' that
# runs the Pi as a WPA2 access point on 192.168.4.1/24 with NM's built-in
# DHCP + DNS (ipv4.method=shared spawns dnsmasq automatically).
#
# Behaviour after install:
#   - When any higher-priority wifi profile is reachable, NM uses it.
#   - When no other wifi can be brought up, NM falls back to satnet,
#     putting wlan0 into AP mode. The operator's laptop joins 'satnet'
#     (password 'cubesat1' by default) and reaches the Pi at
#     http://192.168.4.1:8000 (or http://koenig-pi.local:8000).
#
# Re-runnable: idempotent — deletes any existing 'satnet' profile and
# recreates from scratch.
#
# Usage (on the Pi):
#   sudo bash pi/network/install-ap-fallback.sh
#
# Override the defaults via env:
#   sudo SSID=koenig PASSWORD=longerthan8chars IFACE=wlan0 \
#        bash pi/network/install-ap-fallback.sh

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "must be run as root (sudo)" >&2
  exit 1
fi

SSID="${SSID:-satnet}"
PASSWORD="${PASSWORD:-cubesat1}"
IFACE="${IFACE:-wlan0}"
AP_IP="${AP_IP:-192.168.4.1}"
AP_CIDR="${AP_CIDR:-${AP_IP}/24}"

if [[ ${#PASSWORD} -lt 8 ]]; then
  echo "WPA2 password must be at least 8 characters (got ${#PASSWORD})" >&2
  exit 1
fi

if ! command -v nmcli >/dev/null; then
  echo "nmcli not found — NetworkManager is required" >&2
  exit 1
fi

if ! systemctl is-active --quiet NetworkManager; then
  echo "NetworkManager is not active" >&2
  exit 1
fi

if ! nmcli -t -f DEVICE,TYPE device | grep -q "^${IFACE}:wifi$"; then
  echo "interface '${IFACE}' is not a wifi device managed by NetworkManager" >&2
  exit 1
fi

echo "Removing any existing '${SSID}' profile..."
nmcli con delete "${SSID}" 2>/dev/null || true

echo "Creating AP profile '${SSID}' on ${IFACE} (${AP_CIDR})..."
nmcli con add type wifi ifname "${IFACE}" mode ap \
  con-name "${SSID}" ssid "${SSID}" autoconnect yes

# WPA2-PSK security
nmcli con modify "${SSID}" \
  wifi-sec.key-mgmt wpa-psk \
  wifi-sec.psk "${PASSWORD}" \
  802-11-wireless-security.proto rsn \
  802-11-wireless-security.group ccmp \
  802-11-wireless-security.pairwise ccmp

# Static IP for the AP. ipv4.method=shared makes NM spawn dnsmasq for
# DHCP + DNS to clients automatically.
nmcli con modify "${SSID}" \
  ipv4.method shared ipv4.address "${AP_CIDR}" \
  ipv6.method disabled

# Last-resort priority + keep retrying forever — NM tries higher-priority
# profiles first, falls back here when none are reachable.
nmcli con modify "${SSID}" \
  connection.autoconnect yes \
  connection.autoconnect-priority -999 \
  connection.autoconnect-retries 0

# Boost any existing client wifi above the AP so the fallback only fires
# when those genuinely can't connect. Read NAME + UUID together so the
# echo doesn't need a second nmcli call.
while IFS=: read -r name type uuid; do
  [[ "$type" == "802-11-wireless" && "$name" != "$SSID" ]] || continue
  priority=$(nmcli -t -f connection.autoconnect-priority con show "$uuid" | cut -d: -f2)
  if [[ -z "$priority" || "$priority" -le 0 ]]; then
    nmcli con modify "$uuid" connection.autoconnect-priority 10
    echo "  raised priority of '${name}' to 10 so it wins over ${SSID}"
  fi
done < <(nmcli -t -f NAME,TYPE,UUID con show)

cat <<EOF

----------------------------------------------------------------------
Installed.

SSID:     ${SSID}
Password: ${PASSWORD}
AP IP:    ${AP_IP}  (DHCP serves 192.168.4.x to clients)

The AP will activate automatically when no other wifi profile can be
brought up. With your current wifi reachable, it'll stay dormant.

To test the AP right now (will briefly drop other wifi):
  sudo nmcli con up   ${SSID}        # brings AP up
  sudo nmcli con down ${SSID}        # brings AP down, NM reconnects to wifi

To uninstall:
  sudo nmcli con delete ${SSID}
----------------------------------------------------------------------
EOF
