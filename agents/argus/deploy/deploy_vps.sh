#!/usr/bin/env bash
set -euo pipefail

# ARGUS VPS Bootstrap Script
# Creates the argus system user, installs dependencies, and enables the service.

INSTALL_DIR="/opt/argus"
LOG_DIR="/var/log/argus"
ARGUS_USER="argus"

echo "=== ARGUS VPS Deployment ==="

# 1. Create system user (no login shell, no home directory)
if ! id "$ARGUS_USER" &>/dev/null; then
    echo "Creating system user: $ARGUS_USER"
    sudo useradd --system --no-create-home --shell /usr/sbin/nologin "$ARGUS_USER"
else
    echo "User $ARGUS_USER already exists"
fi

# 2. Create directories
echo "Creating directories..."
sudo mkdir -p "$INSTALL_DIR" "$LOG_DIR"
sudo chown "$ARGUS_USER:$ARGUS_USER" "$LOG_DIR"
sudo chmod 700 "$LOG_DIR"

# 3. Copy ARGUS files
echo "Copying ARGUS package..."
sudo cp -r agents/argus/* "$INSTALL_DIR/"
sudo chown -R root:root "$INSTALL_DIR"
sudo chmod -R 755 "$INSTALL_DIR"

# 4. Create virtualenv and install deps
echo "Setting up Python environment..."
sudo python3 -m venv "$INSTALL_DIR/venv"
sudo "$INSTALL_DIR/venv/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"

# 5. Install systemd service
echo "Installing systemd service..."
sudo cp "$INSTALL_DIR/deploy/argus.service" /etc/systemd/system/argus.service
sudo systemctl daemon-reload
sudo systemctl enable argus.service

echo ""
echo "=== Deployment complete ==="
echo "  Start:   sudo systemctl start argus"
echo "  Status:  sudo systemctl status argus"
echo "  Logs:    sudo journalctl -u argus -f"
echo "  Audit:   tail -f $LOG_DIR/audit.jsonl"
echo "  Escalation: tail -f $LOG_DIR/escalation.jsonl"
echo ""
echo "  NOTE: Update $INSTALL_DIR/config/domains.yaml with correct vault paths for this host."
