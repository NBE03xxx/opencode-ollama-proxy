#!/bin/sh
# ==============================================================================
# OpenCode Ollama Proxy Installer
# ==============================================================================
# This script installs the OpenCode Ollama Proxy as a systemd service.
# It is idempotent and can be safely re-run for updates.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/NBE03xxx/opencode-ollama-proxy/main/install.sh | sh
# ==============================================================================

set -e

# ------------------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------------------

REPO_OWNER="NBE03xxx"
REPO_NAME="opencode-ollama-proxy"
BRANCH="main"
RAW_BASE="https://raw.githubusercontent.com/${REPO_OWNER}/${REPO_NAME}/${BRANCH}"
GITHUB_URL="https://github.com/${REPO_OWNER}/${REPO_NAME}"

INSTALL_DIR="/opt/opencode-qwen-proxy"
SERVICE_NAME="opencode-ollama-proxy"
UNIT_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
OVERRIDE_DIR="/etc/systemd/system/${SERVICE_NAME}.service.d"
OVERRIDE_FILE="${OVERRIDE_DIR}/override.conf"

DEFAULT_OLLAMA_HOST="http://127.0.0.1:11434"
DEFAULT_LISTEN_HOST="0.0.0.0"
DEFAULT_LISTEN_PORT="8000"
DEFAULT_MODEL="qwen3.6:27b-Q6"

# ------------------------------------------------------------------------------
# Colors (if terminal supports it)
# ------------------------------------------------------------------------------

if command -v tput >/dev/null 2>&1 && [ "$(tput colors 2>/dev/null || echo 0)" -ge 8 ] 2>/dev/null; then
  RED="$(tput setaf 1)"
  GREEN="$(tput setaf 2)"
  YELLOW="$(tput setaf 3)"
  BLUE="$(tput setaf 4)"
  BOLD="$(tput bold)"
  RESET="$(tput sgr0)"
else
  RED=""
  GREEN=""
  YELLOW=""
  BLUE=""
  BOLD=""
  RESET=""
fi

# ------------------------------------------------------------------------------
# Logging helpers
# ------------------------------------------------------------------------------

info() {
  printf '%s[INFO]%s %s\n' "$BLUE" "$RESET" "$*"
}

warn() {
  printf '%s[WARN]%s %s\n' "$YELLOW" "$RESET" "$*" >&2
}

error() {
  printf '%s[ERROR]%s %s\n' "$RED" "$RESET" "$*" >&2
}

success() {
  printf '%s[OK]%s %s\n' "$GREEN" "$RESET" "$*"
}

section() {
  printf '\n%s=== %s ===%s\n' "$BOLD" "$*" "$RESET"
}

# ------------------------------------------------------------------------------
# Sudo helper: runs as root if needed, but avoids redundant sudo when already root
# ------------------------------------------------------------------------------

run_sudo() {
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
  else
    sudo -- "$@"
  fi
}

check_sudo() {
  if [ "$(id -u)" -eq 0 ]; then
    return 0
  fi

  if ! command -v sudo >/dev/null 2>&1; then
    error "This script requires root privileges."
    error "'sudo' is not installed on this system."
    echo ""
    echo "Please either:"
    echo "  1. Install sudo:  sudo apt install sudo   (Debian/Ubuntu)"
    echo "                     sudo yum install sudo    (RHEL/CentOS)"
    echo "  2. Or run the installer as root."
    return 1
  fi

  # Verify sudo works non-interactively where possible
  if ! sudo -n true 2>/dev/null; then
    info "This script will prompt for your password via sudo when needed."
  fi

  return 0
}

# ------------------------------------------------------------------------------
# Dependency checks
# ------------------------------------------------------------------------------

