#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# Ollama Agent Proxy - Installation Script
# ============================================================

INSTALL_DIR="/opt/ollama-agent-proxy"
SERVICE_NAME="ollama-agent-proxy"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
DROPIN_DIR="/etc/systemd/system/${SERVICE_NAME}.service.d"
DROPIN_FILE="${DROPIN_DIR}/override.conf"
REPOSITORY="NBE03xxx/ollama-agent-proxy"
INSTALL_REF="${OLLAMA_AGENT_PROXY_VERSION:-main}"
ARCHIVE_URL="https://github.com/${REPOSITORY}/archive/${INSTALL_REF}.tar.gz"
EXPECTED_SHA256="${OLLAMA_AGENT_PROXY_SHA256:-}"

# Track what we've created for rollback
STAGE_ROOT=""
BACKUP_DIR=""
FILES_INSTALLED=false
SERVICE_CREATED=false
DROPIN_CREATED=false
SERVICE_ENABLED=false
SERVICE_WAS_ACTIVE=false
SERVICE_WAS_ENABLED=false
SERVICE_BACKUP=""
DROPIN_BACKUP=""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()    { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; }

# ============================================================
# Rollback
# ============================================================

rollback() {
    echo ""
    warn "Rolling back changes..."

    if [[ "$SERVICE_ENABLED" == true && "$SERVICE_WAS_ENABLED" == false ]]; then
        systemctl disable "$SERVICE_NAME" 2>/dev/null && info "Disabled service" || true
    fi

    if [[ "$SERVICE_CREATED" == true ]]; then
        if [[ -n "$SERVICE_BACKUP" && -f "$SERVICE_BACKUP" ]]; then
            cp -f -- "$SERVICE_BACKUP" "$SERVICE_FILE" && info "Restored service file" || true
        else
            rm -f "$SERVICE_FILE" && info "Removed service file" || true
        fi
    fi

    if [[ "$DROPIN_CREATED" == true ]]; then
        rm -rf -- "$DROPIN_DIR" || true
        if [[ -n "$DROPIN_BACKUP" && -d "$DROPIN_BACKUP" ]]; then
            cp -a -- "$DROPIN_BACKUP" "$DROPIN_DIR" && info "Restored drop-in directory" || true
        else
            info "Removed drop-in directory"
        fi
    fi

    if [[ "$FILES_INSTALLED" == true && -d "$INSTALL_DIR" ]]; then
        rm -rf -- "$INSTALL_DIR" || true
    fi

    if [[ -n "$BACKUP_DIR" && -d "$BACKUP_DIR" ]]; then
        mv -- "$BACKUP_DIR" "$INSTALL_DIR" && info "Restored previous installation" || true
    fi

    if [[ -n "$STAGE_ROOT" && -d "$STAGE_ROOT" ]]; then
        rm -rf -- "$STAGE_ROOT" || true
    fi

    systemctl daemon-reload 2>/dev/null && info "Reloaded systemd daemon" || true
    if [[ "$SERVICE_WAS_ENABLED" == true ]]; then
        systemctl enable "$SERVICE_NAME" 2>/dev/null || true
    fi
    if [[ "$SERVICE_WAS_ACTIVE" == true ]]; then
        systemctl start "$SERVICE_NAME" 2>/dev/null && info "Restarted previous service" || true
    fi

    error "Installation cancelled. All changes have been rolled back."
}

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

if ! command -v python3 &>/dev/null; then
    error "python3 is not installed."
    exit 1
fi
info "Python3: $(python3 --version)"

if ! command -v curl &>/dev/null; then
    error "curl is not installed."
    exit 1
fi

if ! command -v tar &>/dev/null; then
    error "tar is not installed."
    exit 1
fi

if [[ -n "$EXPECTED_SHA256" ]] && ! command -v sha256sum &>/dev/null; then
    error "sha256sum is required when OLLAMA_AGENT_PROXY_SHA256 is set."
    exit 1
fi

