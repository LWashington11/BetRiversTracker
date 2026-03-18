#!/usr/bin/env bash
# BetRivers Poker Tracker — macOS One-Shot Installer
# -------------------------------------------------------
# Double-click this file in Finder (or run it in Terminal) to install
# everything needed to run BetRivers Poker Tracker on macOS.
#
# Permissions issues? If this file won't execute, run in Terminal:
#     chmod +x installer/macos/install.command
#
# What this script does:
#   1. Detects CPU architecture (Apple Silicon vs Intel) and logs it.
#   2. Installs Homebrew if it is not already present.
#   3. Installs Python 3.14 and PostgreSQL 17 via Homebrew.
#   4. Starts the PostgreSQL service and waits for it to accept connections.
#   5. Copies the app to ~/Applications/BetRiversTracker/.
#   6. Creates a Python virtual environment and installs all dependencies.
#   7. Generates a random DB password, writes .env, and initialises the DB.
#   8. Places launch.command on the Desktop for daily use.
#
# If macOS blocks this script ("unidentified developer"), right-click the
# file → Open → Open.  Or run:  xattr -cr <path-to-this-folder>
# -------------------------------------------------------

set -euo pipefail

# ── Version pins (bump these when Homebrew drops an older formula) ────────────
PG_VERSION="postgresql@17"   # e.g. postgresql@18 when 17 is removed
PY_VERSION="python@3.14"     # e.g. python@3.15

# ── Colours (using printf for portability) ────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()    { printf "${GREEN}[BRT]${NC} %s\n" "$*"; }
warn()    { printf "${YELLOW}[BRT]${NC} %s\n" "$*"; }
error()   { printf "${RED}[BRT] ERROR:${NC} %s\n" "$*" >&2; }

# ── Cleanup trap ──────────────────────────────────────────────────────────────
cleanup() {
    local exit_code=$?
    if [[ ${exit_code} -ne 0 ]]; then
        echo ""
        warn "Installation did not complete successfully (exit code ${exit_code})."
        warn "A partial install may exist at ${INSTALL_DIR:-~/Applications/BetRiversTracker}."
        warn "You can re-run this script to retry."
        if [[ -f "${LOG_FILE:-}" ]]; then
            warn "See log file: ${LOG_FILE}"
        fi
    fi
}
trap cleanup EXIT

# ── Banner ────────────────────────────────────────────────────────────────────
echo ""
echo "============================================="
echo "  BetRivers Poker Tracker — macOS Installer"
echo "============================================="
echo ""

# ── Architecture detection ────────────────────────────────────────────────────
ARCH="$(uname -m)"
info "Detected architecture: ${ARCH}"

# ── Determine paths ───────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
INSTALL_DIR="${HOME}/Applications/BetRiversTracker"
DESKTOP="${HOME}/Desktop"

info "Project source : ${PROJECT_DIR}"
info "Install target : ${INSTALL_DIR}"

# ── Log file (captures everything for troubleshooting) ────────────────────────
mkdir -p "${INSTALL_DIR}"
LOG_FILE="${INSTALL_DIR}/install.log"
exec > >(tee -a "${LOG_FILE}") 2>&1
info "Logging to ${LOG_FILE}"

# ── Xcode Command Line Tools ──────────────────────────────────────────────────
# Required by Homebrew (and by pip for native package compilation).
# xcode-select -p exits non-zero when the tools are absent.
if ! xcode-select -p &>/dev/null; then
    info "Xcode Command Line Tools not found — installing..."
    info "A macOS dialog will appear asking you to install the tools."
    info "Click 'Install' and wait for it to finish, then this script will continue."
    # Trigger the GUI install dialog
    xcode-select --install 2>/dev/null || true
    # Wait until the tools directory exists (the user must click Install in the dialog)
    until xcode-select -p &>/dev/null; do
        sleep 5
    done
    info "Xcode Command Line Tools installed."
else
    info "Xcode Command Line Tools already installed."
fi

# ── Homebrew ──────────────────────────────────────────────────────────────────
if ! command -v brew &>/dev/null; then
    info "Homebrew not found — installing (this may ask for your macOS password)..."
    # NONINTERACTIVE=1 avoids the "Press RETURN to continue" prompt that would
    # hang when the script is double-clicked from Finder.
    NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # Add Homebrew to PATH for the rest of this session
    if [[ "${ARCH}" == "arm64" ]]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    else
        eval "$(/usr/local/bin/brew shellenv)"
    fi
    if ! command -v brew &>/dev/null; then
        error "Homebrew installation failed. Please install manually: https://brew.sh"
        exit 1
    fi
else
    info "Homebrew already installed."
fi

# ── Python ───────────────────────────────────────────────────────────────────
info "Installing ${PY_VERSION} (this may take a few minutes)..."
if ! brew list "${PY_VERSION}" &>/dev/null; then
    brew install "${PY_VERSION}" || { error "Failed to install ${PY_VERSION} via Homebrew."; exit 1; }
else
    info "${PY_VERSION} already installed."
fi
PY_MINOR="${PY_VERSION#python@}"   # strips 'python@' → e.g. '3.13'
PYTHON="$(brew --prefix "${PY_VERSION}")/bin/python${PY_MINOR}"
if [[ ! -x "${PYTHON}" ]]; then
    error "Python binary not found at ${PYTHON}. Homebrew install may have failed."
    exit 1
fi

# ── PostgreSQL ───────────────────────────────────────────────────────────────
info "Installing ${PG_VERSION}..."
if ! brew list "${PG_VERSION}" &>/dev/null; then
    brew install "${PG_VERSION}" || { error "Failed to install ${PG_VERSION} via Homebrew."; exit 1; }
else
    info "${PG_VERSION} already installed."