check_dependencies() {
  section "Checking dependencies"

  missing=""

  # curl
  if command -v curl >/dev/null 2>&1; then
    success "curl is available"
  else
    error "curl is not installed."
    missing="${missing}curl "
  fi

  # python3
  if command -v python3 >/dev/null 2>&1; then
    PYVER="$(python3 --version 2>&1)"
    success "${PYVER}"
  else
    error "python3 is not installed."
    missing="${missing}python3 "
  fi

  # systemctl
  if command -v systemctl >/dev/null 2>&1; then
    success "systemctl is available"
  else
    error "systemctl is not available. This script requires systemd."
    missing="${missing}systemd "
  fi

  # Check for wget as fallback (curl is mandatory though, so this is informational)
  if [ -n "$missing" ]; then
    echo ""
    error "Missing required dependencies: ${missing}"
    echo ""
    case "${missing}" in
      *curl*)
        info "Install curl:"
        echo "  Debian/Ubuntu: sudo apt update && sudo apt install curl"
        echo "  RHEL/CentOS:   sudo yum install curl"
        ;;
    esac
    case "${missing}" in
      *python3*)
        info "Install python3:"
        echo "  Debian/Ubuntu: sudo apt update && sudo apt install python3"
        echo "  RHEL/CentOS:   sudo yum install python3"
        ;;
    esac
    case "${missing}" in
      *systemd*)
        info "This script requires a systemd-based Linux distribution."
        ;;
    esac
    return 1
  fi

  return 0
}

# ------------------------------------------------------------------------------
# Check Ollama availability
# ------------------------------------------------------------------------------

