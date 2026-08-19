#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# Ollama Agent Proxy - Uninstallation Script
# ============================================================

SERVICE_NAME="ollama-agent-proxy"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
DROPIN_DIR="/etc/systemd/system/${SERVICE_NAME}.service.d"
DROPIN_FILE="${DROPIN_DIR}/override.conf"
INSTALL_DIR="/opt/ollama-agent-proxy"
_ORIGINAL_USER="${SUDO_USER:-}"
if [[ -z "$_ORIGINAL_USER" ]]; then
    _ORIGINAL_USER=""
fi
if [[ -n "$_ORIGINAL_USER" ]]; then
    _ORIGINAL_HOME="$(getent passwd "$_ORIGINAL_USER" 2>/dev/null | cut -d: -f6)" || true
    if [[ -n "$_ORIGINAL_HOME" ]]; then
        BACKUP_DIR="${_ORIGINAL_HOME}/ollama-agent-proxy-backup"
    else
        BACKUP_DIR="${HOME}/ollama-agent-proxy-backup"
    fi
else
    BACKUP_DIR="${HOME}/ollama-agent-proxy-backup"
fi

# Track deletion results
DELETED_SERVICE=false
DELETED_DROPIN=false
DELETED_PROXY=false
SKIPPED_INSTALL_DIR=false
HAS_DELETE_FAILURE=false
BACKUP_CREATED=false

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()    { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; }

# ============================================================
# Confirm prompt (y/n)
# ============================================================

confirm() {
    local message="$1"
    echo -n "${message} [y/N]: "
    read -r answer
    case "$answer" in
        [yY][eE][sS]|[yY]) return 0 ;;
        *) return 1 ;;
    esac
}

# ============================================================
# Phase 1: Prerequisite checks
# ============================================================

info "===== Phase 1: Prerequisites ====="

if [[ "$(id -u)" -ne 0 ]]; then
    error "This script must be run as root (use sudo)."
    exit 1
fi

# ============================================================
# Phase 2: Installation state check
# ============================================================

echo ""
info "===== Phase 2: Checking installation state ====="

HAS_SERVICE=false
HAS_DROPIN=false
HAS_PROXY=false

if [[ -f "$SERVICE_FILE" ]]; then
    HAS_SERVICE=true
    info "Found: ${SERVICE_FILE}"
fi

if [[ -d "$DROPIN_DIR" ]]; then
    HAS_DROPIN=true
    info "Found: ${DROPIN_DIR}/"
fi

if [[ -f "${INSTALL_DIR}/proxy.py" ]]; then
    HAS_PROXY=true
    info "Found: ${INSTALL_DIR}/proxy.py"
fi

# All not present -> nothing to uninstall
if [[ "$HAS_SERVICE" == false && "$HAS_DROPIN" == false && "$HAS_PROXY" == false ]]; then
    echo ""
    warn "Ollama Agent Proxy does not appear to be installed."
    exit 0
fi

# Partial state detected
PARTIAL_STATE=false
_ITEM_COUNT=0
[[ "$HAS_SERVICE" == true ]] && (( _ITEM_COUNT++ )) || true
[[ "$HAS_DROPIN" == true ]]  && (( _ITEM_COUNT++ )) || true
[[ "$HAS_PROXY" == true ]]   && (( _ITEM_COUNT++ )) || true
if [[ $_ITEM_COUNT -lt 3 ]]; then
    PARTIAL_STATE=true
fi

if [[ "$PARTIAL_STATE" == true ]]; then
    echo ""
    warn "Incomplete installation state detected. The following resources will be removed:"
    if [[ "$HAS_SERVICE" == true ]]; then
        warn "  - ${SERVICE_FILE}"
    fi
    if [[ "$HAS_DROPIN" == true ]]; then
        warn "  - ${DROPIN_DIR}/"
    fi
    if [[ "$HAS_PROXY" == true ]]; then
        warn "  - ${INSTALL_DIR}/proxy.py"
    fi
    echo ""
    if ! confirm "Continue with removal?"; then
        error "Uninstallation cancelled."
        exit 0
    fi
fi

# --- Service running check ---
if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
    echo ""
    warn "${SERVICE_NAME} is currently active. It will be stopped during uninstallation."
    if ! confirm "Continue with uninstallation?"; then
        error "Uninstallation cancelled."
        exit 0
    fi
