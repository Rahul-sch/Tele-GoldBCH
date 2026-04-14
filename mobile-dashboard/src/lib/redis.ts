import { Redis } from "@upstash/redis";

export const redis = new Redis({
  url: process.env.UPSTASH_REDIS_REST_URL!,
  token: process.env.UPSTASH_REDIS_REST_TOKEN!,
});

export type Position = {
  instrument: string;
  direction: "LONG" | "SHORT";
  units: number;
  entry: number;
  sl: number | null;
  tp: number | null;
  unrealized_pl: number;
  opened_at?: string;
};

export type ClosedTrade = {
  instrument: string;
  direction: "LONG" | "SHORT";
  entry: number;
  close: number;
  pnl: number;
  closed_at?: string;
};

export type TradingState = {
  updated_at: string;
  kpis: {
    equity: number;
    balance: number;
    unrealized_pl: number;
    total_pnl_today: number;
    open_trades: number;
    closed_trades: number;
    wins: number;
    losses: number;
    win_rate: number;
  };
  open_positions: Position[];
  recent_closed: ClosedTrade[];
};

export async function fetchState(): Promise<TradingState | null> {
  try {
    const raw = await redis.get<TradingState | string>("tele_goldbch:state");
    if (!raw) return null;
    // Upstash auto-deserializes JSON objects, but handle string case too
    return typeof raw === "string" ? JSON.parse(raw) : raw;
  } catch (err) {
    console.error("Redis fetch error:", err);
    return null;
  }
}
