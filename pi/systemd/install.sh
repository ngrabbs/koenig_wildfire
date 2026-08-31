#!/usr/bin/env bash
#
# install.sh — install + enable the payload-daemon and payload-webui
# systemd services. Run from anywhere on the Pi:
#
#   sudo bash pi/systemd/install.sh
#
# Assumes the repo lives at /home/pi/code/koenig_wildfire. If it doesn't,
# edit WorkingDirectory= in the unit files before running.

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "must be run as root (sudo)" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

install -m 0644 "$SCRIPT_DIR/payload-daemon.service" /etc/systemd/system/
install -m 0644 "$SCRIPT_DIR/payload-webui.service"  /etc/systemd/system/

systemctl daemon-reload
systemctl enable  payload-daemon.service payload-webui.service
systemctl restart payload-daemon.service payload-webui.service

sleep 1
systemctl --no-pager status payload-daemon.service payload-webui.service | head -40

cat <<EOF

----------------------------------------------------------------------
Installed. Browse to:

  http://$(hostname).local:8000
  http://$(hostname -I | awk '{print $1}'):8000

Logs:  journalctl -u payload-daemon -u payload-webui -f
----------------------------------------------------------------------
EOF
