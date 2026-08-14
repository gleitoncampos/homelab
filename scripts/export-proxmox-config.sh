#!/usr/bin/env bash
# =====================================================================
# Script de Exportação e Backup das Configurações do Proxmox VE
# =====================================================================

set -e

BACKUP_DIR="${1:-/var/backups/proxmox_config_exports}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/proxmox-config-${TIMESTAMP}.tar.gz"

echo "📦 Iniciando exportação de configurações do Proxmox VE..."
mkdir -p "${BACKUP_DIR}"

tar -czf "${BACKUP_FILE}" \
    /etc/pve/storage.cfg \
    /etc/pve/qemu-server/ \
    /etc/pve/lxc/ \
    /etc/pve/datacenter.cfg \
    /etc/pve/user.cfg \
    /etc/network/interfaces \
    /etc/hosts \
    /etc/hostname \
    2>/dev/null || true

echo "✅ Backup concluído com sucesso!"
echo "📁 Arquivo gerado: ${BACKUP_FILE}"