check_ollama() {
  section "Checking Ollama"

  # Determine OLLAMA_HOST to check: use override if it already has one, else default
  CHECK_HOST="${DEFAULT_OLLAMA_HOST}"

  if [ -f "${OVERRIDE_FILE}" ]; then
    EXISTING_HOST="$(grep 'OLLAMA_HOST' "${OVERRIDE_FILE}" 2>/dev/null | head -1 | sed 's/.*OLLAMA_HOST="\([^"]*\)".*/\1/' || true)"
    if [ -n "$EXISTING_HOST" ]; then
      CHECK_HOST="$EXISTING_HOST"
    fi
  fi

  info "Checking Ollama at ${CHECK_HOST}..."

  # Try to connect to Ollama's API
  HTTP_CODE="$(curl -sf -o /dev/null -w '%{http_code}' "${CHECK_HOST}/api/tags" 2>/dev/null || echo "000")"

  if [ "$HTTP_CODE" = "200" ]; then
    success "Ollama is running and reachable at ${CHECK_HOST}"
    return 0
  elif [ "$HTTP_CODE" = "000" ]; then
    warn "Cannot connect to Ollama at ${CHECK_HOST}"
    echo ""
    echo "Possible causes:"
    echo "  - Ollama is not running. Start it with:  sudo systemctl start ollama"
    echo "  - OLLAMA_HOST is set to a different address."
    echo "  - Firewall or network issue blocking the connection."
    echo ""
    info "The installer will continue, but the proxy may not function until Ollama is available."
    return 0
  else
    warn "Ollama returned HTTP ${HTTP_CODE} at ${CHECK_HOST}"
    info "The installer will continue, but please verify your Ollama setup."
    return 0
  fi
}

# ------------------------------------------------------------------------------
# Check MODEL availability (informational only)
# ------------------------------------------------------------------------------

check_model() {
  section "Checking model"

  CHECK_HOST="${DEFAULT_OLLAMA_HOST}"

  if [ -f "${OVERRIDE_FILE}" ]; then
    EXISTING_HOST="$(grep 'OLLAMA_HOST' "${OVERRIDE_FILE}" 2>/dev/null | head -1 | sed 's/.*OLLAMA_HOST="\([^"]*\)".*/\1/' || true)"
    if [ -n "$EXISTING_HOST" ]; then
      CHECK_HOST="$EXISTING_HOST"
    fi
  fi

  info "Checking for model '${DEFAULT_MODEL}'..."

  # Query Ollama's /api/tags to list available models
  MODELS_JSON="$(curl -sf "${CHECK_HOST}/api/tags" 2>/dev/null || echo "{}")"

  if [ "$MODELS_JSON" = "{}" ]; then
    warn "Could not query Ollama for model list."
    info "Skipping model verification."
    return 0
  fi

  # Check if the model exists using python3 (more reliable JSON parsing)
  MODEL_FOUND="$(printf '%s' "$MODELS_JSON" | python3 -c '
import sys, json
try:
    data = json.load(sys.stdin)
    models = [m.get("name", "") for m in data.get("models", [])]
    if "'"$DEFAULT_MODEL"'" in models:
        print("yes")
    else:
        print("no")
except Exception:
    print("unknown")
' 2>/dev/null || echo "unknown")"

  if [ "$MODEL_FOUND" = "yes" ]; then
    success "Model '${DEFAULT_MODEL}' is available in Ollama."
  elif [ "$MODEL_FOUND" = "no" ]; then
    warn "Model '${DEFAULT_MODEL}' is not found in Ollama."
    echo ""
    echo "To download the model, run:"
    echo "  ollama pull ${DEFAULT_MODEL}"
    echo ""
    info "Note: The proxy does not enforce a specific model. You can specify"
    info "any available Ollama model in your OpenAI-compatible API requests."
  else
    warn "Could not verify model availability."
  fi

  return 0
}

# ------------------------------------------------------------------------------
# Check if port is in use
# ------------------------------------------------------------------------------

check_port() {
  PORT="$1"
  HOST="$2"

  section "Checking port ${PORT}"

  # Try ss first, then netstat as fallback
  PORT_IN_USE=""

  if command -v ss >/dev/null 2>&1; then
    PORT_IN_USE="$(ss -ltnp "sport = :${PORT}" 2>/dev/null | grep -v '^State' || true)"
  elif command -v netstat >/dev/null 2>&1; then
    PORT_IN_USE="$(netstat -ltnp 2>/dev/null | grep ":${PORT} " || true)"
  fi

  if [ -n "$PORT_IN_USE" ]; then
    warn "Port ${PORT} appears to be in use:"
    echo "$PORT_IN_USE"
    echo ""
    info "If this is the existing proxy, it will be restarted during installation."
    info "If another service is using this port, you may need to change LISTEN_PORT."
  else
    success "Port ${PORT} is available"
  fi

  return 0
}

# ------------------------------------------------------------------------------
# Download project files from GitHub
# ------------------------------------------------------------------------------

download_files() {
  section "Downloading project files"

  IS_UPDATE="no"

  if [ -d "$INSTALL_DIR" ]; then
    info "Existing installation found at ${INSTALL_DIR}"
    info "Updating existing installation..."
    IS_UPDATE="yes"
  fi

  # Create install directory if needed
  run_sudo mkdir -p "${INSTALL_DIR}"

  # Determine files to download from the repository root
  # We fetch proxy.py and README.md; LICENSE is informational
  FILES_TO_DOWNLOAD=""

  # Check each file availability on GitHub
  for FILE in "proxy.py" "README.md"; do
    HTTP_CODE="$(curl -sf -o /dev/null -w '%{http_code}' "${RAW_BASE}/${FILE}" 2>/dev/null || echo "000")"
    if [ "$HTTP_CODE" = "200" ]; then
      FILES_TO_DOWNLOAD="${FILES_TO_DOWNLOAD} ${FILE}"
    else
      error "Cannot download ${FILE} from GitHub (HTTP ${HTTP_CODE})"
      return 1
    fi
  done

  # Download each file
  for FILE in $FILES_TO_DOWNLOAD; do
    URL="${RAW_BASE}/${FILE}"
    DEST="${INSTALL_DIR}/${FILE}"

    info "Downloading ${FILE}..."

    TMPFILE="$(run_sudo mktemp "/tmp/opencode-install.XXXXXX" 2>/dev/null || run_sudo mktemp)"

    if curl -fsSL -o "$TMPFILE" "$URL"; then
      if [ -f "${DEST}" ]; then
        # Compare content to avoid unnecessary restarts
        if cmp -s "$TMPFILE" "${DEST}"; then
          info "${FILE} is already up-to-date."
          rm -f "$TMPFILE"
          continue
        else
          info "Updating ${FILE}..."
        fi
      fi
      run_sudo cp -f "$TMPFILE" "$DEST"
      success "Installed ${FILE}"
    else
      error "Failed to download ${FILE}"
      rm -f "$TMPFILE"
      return 1
    fi

    rm -f "$TMPFILE"
  done

  # Ensure proxy.py is executable
  run_sudo chmod +x "${INSTALL_DIR}/proxy.py"

  success "All project files installed to ${INSTALL_DIR}"
}

# ------------------------------------------------------------------------------
# Install systemd unit file
# ------------------------------------------------------------------------------

install_systemd_unit() {
  section "Installing systemd service"

  UNIT_CONTENT="[Unit]
Description=OpenCode Ollama Proxy
After=network-online.target ollama.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${INSTALL_DIR}
ExecStart=/usr/bin/python3 ${INSTALL_DIR}/proxy.py
Restart=always
RestartSec=3
Environment=PYTHONUNBUFFERED=1
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${SERVICE_NAME}

[Install]
WantedBy=multi-user.target"

  if [ -f "$UNIT_FILE" ]; then
    # Compare existing unit file with desired content
    EXISTING_HASH="$(md5sum "$UNIT_FILE" 2>/dev/null | cut -d' ' -f1 || echo "")"
    NEW_HASH="$(printf '%s\n' "$UNIT_CONTENT" | md5sum | cut -d' ' -f1)"

    if [ "$EXISTING_HASH" = "$NEW_HASH" ]; then
      success "Systemd unit file is already up-to-date."
    else
      info "Updating systemd unit file..."
      run_sudo printf '%s\n' "$UNIT_CONTENT" > "$UNIT_FILE"
      success "Updated systemd unit file."
    fi
  else
    info "Installing systemd unit file..."
    run_sudo printf '%s\n' "$UNIT_CONTENT" > "$UNIT_FILE"
    success "Installed systemd unit file."
  fi
}

# ------------------------------------------------------------------------------
# Setup drop-in override (preserve existing settings)
# ------------------------------------------------------------------------------

setup_override() {
  section "Configuring environment variables"

  if [ -f "${OVERRIDE_FILE}" ]; then
    success "Existing override.conf found. Preserving your configuration."
    info "To modify environment variables, run:"
    echo "  sudo systemctl edit ${SERVICE_NAME}"
    return 0
  fi

  # First-time install: create default override
  info "Creating default override.conf..."

  run_sudo mkdir -p "${OVERRIDE_DIR}"

  OVERRIDE_CONTENT="[Service]
Environment=\"OLLAMA_HOST=${DEFAULT_OLLAMA_HOST}\"
Environment=\"LISTEN_HOST=${DEFAULT_LISTEN_HOST}\"
Environment=\"LISTEN_PORT=${DEFAULT_LISTEN_PORT}\""

  run_sudo printf '%s\n' "$OVERRIDE_CONTENT" > "${OVERRIDE_FILE}"
  success "Created override.conf with default values."
  echo ""
  info "To customize these settings, edit the override file:"
  echo "  sudo systemctl edit ${SERVICE_NAME}"
}

# ------------------------------------------------------------------------------
# Enable and start service
# ------------------------------------------------------------------------------

enable_service() {
  section "Enabling and starting service"

  # Check if already active
  CURRENT_STATE="$(systemctl is-active "${SERVICE_NAME}.service" 2>/dev/null || echo "unknown")"

  if [ "$CURRENT_STATE" = "active" ]; then
    info "Service ${SERVICE_NAME} is already running."
  fi

  # Enable the service
  run_sudo systemctl enable "${SERVICE_NAME}.service" >/dev/null 2>&1 || true
  success "Service enabled for automatic startup."

  if [ "$CURRENT_STATE" = "active" ]; then
    info "Restarting existing service..."
    run_sudo systemctl restart "${SERVICE_NAME}.service"
  else
    info "Starting ${SERVICE_NAME}..."
    run_sudo systemctl start "${SERVICE_NAME}.service"
  fi

  # Brief pause to allow the service to start
  sleep 2

  # Verify
  NEW_STATE="$(systemctl is-active "${SERVICE_NAME}.service" 2>/dev/null || echo "unknown")"

  if [ "$NEW_STATE" = "active" ]; then
    success "Service ${SERVICE_NAME} is active."
    return 0
  else
    error "Service ${SERVICE_NAME} failed to start (state: ${NEW_STATE})."
    echo ""
    info "Check logs with:"
    echo "  journalctl -u ${SERVICE_NAME}.service -n 50 --no-pager"
    echo ""
    info "Common issues:"
    echo "  - Port ${DEFAULT_LISTEN_PORT} is already in use by another process"
    echo "  - Ollama is not running at the configured OLLAMA_HOST"
    echo "  - Python3 path is incorrect"
    return 1
  fi
}

# ------------------------------------------------------------------------------
# Verify proxy endpoint
# ------------------------------------------------------------------------------

verify_proxy() {
  section "Verifying proxy"

  LISTEN_PORT="${DEFAULT_LISTEN_PORT}"

  # Try to get actual port from override if it exists
  if [ -f "${OVERRIDE_FILE}" ]; then
    ACTUAL_PORT="$(grep '^Environment=.*LISTEN_PORT' "${OVERRIDE_FILE}" 2>/dev/null | sed 's/.*LISTEN_PORT=\([^"]*\)".*/\1/' || true)"
    if [ -n "$ACTUAL_PORT" ]; then
      LISTEN_PORT="$ACTUAL_PORT"
    fi
  fi

  info "Checking proxy endpoint at http://127.0.0.1:${LISTEN_PORT}/..."

  # The proxy only responds to POST /v1/chat/completions, so we check for a response
  HTTP_CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 \
    -X POST "http://127.0.0.1:${LISTEN_PORT}/v1/chat/completions" \
    -H 'Content-Type: application/json' \
    -d '{"model":"test","messages":[]}' 2>/dev/null || echo "000")"

  if [ "$HTTP_CODE" = "000" ]; then
    warn "Proxy is not responding on port ${LISTEN_PORT}"
    echo ""
    info "The service may need a moment to fully start. Try again in a few seconds."
    info "Or check logs: journalctl -u ${SERVICE_NAME}.service -n 20 --no-pager"
    return 1
  elif [ "$HTTP_CODE" = "502" ] || [ "$HTTP_CODE" = "400" ]; then
    success "Proxy is responding (HTTP ${HTTP_CODE} as expected for empty request)."
  else
    success "Proxy is responding on port ${LISTEN_PORT}"
  fi

  return 0
}

# ------------------------------------------------------------------------------
# Print service status summary
# ------------------------------------------------------------------------------

print_summary() {
  section "Installation Summary"

  echo ""
  echo "Service name   : ${SERVICE_NAME}"
  echo "Install dir    : ${INSTALL_DIR}"
  echo "Unit file      : ${UNIT_FILE}"
  echo "Override file  : ${OVERRIDE_FILE}"
  echo ""

  # Show effective environment from systemd (without exposing secrets)
  if [ -f "${OVERRIDE_FILE}" ]; then
    info "Current override.conf settings:"
    while IFS= read -r line; do
      case "$line" in
        Environment=*OLLAMA_HOST*)
          echo "  OLLAMA_HOST: (configured)"
          ;;
        Environment=*LISTEN_HOST*)
          VAL="$(printf '%s' "$line" | sed 's/.*LISTEN_HOST=\([^"]*\)".*/\1/')"
          echo "  LISTEN_HOST: ${VAL}"
          ;;
        Environment=*LISTEN_PORT*)
          VAL="$(printf '%s' "$line" | sed 's/.*LISTEN_PORT=\([^"]*\)".*/\1/')"
          echo "  LISTEN_PORT: ${VAL}"
          ;;
      esac
    done < "${OVERRIDE_FILE}"
  fi

  echo ""
  info "Service status:"
  systemctl is-active "${SERVICE_NAME}.service" 2>/dev/null && \
    success "Service is active" || \
    warn "Service is not active - check logs with: journalctl -u ${SERVICE_NAME}.service -f"

  echo ""
  info "To configure environment variables:"
  echo "  sudo systemctl edit ${SERVICE_NAME}"
  echo ""
  info "To view logs:"
  echo "  journalctl -u ${SERVICE_NAME} -f"
  echo ""
  info "Repository: ${GITHUB_URL}"
  echo ""

  # Show systemd cat output for verification
  info "Systemd unit configuration:"
  systemctl cat "${SERVICE_NAME}.service" 2>/dev/null | head -30 || true
  echo ""
}

