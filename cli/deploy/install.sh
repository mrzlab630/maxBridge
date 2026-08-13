#!/bin/bash
# maxBridge systemd installation script

set -euo pipefail

SERVICE_USER="maxbridge"
CONFIG_DIR="/etc/maxbridge"
STATE_DIR="/var/lib/maxbridge"

echo "=== maxBridge Installation ==="

# Create service user
if ! id "$SERVICE_USER" &>/dev/null; then
    useradd --system --shell /usr/sbin/nologin --home-dir "$STATE_DIR" "$SERVICE_USER"
    echo "Created user: $SERVICE_USER"
fi

# Create directories
mkdir -p "$CONFIG_DIR" "$STATE_DIR/data"
chown "$SERVICE_USER:$SERVICE_USER" "$STATE_DIR" "$STATE_DIR/data"
chmod 700 "$STATE_DIR/data"

# Copy config if not exists
if [ ! -f "$CONFIG_DIR/config.yaml" ]; then
    cp src/maxbridge/data/default.yaml "$CONFIG_DIR/config.yaml"
    chown "$SERVICE_USER:$SERVICE_USER" "$CONFIG_DIR/config.yaml"
    chmod 600 "$CONFIG_DIR/config.yaml"
    echo "Config created: $CONFIG_DIR/config.yaml"
fi

# Install package
python3 -m pip install .

# Install systemd unit
cp deploy/maxbridge.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable maxbridge

echo ""
echo "=== Installation complete ==="
echo "1. Edit config:    sudo nano $CONFIG_DIR/config.yaml"
echo "2. Authenticate:   sudo -u $SERVICE_USER maxbridge --auth-only -c $CONFIG_DIR/config.yaml"
echo "3. Start:          sudo systemctl start maxbridge"
echo "4. Status:         sudo systemctl status maxbridge"
echo "5. Logs:           sudo journalctl -u maxbridge -f"
