# BTC Goldbach Day Trader

Automated BTC day trading system using Goldbach Bounce + PO3 Breakout strategies from quantalgo, executing paper trades on **Alpaca** (US-compliant), with optional TradingView MCP integration and nightly walk-forward optimization.

```
┌──────────────────┐     ┌────────────────────┐     ┌────────────────┐
│ TradingView MCP  │────▶│  STRATEGY ENGINE    │────▶│  Alpaca Paper  │
│ (optional)       │     │  Goldbach Bounce    │     │  (US-compliant)│
│ chart data + CDP │     │  PO3 Breakout       │     │  BTC/USD spot  │
└──────────────────┘     └────────────────────┘     └────────────────┘
         │                        │                         │
         ▼                        ▼                         ▼
┌──────────────────┐     ┌────────────────────┐     ┌────────────────┐
│ Alpaca data      │     │  Signal Manager    │     │  Risk Manager  │
│ (historical +    │     │  dedup + conflict  │     │  sizing + $150 │
│ live candles)    │     │  resolution        │     │  circuit break │
└──────────────────┘     └────────────────────┘     └────────────────┘
                                  │
                    ┌─────────────┼──────────────┐
                    ▼             ▼              ▼
              ┌──────────┐ ┌──────────┐  ┌────────────┐
              │ Telegram │ │ Terminal │  │ JSON Log   │
              │ alerts   │ │ (rich)   │  │ (audit)    │
              └──────────┘ └──────────┘  └────────────┘
                                  │
                                  ▼
                         ┌────────────────┐
                         │ Nightly        │
                         │ Optimizer      │
                         │ (walk-forward) │
                         └────────────────┘
```

## Quick Start

```bash
git clone https://github.com/Rahul-sch/Tele-GoldBCH.git
cd Tele-GoldBCH
cp .env.example .env
# Edit .env: add ALPACA_API_KEY and ALPACA_SECRET from alpaca.markets
chmod +x run.sh
./run.sh --once    # Single analysis
./run.sh           # Live trading (AM + PM sessions)
```

## Setup

### 1. Alpaca Paper Account (Required, US-compliant)
1. Go to [alpaca.markets/signup](https://alpaca.markets/signup) — free, no KYC for paper trading
2. Sign up with email (takes ~30 seconds)
3. Once in the dashboard, make sure "Paper Trading" is selected (toggle in top-right)
4. Left sidebar → **API Keys** → **Generate New Key**
5. Copy the **API Key** and **Secret** immediately (secret only shown once)
6. Paste into `.env`:
   ```
   ALPACA_API_KEY=your_key_here
   ALPACA_SECRET=your_secret_here
   ALPACA_PAPER=true
   ```
7. Your paper account starts with $100,000 virtual USD

### 2. TradingView MCP (Optional)
1. Launch TradingView Desktop with `--remote-debugging-port=9222`
2. Set `TV_ENABLED=true` in `.env`
3. The system reads chart data via Chrome DevTools Protocol

### 3. Telegram Alerts (Optional)
1. Create bot via [@BotFather](https://t.me/botfather)
2. Get chat ID from [@userinfobot](https://t.me/userinfobot)
3. Set `TELEGRAM_ENABLED=true` with token and chat ID in `.env`

## Usage

```bash
./run.sh                     # Live: AM (8-10 ET) + PM (2-4 ET) sessions
./run.sh --once              # One analysis cycle, then exit
./run.sh --session am        # Only morning session
./run.sh --session pm        # Only afternoon session
./run.sh --optimize          # Force nightly optimization
./run.sh --backtest 14       # Backtest last 14 days
./run.sh --no-tv             # Skip TradingView, Alpaca data only
./run.sh --timeframe 5m      # Override timeframe
```

## How the Strategies Work

### Goldbach Bounce (Mean-Reversion)
Uses your Goldbach level engine to find PO3/PO9 levels within a rolling dealing range. Buys at discount levels, sells at premium levels, targeting equilibrium. Confirmed with RSI for confidence scoring.

### PO3 Breakout (Momentum)
Detects when price breaks through a PO3 level and trades the continuation to the next level. Volume confirmation increases confidence. Wider stops for BTC volatility.

### Conflict Resolution
If both strategies fire opposite signals at the same price zone, PO3 Breakout (momentum) takes priority over Goldbach Bounce (mean-reversion).

## Risk Management

- **Position sizing**: 1% equity risk per trade
- **Circuit breaker**: Trading halts at -$150 daily loss
- **Max positions**: 3 concurrent
- **Min R:R**: 1.5x required
- **Break-even**: SL moves to entry when 50% to target

## Nightly Optimizer

At midnight ET, the system runs walk-forward optimization on the last 14 days:
- Tests 48 parameter combinations (lookback × tolerance × sl_mult)
- Scores by net simulated PnL
- Saves best params for next trading day
- Logs results and sends Telegram alert

## Going Live (Future)

When you're ready to trade real money, just flip one flag:
```
ALPACA_PAPER=false
```
And add your live Alpaca API keys. Same codebase, same strategies, real execution. **Don't do this until you've paper traded for weeks and understand exactly how the system behaves.**

## Disclaimer

This is a **paper trading tool** for strategy development and education. It is NOT financial advice. AI-generated trading signals are experimental. You are responsible for all trading decisions. Never trade with money you can't afford to lose.

## License

MIT
