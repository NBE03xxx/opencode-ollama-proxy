#!/bin/sh
# ==============================================================================
# OpenCode Ollama Proxy Uninstaller
# ==============================================================================
# This script removes the OpenCode Ollama Proxy service and files.
# It does NOT remove Ollama, Ollama models, or user configurations.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/NBE03xxx/opencode-ollama-proxy/main/uninstall.sh | sh
# ==============================================================================

set -e

# ------------------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------------------

REPO_OWNER="NBE03xxx"
REPO_NAME="opencode-ollama-proxy"
BRANCH="main"
GITHUB_URL="https://github.com/${REPO_OWNER}/${REPO_NAME}"

INSTALL_DIR="/opt/opencode-qwen-proxy"
SERVICE_NAME="opencode-ollama-proxy"
UNIT_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
OVERRIDE_DIR="/etc/systemd/system/${SERVICE_NAME}.service.d"
OVERRIDE_FILE="${OVERRIDE_DIR}/override.conf"

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
# Sudo helper
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
    return 1
  fi

  return 0
}

# ------------------------------------------------------------------------------
# Main uninstall logic
# ------------------------------------------------------------------------------

main() {
  echo ""
  echo "============================================================"
  echo "  OpenCode Ollama Proxy Uninstaller"
  echo "============================================================"
  echo ""

  if ! check_sudo; then
    exit 1
  fi

  # ----------------------------------------------------------
  # Step 1: Stop the service
  # ----------------------------------------------------------
  section "Stopping service"

  if systemctl is-active "${SERVICE_NAME}.service" >/dev/null 2>&1; then
    info "Stopping ${SERVICE_NAME}..."
    run_sudo systemctl stop "${SERVICE_NAME}.service" && \
      success "Service stopped." || \
      warn "Failed to stop service (may already be stopped)."
  else
    info "Service ${SERVICE_NAME} is not running."
  fi

  # ----------------------------------------------------------
  # Step 2: Disable the service
  # ----------------------------------------------------------
  section "Disabling service"

  if systemctl is-enabled "${SERVICE_NAME}.service" >/dev/null 2>&1; then
    info "Disabling ${SERVICE_NAME}..."
    run_sudo systemctl disable "${SERVICE_NAME}.service" >/dev/null 2>&1 && \
      success "Service disabled." || \
      warn "Failed to disable service (may already be disabled)."
  else
    info "Service ${SERVICE_NAME} is not enabled."
  fi

  # ----------------------------------------------------------
  # Step 3: Remove systemd unit file
  # ----------------------------------------------------------
  section "Removing systemd unit file"

  if [ -f "$UNIT_FILE" ]; then
    info "Removing ${UNIT_FILE}..."
    run_sudo rm -f "$UNIT_FILE" && \
      success "Unit file removed." || \
      error "Failed to remove unit file."
  else
    info "Unit file does not exist: ${UNIT_FILE}"
  fi

  # ----------------------------------------------------------
  # Step 4: Handle drop-in override (careful!)
  # ----------------------------------------------------------
  section "Handling drop-in override"

  if [ -f "${OVERRIDE_FILE}" ]; then
    warn "Found existing override.conf:"
    echo ""
    cat "${OVERRIDE_FILE}" | sed 's/^/  /'
    echo ""
    warn "This file contains your custom environment variable settings."
    echo ""
    info "Options:"
    echo "  1. Keep the override directory (recommended if you want to preserve settings)"
    echo "  2. Remove only the override.conf file"
    echo "  3. Remove the entire override directory"
    echo ""

    # For automated execution, we default to keeping it safe: remove the override dir
    # but warn clearly. The user can answer or just let it proceed.
    if [ -t 0 ]; then
      printf 'Remove override.conf? (y/N): ' >&2
      read -r ANSWER || true
      case "$ANSWER" in
        y|Y)
          info "Removing override directory..."
          run_sudo rm -rf "${OVERRIDE_DIR}" && \
            success "Override directory removed." || \
            warn "Failed to remove override directory."
          ;;
        *)
          info "Keeping override.conf intact at ${OVERRIDE_FILE}"
          info "You can safely remove it manually later if desired:"
          echo "  sudo rm -rf ${OVERRIDE_DIR}"
          ;;
      esac
    else
      # Non-interactive: keep the override directory to be safe
      warn "Non-interactive mode: keeping override.conf intact."
      info "You can safely remove it manually later if desired:"
      echo "  sudo rm -rf ${OVERRIDE_DIR}"
    fi
  else
    info "No override.conf found. Nothing to remove."
  fi

  # ----------------------------------------------------------
  # Step 5: daemon-reload
  # ----------------------------------------------------------
  section "Reloading systemd"

  run_sudo systemctl daemon-reload && \
    success "Systemd reloaded." || \
    warn "Failed to reload systemd."

  # ----------------------------------------------------------
  # Step 6: Remove install directory
  # ----------------------------------------------------------
  section "Removing project files"

  if [ -d "$INSTALL_DIR" ]; then
    info "Removing ${INSTALL_DIR}..."
    run_sudo rm -rf "${INSTALL_DIR}" && \
      success "Project directory removed." || \
      error "Failed to remove project directory."
  else
    info "Install directory does not exist: ${INSTALL_DIR}"
  fi

  # ----------------------------------------------------------
  # Summary
  # ----------------------------------------------------------
  section "Uninstallation Summary"

  echo ""
  success "OpenCode Ollama Proxy has been uninstalled."
  echo ""
  echo "The following were removed:"
  if [ -f "$UNIT_FILE" ] 2>/dev/null; then
    echo "  (still present) Unit file: ${UNIT_FILE}"
  else
    echo "  Unit file: ${UNIT_FILE} (removed)"
  fi
  echo "  Install dir: ${INSTALL_DIR} (removed)"
  echo ""
  info "The following were NOT removed:"
  echo "  - Ollama and its models"
  echo "  - User configurations outside of this project"
  if [ -f "${OVERRIDE_FILE}" ] 2>/dev/null; then
    echo "  - Override file: ${OVERRIDE_FILE} (kept for safety)"
  fi
  echo ""
}

main "$@"