if [[ "$INSTALL_REF" != "main" && -z "$EXPECTED_SHA256" ]]; then
    error "OLLAMA_AGENT_PROXY_SHA256 is required for a tagged or commit-pinned install."
    exit 1
fi

if ! command -v systemctl &>/dev/null; then
    error "systemd is not available."
    exit 1
fi
info "systemd: available"

# ============================================================
# Phase 2: Pre-flight checks
# ============================================================

echo ""
info "===== Phase 2: Pre-flight checks ====="

# --- Ollama check ---
OLLAMA_RUNNING=false
OLLAMA_LIST_OUTPUT=""

if command -v ollama &>/dev/null; then
    info "Checking Ollama service (via 'ollama list')..."
    if OLLAMA_LIST_OUTPUT=$(ollama list 2>&1); then
        OLLAMA_RUNNING=true
        info "Ollama is running"
    else
        warn "'ollama list' failed. Trying direct API fallback..."
    fi
fi

if [[ "$OLLAMA_RUNNING" == false ]]; then
    if curl -sf http://127.0.0.1:11434/api/tags -o /dev/null; then
        OLLAMA_RUNNING=true
        OLLAMA_LIST_OUTPUT=$(curl -s http://127.0.0.1:11434/api/tags)
        info "Ollama is running (detected via API)"
    else
        warn "Cannot connect to Ollama. It may not be running."
        if ! confirm "Continue installation anyway?"; then
            error "Installation cancelled."
            exit 1
        fi
    fi
fi

# --- Model check ---
if [[ "$OLLAMA_RUNNING" == true ]]; then
    if echo "$OLLAMA_LIST_OUTPUT" | grep -i 'qwen3\.6' &>/dev/null; then
        info "qwen3.6 model found"
    else
        warn "qwen3.6 model not found in Ollama."
        warn "It is recommended to install the model before continuing:"
        warn "  ollama pull qwen3.6:27b-Q6"
        if ! confirm "Continue installation without qwen3.6?"; then
            error "Installation cancelled."
            exit 1
        fi
    fi
fi

# --- Interactive configuration ---
echo ""
info "Configuration (press Enter to accept defaults)"

read -rp "  OLLAMA_HOST [http://127.0.0.1:11434]: " INPUT_OLLAMA_HOST
OLLAMA_HOST="${INPUT_OLLAMA_HOST:-http://127.0.0.1:11434}"

read -rp "  LISTEN_HOST [0.0.0.0]: " INPUT_LISTEN_HOST
LISTEN_HOST="${INPUT_LISTEN_HOST:-0.0.0.0}"

# --- Port conflict check (with loop) ---
PORT_PROMPT_FIRST=true
while true; do
    read -rp "  LISTEN_PORT [8000]: " INPUT_LISTEN_PORT

    # On re-prompt (port was in use), empty input means cancel
    if [[ "$PORT_PROMPT_FIRST" == false && -z "$INPUT_LISTEN_PORT" ]]; then
        error "Installation cancelled."
        exit 1
    fi

    PORT_PROMPT_FIRST=false
    LISTEN_PORT="${INPUT_LISTEN_PORT:-8000}"

    # Convert port to hex for /proc/net/tcp fallback
    PORT_HEX=$(printf '%04X' "$LISTEN_PORT" 2>/dev/null) || {
        warn "Invalid port number: $LISTEN_PORT"
        continue
    }

    PORT_IN_USE=false

    if command -v ss &>/dev/null; then
        if ss -tlnp | grep -qE ":${LISTEN_PORT}(\s|$)"; then
            PORT_IN_USE=true
        fi
    elif [[ -r /proc/net/tcp ]]; then
        if awk '{print $2}' /proc/net/tcp | grep -qi ":${PORT_HEX} 0A$"; then
            PORT_IN_USE=true
        fi
    fi

    if [[ "$PORT_IN_USE" == true ]]; then
        warn "Port ${LISTEN_PORT} is already in use. Please enter a different port."
    else
        break
    fi
done

info "Configuration:"
info "  OLLAMA_HOST = ${OLLAMA_HOST}"
info "  LISTEN_HOST = ${LISTEN_HOST}"
info "  LISTEN_PORT = ${LISTEN_PORT}"

# ============================================================
# Phase 3: File installation
# ============================================================

echo ""
info "===== Phase 3: Installing files ====="

# --- Check for existing installation ---
if [[ -f "$SERVICE_FILE" ]]; then
    warn "Existing service file found: ${SERVICE_FILE}"
    if ! confirm "Overwrite existing installation?"; then
        error "Installation cancelled."
        exit 1
    fi

    # Stop existing service before overwriting
    if systemctl is-enabled --quiet "$SERVICE_NAME" 2>/dev/null; then
        SERVICE_WAS_ENABLED=true
    fi
    if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
        SERVICE_WAS_ACTIVE=true
        warn "Stopping existing ${SERVICE_NAME}..."
        systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    fi
fi

# --- Download and verify one versioned archive ---
STAGE_ROOT=$(mktemp -d "/opt/.ollama-agent-proxy.XXXXXX")
SOURCE_DIR="${STAGE_ROOT}/source"
STAGED_INSTALL="${STAGE_ROOT}/install"
ARCHIVE_FILE="${STAGE_ROOT}/source.tar.gz"
mkdir -p "$SOURCE_DIR" "$STAGED_INSTALL"

info "Downloading ${REPOSITORY}@${INSTALL_REF}..."
if ! curl -fsSL "$ARCHIVE_URL" -o "$ARCHIVE_FILE"; then
    error "Failed to download ${ARCHIVE_URL}"
    rollback
    exit 1
fi

if [[ -n "$EXPECTED_SHA256" ]]; then
    ACTUAL_SHA256=$(sha256sum "$ARCHIVE_FILE" | awk '{print $1}')
    if [[ "$ACTUAL_SHA256" != "$EXPECTED_SHA256" ]]; then
        error "Archive SHA-256 mismatch."
        rollback
        exit 1
    fi
    info "Archive SHA-256 verified"
else
    warn "OLLAMA_AGENT_PROXY_SHA256 is not set; archive checksum verification was skipped."
fi

if ! tar -xzf "$ARCHIVE_FILE" --strip-components=1 -C "$SOURCE_DIR"; then
    error "Failed to extract release archive."
    rollback
    exit 1
fi

MANIFEST_SOURCE="${SOURCE_DIR}/install-manifest.txt"
if [[ ! -f "$MANIFEST_SOURCE" ]]; then
    error "install-manifest.txt is missing from the archive."
    rollback
    exit 1
fi

while IFS= read -r MANAGED_PATH || [[ -n "$MANAGED_PATH" ]]; do
    [[ -z "$MANAGED_PATH" || "$MANAGED_PATH" == \#* ]] && continue
    if [[ "$MANAGED_PATH" == /* || "$MANAGED_PATH" == *".."* ]]; then
        error "Unsafe path in install-manifest.txt: ${MANAGED_PATH}"
        rollback
        exit 1
    fi
    if [[ ! -f "${SOURCE_DIR}/${MANAGED_PATH}" ]]; then
        error "Required file is missing: ${MANAGED_PATH}"
        rollback
        exit 1
    fi
    mkdir -p "${STAGED_INSTALL}/$(dirname "$MANAGED_PATH")"
    cp -f -- "${SOURCE_DIR}/${MANAGED_PATH}" "${STAGED_INSTALL}/${MANAGED_PATH}"
done < "$MANIFEST_SOURCE"
cp -f -- "$MANIFEST_SOURCE" "${STAGED_INSTALL}/install-manifest.txt"
chmod 0755 "${STAGED_INSTALL}/proxy.py"

if ! PYTHONPYCACHEPREFIX="${STAGE_ROOT}/pycache" python3 -m py_compile \
    "${STAGED_INSTALL}/proxy.py" \
    "${STAGED_INSTALL}/common.py" \
    "${STAGED_INSTALL}/ollama.py" \
    "${STAGED_INSTALL}/agents/__init__.py" \
    "${STAGED_INSTALL}/agents/opencode.py" \
    "${STAGED_INSTALL}/agents/codex.py" \
    "${STAGED_INSTALL}/agents/claudecode.py"; then
    error "Python syntax validation failed."
    rollback
    exit 1
fi

if ! PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$STAGED_INSTALL" python3 -c 'import proxy, common, ollama, agents'; then
    error "Python import validation failed."
    rollback
    exit 1
fi

if [[ -d "$INSTALL_DIR" ]]; then
    BACKUP_DIR="${INSTALL_DIR}.backup.$$"
    mv -- "$INSTALL_DIR" "$BACKUP_DIR"
fi
mv -- "$STAGED_INSTALL" "$INSTALL_DIR"
FILES_INSTALLED=true
chmod 0755 "$INSTALL_DIR"
info "Installed runtime files -> ${INSTALL_DIR}"

# --- systemd service file ---
if [[ -f "$SERVICE_FILE" ]]; then
    SERVICE_BACKUP="${STAGE_ROOT}/service.backup"
    cp -f -- "$SERVICE_FILE" "$SERVICE_BACKUP"
fi
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Ollama Agent Proxy
After=network-online.target ollama.service
Wants=network-online.target

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=${INSTALL_DIR}
ExecStart=/usr/bin/python3 ${INSTALL_DIR}/proxy.py
Restart=always
RestartSec=3
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
SERVICE_CREATED=true
info "Created service file -> ${SERVICE_FILE}"

# --- drop-in override ---
if [[ -d "$DROPIN_DIR" ]]; then
    DROPIN_BACKUP="${STAGE_ROOT}/dropin.backup"
    cp -a -- "$DROPIN_DIR" "$DROPIN_BACKUP"
fi
mkdir -p "$DROPIN_DIR"
cat > "$DROPIN_FILE" <<EOF
[Service]
Environment="OLLAMA_HOST=${OLLAMA_HOST}"
Environment="LISTEN_HOST=${LISTEN_HOST}"
Environment="LISTEN_PORT=${LISTEN_PORT}"
EOF
DROPIN_CREATED=true
info "Created drop-in override -> ${DROPIN_FILE}"

# ============================================================
# Phase 4: Service activation
# ============================================================

echo ""
info "===== Phase 4: Activating service ====="

if ! systemctl daemon-reload; then
    error "Failed to reload systemd daemon."
    rollback
    exit 1
fi
info "Reloaded systemd daemon"

if ! systemctl enable "$SERVICE_NAME"; then
    error "Failed to enable ${SERVICE_NAME}."
    rollback
    exit 1
fi
SERVICE_ENABLED=true
info "Enabled ${SERVICE_NAME} (auto-start on boot)"

if systemctl start "$SERVICE_NAME"; then
    sleep 1
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        echo ""
        info "============================================"
        if [[ -n "$BACKUP_DIR" && -d "$BACKUP_DIR" ]]; then
            rm -rf -- "$BACKUP_DIR"
            BACKUP_DIR=""
        fi
        if [[ -n "$STAGE_ROOT" && -d "$STAGE_ROOT" ]]; then
            rm -rf -- "$STAGE_ROOT"
            STAGE_ROOT=""
        fi
        info "Installation complete!"
        info "============================================"
        echo ""
        info "Service: ${SERVICE_NAME}"
        info "Listen : http://${LISTEN_HOST}:${LISTEN_PORT}"
        info "Ollama : ${OLLAMA_HOST}/api/chat"
        echo ""
        info "Status : systemctl status ${SERVICE_NAME}"
        info "Logs   : journalctl -u ${SERVICE_NAME} -f"
        info "Stop   : sudo systemctl stop ${SERVICE_NAME}"
        info "============================================"
    else
        error "Service failed to start."
        echo ""
        warn "Check logs with:"
        warn "  journalctl -u ${SERVICE_NAME} -f"
        rollback
        exit 1
    fi
else
    error "Failed to start service."
    echo ""
    warn "Check logs with:"
    warn "  journalctl -u ${SERVICE_NAME} -f"
    rollback
    exit 1
fi
