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
MANIFEST_FILE="${INSTALL_DIR}/install-manifest.txt"
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

if [[ -f "${INSTALL_DIR}/proxy.py" || -f "$MANIFEST_FILE" ]]; then
    HAS_PROXY=true
    info "Found runtime installation: ${INSTALL_DIR}"
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
        warn "  - ${INSTALL_DIR} runtime files"
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

is_managed_path() {
    local relative_path="$1"
    [[ "$relative_path" == "install-manifest.txt" ]] && return 0
    if [[ -f "$MANIFEST_FILE" ]]; then
        grep -Fqx -- "$relative_path" "$MANIFEST_FILE"
    else
        [[ "$relative_path" == "proxy.py" || "$relative_path" == "proxy.py.bak" ]]
    fi
}

if [[ -d "$INSTALL_DIR" ]]; then
    # Compare every file recursively with the managed-file manifest.
    EXTRA_FILES=()
    while IFS= read -r -d '' file; do
        RELATIVE_PATH="${file#${INSTALL_DIR}/}"
        if ! is_managed_path "$RELATIVE_PATH"; then
            EXTRA_FILES+=("$RELATIVE_PATH")
        fi
    done < <(find "$INSTALL_DIR" -type f -print0)

    if [[ ${#EXTRA_FILES[@]} -gt 0 ]]; then
        warn "Unexpected files found in ${INSTALL_DIR}:"
        for f in "${EXTRA_FILES[@]}"; do
            warn "  - ${f}"
        done
        echo ""
        REMOVE_DIR_FULLY=false
        info "Only files listed in install-manifest.txt will be removed."
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
        echo "  [ ] managed runtime files in ${INSTALL_DIR}/"
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

# --- Managed runtime files ---
if [[ -d "$INSTALL_DIR" ]]; then
    RUNTIME_DELETE_FAILED=false
    if [[ -f "$MANIFEST_FILE" ]]; then
        while IFS= read -r MANAGED_PATH || [[ -n "$MANAGED_PATH" ]]; do
            [[ -z "$MANAGED_PATH" || "$MANAGED_PATH" == \#* ]] && continue
            if [[ "$MANAGED_PATH" == /* || "$MANAGED_PATH" == *".."* ]]; then
                warn "Skipping unsafe manifest path: ${MANAGED_PATH}"
                RUNTIME_DELETE_FAILED=true
                continue
            fi
            if [[ -f "${INSTALL_DIR}/${MANAGED_PATH}" ]]; then
                rm -f -- "${INSTALL_DIR}/${MANAGED_PATH}" || RUNTIME_DELETE_FAILED=true
            fi
        done < "$MANIFEST_FILE"
        rm -f -- "$MANIFEST_FILE" || RUNTIME_DELETE_FAILED=true
    else
        # Legacy single-file installation.
        rm -f -- "${INSTALL_DIR}/proxy.py" || RUNTIME_DELETE_FAILED=true
        rm -f "${INSTALL_DIR}/proxy.py.bak" 2>/dev/null || true
    fi

    find "$INSTALL_DIR" -depth -type d -empty -delete 2>/dev/null || true
    if [[ "$RUNTIME_DELETE_FAILED" == false ]]; then
        DELETED_PROXY=true
        info "Removed managed runtime files from ${INSTALL_DIR}/"
    else
        error "Failed to remove one or more managed runtime files."
        HAS_DELETE_FAILURE=true
    fi
    if [[ -d "$INSTALL_DIR" ]]; then
        SKIPPED_INSTALL_DIR=true
        info "Kept ${INSTALL_DIR}/ because unmanaged files remain."
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
            echo -e "  ${GREEN}✓${NC} managed runtime files"
            echo -e "  ${YELLOW}!${NC} ${INSTALL_DIR}/ (kept - unexpected files present)"
        else
            echo -e "  ${GREEN}✓${NC} ${INSTALL_DIR}/"
        fi
    else
        echo -e "  ${RED}✗${NC} managed runtime files (failed)"
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