fi

# ============================================================
# Phase 3: drop-in backup check
# ============================================================

echo ""
info "===== Phase 3: Backup check ====="

if [[ -f "$DROPIN_FILE" ]]; then
    echo ""
    info "Found override.conf:"
    echo "---"
    cat "$DROPIN_FILE"
    echo "---"
    echo ""
    if confirm "Backup this file to ${BACKUP_DIR}/override.conf?"; then
        mkdir -p "$BACKUP_DIR"
        cp -f "$DROPIN_FILE" "${BACKUP_DIR}/override.conf"
        info "Backed up -> ${BACKUP_DIR}/override.conf"
        BACKUP_CREATED=true
    else
        info "Skipping backup of override.conf"
    fi
fi

# ============================================================
# Phase 4: Install directory content check
# ============================================================

echo ""
info "===== Phase 4: Checking install directory ====="

REMOVE_DIR_FULLY=true

if [[ -d "$INSTALL_DIR" ]]; then
    # List all files in the directory (excluding . and ..)
    EXTRA_FILES=()
    while IFS= read -r -d '' file; do
        _BASENAME="$(basename -- "$file" 2>/dev/null)" || continue
        if [[ "$_BASENAME" != "proxy.py" && "$_BASENAME" != "proxy.py.bak" ]]; then
            EXTRA_FILES+=("$_BASENAME")
        fi
    done < <(find "$INSTALL_DIR" -maxdepth 1 -mindepth 1 -print0)

    if [[ ${#EXTRA_FILES[@]} -gt 0 ]]; then
        warn "Unexpected files found in ${INSTALL_DIR}:"
        for f in "${EXTRA_FILES[@]}"; do
            warn "  - ${f}"
        done
        echo ""
        REMOVE_DIR_FULLY=false
        if confirm "Remove the entire directory including these files?"; then
            REMOVE_DIR_FULLY=true
        else
            info "Will remove only proxy.py and keep the directory."
        fi
    fi
else
    info "${INSTALL_DIR} does not exist. Skipping content check."
fi

# ============================================================
# Phase 5: Final confirmation
# ============================================================

echo ""
info "===== Phase 5: Final confirmation ====="
echo ""
echo "The following resources will be removed:"
echo ""

if [[ "$HAS_SERVICE" == true ]]; then
    echo "  [ ] ${SERVICE_FILE}"
fi
if [[ "$HAS_DROPIN" == true ]]; then
    echo "  [ ] ${DROPIN_DIR}/"
fi
if [[ "$HAS_PROXY" == true ]]; then
    if [[ "$REMOVE_DIR_FULLY" == true ]]; then
        echo "  [ ] ${INSTALL_DIR}/"
    else
        echo "  [ ] ${INSTALL_DIR}/proxy.py"
        echo "      (directory will be kept due to unexpected files)"
    fi
fi

echo ""
if ! confirm "Proceed with uninstallation?"; then
    error "Uninstallation cancelled."
    exit 0
fi

# ============================================================
# Phase 6: Service stop
# ============================================================

echo ""
info "===== Phase 6: Stopping service ====="

if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
    if systemctl stop "$SERVICE_NAME" 2>/dev/null; then
        info "Stopped ${SERVICE_NAME}"
    else
        warn "Failed to stop ${SERVICE_NAME}. Continuing with removal."
    fi
else
    info "${SERVICE_NAME} is not active. Skipping stop."
fi

if systemctl is-enabled --quiet "$SERVICE_NAME" 2>/dev/null; then
    if systemctl disable "$SERVICE_NAME" 2>/dev/null; then
        info "Disabled ${SERVICE_NAME}"
    else
        warn "Failed to disable ${SERVICE_NAME}. Continuing with removal."
    fi
else
    info "${SERVICE_NAME} is not enabled. Skipping disable."
fi

# ============================================================
# Phase 7: File deletion
# ============================================================

echo ""
info "===== Phase 7: Removing files ====="

# --- Service file ---
if [[ -f "$SERVICE_FILE" ]]; then
    if rm -f "$SERVICE_FILE"; then
        DELETED_SERVICE=true
        info "Removed ${SERVICE_FILE}"
    else
        error "Failed to remove ${SERVICE_FILE}"
        HAS_DELETE_FAILURE=true
    fi
else
    info "${SERVICE_FILE} not found. Skipping."
fi

# --- drop-in directory ---
if [[ -d "$DROPIN_DIR" ]]; then
    if rm -rf "$DROPIN_DIR"; then
        DELETED_DROPIN=true
        info "Removed ${DROPIN_DIR}/"
    else
        error "Failed to remove ${DROPIN_DIR}/"
        HAS_DELETE_FAILURE=true
    fi
else
    info "${DROPIN_DIR}/ not found. Skipping."
fi

# --- Install directory / proxy.py ---
if [[ -d "$INSTALL_DIR" ]]; then
    if [[ "$REMOVE_DIR_FULLY" == true ]]; then
        if rm -rf "$INSTALL_DIR"; then
            DELETED_PROXY=true
            info "Removed ${INSTALL_DIR}/"
        else
            error "Failed to remove ${INSTALL_DIR}/"
            HAS_DELETE_FAILURE=true
        fi
    else
        # Remove only proxy.py (and .bak if present)
        if [[ -f "${INSTALL_DIR}/proxy.py" ]]; then
            if rm -f "${INSTALL_DIR}/proxy.py"; then
                DELETED_PROXY=true
                info "Removed ${INSTALL_DIR}/proxy.py"
            else
                error "Failed to remove ${INSTALL_DIR}/proxy.py"
                HAS_DELETE_FAILURE=true
            fi
        else
            info "${INSTALL_DIR}/proxy.py not found. Skipping."
        fi
        rm -f "${INSTALL_DIR}/proxy.py.bak" 2>/dev/null || true
        SKIPPED_INSTALL_DIR=true
    fi
else
    info "${INSTALL_DIR} not found. Skipping."
fi

# ============================================================
# Phase 8: Cleanup
# ============================================================

echo ""
info "===== Phase 8: Cleanup ====="

if systemctl daemon-reload 2>/dev/null; then
    info "Reloaded systemd daemon"
else
    warn "Failed to reload systemd daemon. This may not affect anything."
fi

# --- Final message ---
echo ""

if [[ "$HAS_DELETE_FAILURE" == true ]]; then
    warn "============================================"
    warn "Uninstallation completed with errors."
    warn "============================================"
else
    if [[ "$SKIPPED_INSTALL_DIR" == false && "$BACKUP_CREATED" == false ]]; then
        info "============================================"
        info "Uninstallation complete!"
        info "============================================"
    else
        info "============================================"
        info "Uninstallation complete (some resources remain)."
        info "============================================"
    fi
fi

echo ""
info "Removal results:"

if [[ "$HAS_SERVICE" == true ]]; then
    if [[ "$DELETED_SERVICE" == true ]]; then
        echo -e "  ${GREEN}✓${NC} ${SERVICE_FILE}"
    else
        echo -e "  ${RED}✗${NC} ${SERVICE_FILE} (failed)"
    fi
fi

if [[ "$HAS_DROPIN" == true ]]; then
    if [[ "$DELETED_DROPIN" == true ]]; then
        echo -e "  ${GREEN}✓${NC} ${DROPIN_DIR}/"
    else
        echo -e "  ${RED}✗${NC} ${DROPIN_DIR}/ (failed)"
    fi
fi

if [[ "$HAS_PROXY" == true ]]; then
    if [[ "$DELETED_PROXY" == true ]]; then
        if [[ "$SKIPPED_INSTALL_DIR" == true ]]; then
            echo -e "  ${GREEN}✓${NC} ${INSTALL_DIR}/proxy.py"
            echo -e "  ${YELLOW}!${NC} ${INSTALL_DIR}/ (kept - unexpected files present)"
        else
            echo -e "  ${GREEN}✓${NC} ${INSTALL_DIR}/"
        fi
    else
        echo -e "  ${RED}✗${NC} ${INSTALL_DIR}/proxy.py (failed)"
    fi
fi

if [[ "$BACKUP_CREATED" == true ]]; then
    echo ""
    info "Backup:"
    echo -e "  ~ ${BACKUP_DIR}/override.conf"
fi

echo ""
warn "Service logs are still present in journalctl."
warn "To remove them, run:"
warn "  journalctl --rotate && journalctl --vacuum-time=1s"

if [[ "$HAS_DELETE_FAILURE" == true ]]; then
    exit 1
fi

exit 0
