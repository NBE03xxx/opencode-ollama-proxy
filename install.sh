#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# OpenCode Ollama Proxy - Installation Script
# ============================================================

INSTALL_DIR="/opt/opencode-ollama-proxy"
SERVICE_NAME="opencode-ollama-proxy"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
DROPIN_DIR="/etc/systemd/system/${SERVICE_NAME}.service.d"
DROPIN_FILE="${DROPIN_DIR}/override.conf"
PROXY_URL="https://raw.githubusercontent.com/NBE03xxx/opencode-ollama-proxy/main/proxy.py"

# Track what we've created for rollback
CREATED_DIR=false
BACKUP_FILE=""
PROXY_COPIED=false
SERVICE_CREATED=false
DROPIN_CREATED=false
SERVICE_ENABLED=false

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

    if [[ "$SERVICE_ENABLED" == true ]]; then
        systemctl disable "$SERVICE_NAME" 2>/dev/null && info "Disabled service" || true
    fi

    if [[ "$SERVICE_CREATED" == true ]]; then
        rm -f "$SERVICE_FILE" && info "Removed service file" || true
    fi

    if [[ "$DROPIN_CREATED" == true ]]; then
        rm -rf "$DROPIN_DIR" && info "Removed drop-in directory" || true
    fi

    if [[ "$PROXY_COPIED" == true ]]; then
        if [[ -n "$BACKUP_FILE" && -f "$BACKUP_FILE" ]]; then
            mv -- "$BACKUP_FILE" "${INSTALL_DIR}/proxy.py" && info "Restored proxy.py from backup" || true
        else
            rm -f "${INSTALL_DIR}/proxy.py" && info "Removed copied proxy.py" || true
        fi
    fi

    if [[ -n "$BACKUP_FILE" && -f "$BACKUP_FILE" ]]; then
        rm -f "$BACKUP_FILE" || true
    fi

    if [[ "$CREATED_DIR" == true && -d "$INSTALL_DIR" ]]; then
        rmdir "$INSTALL_DIR" 2>/dev/null && info "Removed install directory" || true
    fi

    systemctl daemon-reload 2>/dev/null && info "Reloaded systemd daemon" || true

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
    if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
        warn "Stopping existing ${SERVICE_NAME}..."
        systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    fi
fi

# --- Install directory ---
if [[ ! -d "$INSTALL_DIR" ]]; then
    mkdir -p "$INSTALL_DIR"
    chmod 0755 "$INSTALL_DIR"
    CREATED_DIR=true
    info "Created ${INSTALL_DIR}"
else
    # Check for existing proxy.py
    if [[ -f "${INSTALL_DIR}/proxy.py" ]]; then
        BACKUP_FILE="${INSTALL_DIR}/proxy.py.bak"
        cp -f "${INSTALL_DIR}/proxy.py" "$BACKUP_FILE"
        info "Backed up existing file to ${BACKUP_FILE}"
    else
        rm -f "${INSTALL_DIR}/proxy.py.bak"
    fi
fi

# --- Download proxy.py from GitHub ---
info "Downloading proxy.py from GitHub..."
if ! curl -fsSL "$PROXY_URL" -o "${INSTALL_DIR}/proxy.py"; then
    error "Failed to download proxy.py from ${PROXY_URL}"
    rollback
    exit 1
fi
chmod 0755 "${INSTALL_DIR}/proxy.py"
PROXY_COPIED=true
info "Installed proxy.py -> ${INSTALL_DIR}/proxy.py"

# --- systemd service file ---
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=OpenCode Ollama Proxy
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
