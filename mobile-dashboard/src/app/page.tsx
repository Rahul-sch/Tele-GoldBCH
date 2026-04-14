"use client";

import { useEffect, useState } from "react";
import type { TradingState } from "@/lib/redis";

export default function Dashboard() {
  const [state, setState] = useState<TradingState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastFetch, setLastFetch] = useState<Date | null>(null);

  useEffect(() => {
    const fetchState = async () => {
      try {
        const res = await fetch("/api/state", { cache: "no-store" });
        if (!res.ok) {
          setError(res.status === 404 ? "Waiting for bot to sync..." : "Error");
          return;
        }
        const data = await res.json();
        setState(data);
        setError(null);
        setLastFetch(new Date());
      } catch {
        setError("Network error");
      }
    };
    fetchState();
    const id = setInterval(fetchState, 10000);
    return () => clearInterval(id);
  }, []);

  if (!state && !error) {
    return (
      <main className="min-h-screen bg-neutral-950 text-neutral-100 flex items-center justify-center">
        <div className="text-neutral-500">Loading…</div>
      </main>
    );
  }

  if (!state) {
    return (
      <main className="min-h-screen bg-neutral-950 text-neutral-100 flex items-center justify-center">
        <div className="text-center">
          <div className="text-neutral-400 text-lg mb-2">{error}</div>
          <div className="text-neutral-600 text-sm">Retrying every 10s...</div>
        </div>
      </main>
    );
  }

  const { kpis, open_positions, recent_closed, updated_at } = state;
  const pnlColor = kpis.total_pnl_today >= 0 ? "text-emerald-400" : "text-red-400";
  const navChange = kpis.equity - 100000;
  const navChangePct = (navChange / 100000) * 100;

  return (
    <main className="min-h-screen bg-neutral-950 text-neutral-100 p-4 sm:p-6">
      <div className="max-w-5xl mx-auto">
        <header className="mb-6 flex items-center justify-between">
          <div>
            <h1 className="text-xl sm:text-2xl font-bold tracking-tight">Tele-GoldBCH</h1>
            <p className="text-xs text-neutral-500">Goldbach Bounce + ICT Continuation · OANDA Practice</p>
          </div>
          <div className="text-right">
            <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-emerald-500/10 border border-emerald-500/30">
              <div className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
              <span className="text-xs text-emerald-300 font-medium">LIVE</span>
            </div>
            <div className="text-[10px] text-neutral-600 mt-1">
              Last sync: {lastFetch ? lastFetch.toLocaleTimeString() : "—"}
            </div>
          </div>
        </header>

        <section className="grid grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-4 mb-6">
          <KpiCard
            label="NAV"
            value={`$${kpis.equity.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`}
            sub={`${navChange >= 0 ? "+" : ""}$${navChange.toFixed(2)} (${navChangePct >= 0 ? "+" : ""}${navChangePct.toFixed(2)}%)`}
            subColor={navChange >= 0 ? "text-emerald-400" : "text-red-400"}
          />
          <KpiCard
            label="P&L Today"
            value={`${kpis.total_pnl_today >= 0 ? "+" : ""}$${kpis.total_pnl_today.toFixed(2)}`}
            valueColor={pnlColor}
            sub={`${kpis.closed_trades} closed`}
          />
          <KpiCard
            label="Win Rate"
            value={`${kpis.win_rate.toFixed(1)}%`}
            sub={`${kpis.wins}W / ${kpis.losses}L`}
            valueColor={kpis.win_rate >= 50 ? "text-emerald-400" : "text-amber-400"}
          />
          <KpiCard
            label="Open / Unreal"
            value={`${kpis.open_trades}`}
            sub={`${kpis.unrealized_pl >= 0 ? "+" : ""}$${kpis.unrealized_pl.toFixed(2)}`}
            subColor={kpis.unrealized_pl >= 0 ? "text-emerald-400" : "text-red-400"}
          />
        </section>

        <section className="mb-6">
          <h2 className="text-xs uppercase tracking-wider text-neutral-500 font-semibold mb-2 px-1">Open Positions</h2>
          {open_positions.length === 0 ? (
            <div className="rounded-lg border border-neutral-800 bg-neutral-900/40 p-6 text-center text-neutral-600 text-sm">No open positions</div>
          ) : (
            <div className="rounded-lg border border-neutral-800 bg-neutral-900/40 overflow-hidden">
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="bg-neutral-900/60 text-neutral-500 text-xs uppercase tracking-wider">
                    <tr>
                      <th className="px-4 py-3 text-left">Pair</th>
                      <th className="px-4 py-3 text-left">Dir</th>
                      <th className="px-4 py-3 text-right">Entry</th>
                      <th className="px-4 py-3 text-right">SL</th>
                      <th className="px-4 py-3 text-right">TP</th>
                      <th className="px-4 py-3 text-right">Units</th>
                      <th className="px-4 py-3 text-right">P&L</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-neutral-800">
                    {open_positions.map((p, i) => (
                      <tr key={i} className="hover:bg-neutral-900/50 transition-colors">
                        <td className="px-4 py-3 font-medium">{p.instrument.replace("_", "/")}</td>
                        <td className="px-4 py-3">
                          <span className={`inline-block px-2 py-0.5 rounded text-xs font-semibold ${p.direction === "LONG" ? "bg-emerald-500/10 text-emerald-400" : "bg-red-500/10 text-red-400"}`}>{p.direction}</span>
                        </td>
                        <td className="px-4 py-3 text-right font-mono text-neutral-300">{p.entry}</td>
                        <td className="px-4 py-3 text-right font-mono text-neutral-500 text-xs">{p.sl || "—"}</td>
                        <td className="px-4 py-3 text-right font-mono text-neutral-500 text-xs">{p.tp || "—"}</td>
                        <td className="px-4 py-3 text-right font-mono text-neutral-400">{p.units.toLocaleString()}</td>
                        <td className={`px-4 py-3 text-right font-mono font-semibold ${p.unrealized_pl >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                          {p.unrealized_pl >= 0 ? "+" : ""}${p.unrealized_pl.toFixed(2)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </section>

        <section>
          <h2 className="text-xs uppercase tracking-wider text-neutral-500 font-semibold mb-2 px-1">
            Recent Trades ({recent_closed.length})
          </h2>
          {recent_closed.length === 0 ? (
            <div className="rounded-lg border border-neutral-800 bg-neutral-900/40 p-6 text-center text-neutral-600 text-sm">No closed trades yet</div>
          ) : (
            <div className="rounded-lg border border-neutral-800 bg-neutral-900/40 overflow-hidden">
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="bg-neutral-900/60 text-neutral-500 text-xs uppercase tracking-wider">
                    <tr>
                      <th className="px-4 py-3 text-left">Pair</th>
                      <th className="px-4 py-3 text-left">Dir</th>
                      <th className="px-4 py-3 text-right">Entry</th>
                      <th className="px-4 py-3 text-right">Exit</th>
                      <th className="px-4 py-3 text-right">P&L</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-neutral-800">
                    {recent_closed.map((t, i) => (
                      <tr key={i} className="hover:bg-neutral-900/50 transition-colors">
                        <td className="px-4 py-3 font-medium">{t.instrument.replace("_", "/")}</td>
                        <td className="px-4 py-3">
                          <span className={`inline-block px-2 py-0.5 rounded text-xs font-semibold ${t.direction === "LONG" ? "bg-emerald-500/10 text-emerald-400" : "bg-red-500/10 text-red-400"}`}>{t.direction}</span>
                        </td>
                        <td className="px-4 py-3 text-right font-mono text-neutral-400">{t.entry}</td>
                        <td className="px-4 py-3 text-right font-mono text-neutral-400">{t.close}</td>
                        <td className={`px-4 py-3 text-right font-mono font-semibold ${t.pnl >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                          {t.pnl >= 0 ? "+" : ""}${t.pnl.toFixed(2)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </section>

        <footer className="mt-8 text-center text-xs text-neutral-700">
          Synced at {new Date(updated_at).toLocaleString()} · Auto-refresh every 10s
        </footer>
      </div>
    </main>
  );
}

function KpiCard({
  label,
  value,
  valueColor = "text-neutral-100",
  sub,
  subColor = "text-neutral-500",
}: {
  label: string;
  value: string;
  valueColor?: string;
  sub?: string;
  subColor?: string;
}) {
  return (
    <div className="rounded-lg border border-neutral-800 bg-neutral-900/40 p-3 sm:p-4">
      <div className="text-[10px] sm:text-xs uppercase tracking-wider text-neutral-500 font-semibold mb-1">{label}</div>
      <div className={`text-lg sm:text-2xl font-bold font-mono tabular-nums ${valueColor}`}>{value}</div>
      {sub && <div className={`text-[10px] sm:text-xs font-mono mt-1 ${subColor}`}>{sub}</div>}
    </div>
  );
}
