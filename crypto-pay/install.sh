#!/usr/bin/env bash
# install.sh — One-command installer for crypto-pay on Ubuntu 20.04
# Usage: sudo bash install.sh
set -euo pipefail

INSTALL_DIR="/opt/crypto-pay"
DATA_DIR="/var/lib/crypto-pay"
LOG_DIR="/var/log/crypto-pay"
SERVICE_USER="www-data"

echo "=== crypto-pay installer ==="

# ----- System dependencies -----
apt-get update -qq
apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv \
    libssl-dev libffi-dev \
    2>/dev/null

# ----- Create directories -----
mkdir -p "${INSTALL_DIR}" "${DATA_DIR}" "${LOG_DIR}"

# ----- Copy application files -----
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cp -r "${SCRIPT_DIR}/server"    "${INSTALL_DIR}/"
cp -r "${SCRIPT_DIR}/frontend"  "${INSTALL_DIR}/"
cp -r "${SCRIPT_DIR}/templates" "${INSTALL_DIR}/"
cp -r "${SCRIPT_DIR}/database"  "${INSTALL_DIR}/"
cp "${SCRIPT_DIR}/config.yaml"  "${INSTALL_DIR}/"
cp "${SCRIPT_DIR}/requirements.txt" "${INSTALL_DIR}/"

# ----- .env -----
if [ ! -f "${INSTALL_DIR}/.env" ]; then
    cp "${SCRIPT_DIR}/.env.example" "${INSTALL_DIR}/.env"
    chmod 600 "${INSTALL_DIR}/.env"
    echo "[!] Copied .env.example to ${INSTALL_DIR}/.env — edit it before starting."
fi

# ----- Python virtualenv -----
python3 -m venv "${INSTALL_DIR}/venv"
"${INSTALL_DIR}/venv/bin/pip" install --quiet --upgrade pip
"${INSTALL_DIR}/venv/bin/pip" install --quiet -r "${INSTALL_DIR}/requirements.txt"

# ----- Permissions -----
chown -R "${SERVICE_USER}:${SERVICE_USER}" "${INSTALL_DIR}" "${DATA_DIR}" "${LOG_DIR}"
chmod 750 "${INSTALL_DIR}/server"
chmod 640 "${INSTALL_DIR}/.env"

# ----- Systemd units -----
cp "${SCRIPT_DIR}/systemd/crypto-pay.service"    /etc/systemd/system/
cp "${SCRIPT_DIR}/systemd/price-updater.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now crypto-pay price-updater

echo ""
echo "=== Installation complete ==="
echo "  Edit:    ${INSTALL_DIR}/.env"
echo "  Config:  ${INSTALL_DIR}/config.yaml"
echo "  Logs:    journalctl -u crypto-pay -f"
echo ""
echo "  Add wallet addresses:"
echo "    sudo python3 ${SCRIPT_DIR}/crypto_pay.py --add-wallet --coin BTC --address 'bc1q...'"
