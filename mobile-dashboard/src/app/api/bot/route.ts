import { NextResponse } from "next/server";
import { redis } from "@/lib/redis";

export const dynamic = "force-dynamic";

/** GET /api/bot — returns status of forex and nasdaq bots */
export async function GET() {
  try {
    const [forexRaw, nasdaqRaw] = await Promise.all([
      redis.get<string>("tele_goldbch:bot:forex:status"),
      redis.get<string>("tele_goldbch:bot:nasdaq:status"),
    ]);

    const parse = (raw: string | null) => {
      if (!raw) return { status: "unknown", updated_at: null };
      try {
        return typeof raw === "string" ? JSON.parse(raw) : raw;
      } catch {
        return { status: "unknown", updated_at: null };
      }
    };

    return NextResponse.json({
      forex: parse(forexRaw),
      nasdaq: parse(nasdaqRaw),
    });
  } catch (err) {
    return NextResponse.json({ error: "Failed to fetch bot status" }, { status: 500 });
  }
}

/** POST /api/bot — send start/stop command to a bot */
export async function POST(request: Request) {
  try {
    const body = await request.json();
    const { bot, action } = body as { bot: string; action: string };

    if (!["forex", "nasdaq"].includes(bot)) {
      return NextResponse.json({ error: "Invalid bot name" }, { status: 400 });
    }
    if (!["start", "stop"].includes(action)) {
      return NextResponse.json({ error: "Invalid action" }, { status: 400 });
    }

    const key = `tele_goldbch:bot:${bot}:command`;
    await redis.set(key, JSON.stringify({
      action,
      requested_at: new Date().toISOString(),
    }));

    return NextResponse.json({ ok: true, bot, action });
  } catch (err) {
    return NextResponse.json({ error: "Failed to send command" }, { status: 500 });
  }
}
