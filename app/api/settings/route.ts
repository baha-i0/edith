import { NextResponse } from "next/server";
import { db, getSettings } from "@/lib/db";
import type { AggressionLevel } from "@/lib/types";

const VALID_LEVELS: AggressionLevel[] = ["temkinli", "dengeli", "atak"];

export async function GET() {
  const settings = await getSettings();
  return NextResponse.json(settings);
}

export async function POST(request: Request) {
  const body = (await request.json()) as {
    aggression_level?: string;
    max_app_share_per_week?: number;
    max_comments_per_day?: number;
    paused?: boolean;
  };

  const update: Record<string, unknown> = {};

  if (body.aggression_level !== undefined) {
    if (!VALID_LEVELS.includes(body.aggression_level as AggressionLevel)) {
      return NextResponse.json({ error: "Geçersiz atak seviyesi" }, { status: 400 });
    }
    update.aggression_level = body.aggression_level;
  }

  if (body.max_app_share_per_week !== undefined) {
    if (!Number.isInteger(body.max_app_share_per_week) || body.max_app_share_per_week < 0) {
      return NextResponse.json({ error: "Geçersiz haftalık tanıtım limiti" }, { status: 400 });
    }
    update.max_app_share_per_week = body.max_app_share_per_week;
  }

  if (body.max_comments_per_day !== undefined) {
    if (!Number.isInteger(body.max_comments_per_day) || body.max_comments_per_day < 0) {
      return NextResponse.json({ error: "Geçersiz günlük limit" }, { status: 400 });
    }
    update.max_comments_per_day = body.max_comments_per_day;
  }

  if (body.paused !== undefined) {
    update.paused = Boolean(body.paused);
  }

  if (Object.keys(update).length === 0) {
    return NextResponse.json({ error: "Güncellenecek alan yok" }, { status: 400 });
  }

  update.updated_at = new Date().toISOString();

  const { data, error } = await db
    .from("settings")
    .update(update)
    .eq("id", 1)
    .select()
    .single();

  if (error) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }

  return NextResponse.json(data);
}
