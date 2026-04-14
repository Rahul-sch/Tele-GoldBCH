import { NextResponse } from "next/server";
import { fetchState } from "@/lib/redis";

export const dynamic = "force-dynamic";
export const revalidate = 0;

export async function GET() {
  const state = await fetchState();
  if (!state) {
    return NextResponse.json(
      { error: "No state available" },
      { status: 404 }
    );
  }
  return NextResponse.json(state);
}