fi

info "Starting ${PG_VERSION} service..."
brew services start "${PG_VERSION}" || true   # idempotent

# Wait for PostgreSQL to accept connections (up to 30 seconds)
PG_BIN="$(brew --prefix "${PG_VERSION}")/bin"
info "Waiting for PostgreSQL to accept connections..."
PG_READY=false
for i in $(seq 1 30); do
    if "${PG_BIN}/pg_isready" -q 2>/dev/null; then
        PG_READY=true
        break
    fi
    sleep 1
done
if [[ "${PG_READY}" != "true" ]]; then
    error "PostgreSQL did not start within 30 seconds. Check: brew services list"
    exit 1
fi
info "PostgreSQL is ready."

# ── Copy app to ~/Applications/BetRiversTracker/ ─────────────────────────────
info "Copying app to ${INSTALL_DIR}..."
rsync -a \
    --exclude='.git' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='venv' \
    --exclude='.env' \
    --exclude='hand_histories' \
    --exclude='installer/macos/dist' \
    "${PROJECT_DIR}/" "${INSTALL_DIR}/"

# ── Virtual environment ───────────────────────────────────────────────────────
VENV="${INSTALL_DIR}/venv"
if [[ ! -d "${VENV}" ]]; then
    info "Creating Python virtual environment..."
    "${PYTHON}" -m venv "${VENV}"
fi

info "Installing Python packages (this may take a minute)..."
"${VENV}/bin/pip" install --upgrade pip setuptools --quiet
"${VENV}/bin/pip" install -r "${INSTALL_DIR}/requirements.txt"

# ── Generate DB password & write .env ─────────────────────────────────────────
ENV_FILE="${INSTALL_DIR}/.env"
if [[ ! -f "${ENV_FILE}" ]]; then
    info "Generating database password and writing .env..."
    # The '|| true' is required: head closes the pipe after 24 bytes, sending
    # SIGPIPE to tr (exit 141). With set -o pipefail that would abort the script.
    DB_PASS="$(LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom | head -c 24 || true)"
    cat > "${ENV_FILE}" <<EOF
# PostgreSQL connection settings (auto-generated by installer)
# Edit this file if you need to change database credentials.
PGUSER=postgres
PGPASSWORD=${DB_PASS}
PGHOST=localhost
PGPORT=5432
PGDATABASE=betrivers_tracker

# Hand history directory (absolute or relative to install root)
HAND_HISTORY_DIR=./hand_histories
EOF
    chmod 600 "${ENV_FILE}"   # restrict .env to owner-only
else
    info ".env already exists — reading existing password."
    DB_PASS="$(grep '^PGPASSWORD=' "${ENV_FILE}" | cut -d= -f2)"
fi

# ── Ensure 'postgres' role exists and password matches .env ───────────────────
# Homebrew PostgreSQL creates a superuser matching the macOS username, not
# 'postgres'.  We always run this block (including on re-runs) so the role
# and password stay in sync with .env regardless of install history.
info "Configuring PostgreSQL 'postgres' role..."
PG_ROLE_SQL="
DO \$\$ BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'postgres') THEN
        CREATE ROLE postgres WITH LOGIN SUPERUSER PASSWORD '${DB_PASS}';
    ELSE
        ALTER USER postgres PASSWORD '${DB_PASS}';
    END IF;
END \$\$;"

if ! "${PG_BIN}/psql" -U "$(whoami)" -d postgres -c "${PG_ROLE_SQL}"; then
    error "Could not configure the 'postgres' role in PostgreSQL."
    error "Run this manually in Terminal, then re-run the installer:"
    error "  psql postgres -c \"CREATE ROLE postgres WITH LOGIN SUPERUSER PASSWORD '${DB_PASS}';\""
    exit 1
fi
info "PostgreSQL 'postgres' role is ready."

# ── Create the application database ──────────────────────────────────────────
# SQLAlchemy's create_all (called by app.cli init) only creates tables — it
# cannot create the database itself.  We must do that here first.
info "Creating database 'betrivers_tracker' (if it does not exist)..."
"${PG_BIN}/psql" -U "$(whoami)" -d postgres \
    -c "SELECT 1 FROM pg_database WHERE datname='betrivers_tracker'" \
    | grep -q 1 || \
"${PG_BIN}/createdb" -U "$(whoami)" betrivers_tracker 2>/dev/null || true
# Verify it now exists
if ! "${PG_BIN}/psql" -U "$(whoami)" -lqt | cut -d\| -f1 | grep -qw betrivers_tracker; then
    error "Could not create database 'betrivers_tracker'."
    error "Run this manually in Terminal, then re-run the installer:"
    error "  createdb betrivers_tracker"
    exit 1
fi
info "Database 'betrivers_tracker' is ready."

# ── Initialise the database ───────────────────────────────────────────────────
info "Initialising database..."
(cd "${INSTALL_DIR}" && "${VENV}/bin/python" -m app.cli init)

# ── Drop launch.command on the Desktop ───────────────────────────────────────
LAUNCHER_SRC="${INSTALL_DIR}/installer/macos/launch.command"
LAUNCHER_DST="${DESKTOP}/BetRiversTracker.command"

if [[ -f "${LAUNCHER_SRC}" ]]; then
    cp "${LAUNCHER_SRC}" "${LAUNCHER_DST}"
    chmod +x "${LAUNCHER_DST}"
    info "Desktop launcher created: ${LAUNCHER_DST}"
else
    warn "launch.command not found in installer/macos/ — skipping Desktop shortcut."
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
info "Installation complete!"
info "Double-click 'BetRiversTracker.command' on your Desktop to launch the app."
info "Install log saved to: ${LOG_FILE}"
echo ""
