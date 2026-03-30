#!/usr/bin/env bash
# BetRivers Poker Tracker - Unofficial — macOS Daily Launcher
# -------------------------------------------------------
# Double-click this file on your Desktop to start the app.
#
# Permissions issues? If this file won't execute, run in Terminal:
#     chmod +x ~/Desktop/BetRiversTracker.command
#
# What this script does:
#   1. Starts (or resumes) the PostgreSQL 17 service via Homebrew.
#   2. Activates the virtual environment.
#   3. Launches Streamlit on localhost:8501.
#   4. Opens http://localhost:8501 in your default browser.
#   5. Stops Streamlit when you close this Terminal window.
# -------------------------------------------------------

set -euo pipefail

INSTALL_DIR="${HOME}/Applications/BetRiversTracker"
VENV="${INSTALL_DIR}/venv"
MAIN="${INSTALL_DIR}/app/main.py"
PORT=8501

# ── Verify install exists ────────────────────────────────────────────────────
if [[ ! -d "${INSTALL_DIR}" ]]; then
    echo "Install directory not found at ${INSTALL_DIR}"
    echo "Please run install.command first."
    read -rp "Press Enter to close..." _
    exit 1
fi

# ── Start PostgreSQL (idempotent) ─────────────────────────────────────────────
# Use absolute paths so this works even when brew is not yet on PATH in a
# freshly opened Terminal window (e.g. on Apple Silicon before shell profile loads).
BREW="/opt/homebrew/bin/brew"
[[ ! -x "${BREW}" ]] && BREW="/usr/local/bin/brew"   # Intel fallback
"${BREW}" services start postgresql@17 2>/dev/null || true

# ── Activate the venv ────────────────────────────────────────────────────────
if [[ ! -f "${VENV}/bin/activate" ]]; then
    echo "Virtual environment not found at ${VENV}"
    echo "Please run install.command first."
    read -rp "Press Enter to close..." _
    exit 1
fi

# shellcheck disable=SC1091
source "${VENV}/bin/activate"

# ── Check if Streamlit is already running ────────────────────────────────────
if lsof -i :"${PORT}" &>/dev/null; then
    echo "BetRivers Tracker - Unofficial appears to be already running on port ${PORT}."
    open "http://localhost:${PORT}"
    exit 0
fi

# ── Launch Streamlit ─────────────────────────────────────────────────────────
cd "${INSTALL_DIR}"
streamlit run "${MAIN}" --server.headless true --server.port "${PORT}" &
STREAMLIT_PID=$!

# Clean up Streamlit when this Terminal window is closed or Ctrl+C is pressed
trap "kill ${STREAMLIT_PID} 2>/dev/null; exit 0" EXIT INT TERM

# ── Wait for Streamlit to be ready, then open browser ────────────────────────
echo "Starting BetRivers Tracker - Unofficial..."
for i in $(seq 1 30); do
    if curl -s -o /dev/null "http://localhost:${PORT}" 2>/dev/null; then
        open "http://localhost:${PORT}"
        break
    fi
    sleep 1
done

# Keep the script alive so the trap can clean up when the window closes
wait "${STREAMLIT_PID}"
