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
echo -e "${CYAN}║  BTC Goldbach Day Trader                  ║${NC}"
echo -e "${CYAN}║  Goldbach Bounce + PO3 Breakout           ║${NC}"
echo -e "${CYAN}║  Bybit Testnet Paper Trading              ║${NC}"
echo -e "${CYAN}╚═══════════════════════════════════════════╝${NC}"
echo ""
exec "$VENV/bin/python" main.py "$@"
