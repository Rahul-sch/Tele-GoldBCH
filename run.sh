#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/.venv"
GREEN='\033[0;32m'; RED='\033[0;31m'; CYAN='\033[0;36m'; NC='\033[0m'
log_ok()  { echo -e "${GREEN}[✓]${NC} $1"; }
log_err() { echo -e "${RED}[✗]${NC} $1"; }
log_do()  { echo -e "${CYAN}[→]${NC} $1"; }

# Python check
PYTHON=python3
command -v $PYTHON &>/dev/null || { log_err "Python 3.10+ required"; exit 1; }

# .env check
[[ -f "$SCRIPT_DIR/.env" ]] || { log_err ".env not found — run: cp .env.example .env"; exit 1; }

# Venv
[[ -d "$VENV" ]] || { log_do "Creating venv…"; $PYTHON -m venv "$VENV"; }
source "$VENV/bin/activate"
log_do "Installing deps…"
pip install --quiet --upgrade pip
pip install --quiet -r "$SCRIPT_DIR/requirements.txt"
log_ok "Ready"

mkdir -p "$SCRIPT_DIR/logs" "$SCRIPT_DIR/screenshots"
echo ""
echo -e "${CYAN}╔═══════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║  Tele-GoldBCH Day Trader                  ║${NC}"
echo -e "${CYAN}║  Continuation + Goldbach + Meta-Filter    ║${NC}"
echo -e "${CYAN}║  OANDA Practice / Alpaca Paper            ║${NC}"
echo -e "${CYAN}╚═══════════════════════════════════════════╝${NC}"
echo ""

# Tee output to terminal AND a rolling log file (so I can debug remotely).
LOG_FILE="$SCRIPT_DIR/logs/forward_test.log"
if [[ -f "$LOG_FILE" ]]; then
    mv "$LOG_FILE" "$LOG_FILE.$(date +%Y%m%d_%H%M%S).bak" 2>/dev/null || true
fi
echo "Logging to: $LOG_FILE"
echo ""

# Python unbuffered so logs are written real-time, not held in stdout buffer.
PYTHONUNBUFFERED=1 "$VENV/bin/python" main.py "$@" 2>&1 | tee "$LOG_FILE"