# ------------------------------------------------------------------------------
# Main
# ------------------------------------------------------------------------------

main() {
  echo ""
  echo "============================================================"
  echo "  OpenCode Ollama Proxy Installer"
  echo "  Repository: ${GITHUB_URL}"
  echo "============================================================"
  echo ""

  # Step 1: Check sudo/root access
  if ! check_sudo; then
    exit 1
  fi

  # Step 2: Check dependencies
  if ! check_dependencies; then
    exit 1
  fi

  # Step 3: Check Ollama availability
  check_ollama || true

  # Step 4: Check model availability (informational)
  check_model || true

  # Step 5: Check port availability
  check_port "${DEFAULT_LISTEN_PORT}" "${DEFAULT_LISTEN_HOST}" || true

  # Step 6: Download/install files
  if ! download_files; then
    error "File installation failed."
    exit 1
  fi

  # Step 7: Install systemd unit (preserve existing override)
  install_systemd_unit

  # Step 8: Setup drop-in override
  setup_override

  # Step 9: daemon-reload and enable service
  info "Reloading systemd..."
  run_sudo systemctl daemon-reload

  if ! enable_service; then
    error "Service startup failed."
    exit 1
  fi

  # Step 10: Verify proxy is responding
  verify_proxy || true

  # Step 11: Print summary
  print_summary

  echo ""
  success "Installation complete!"
  echo ""
}

main "$@"
