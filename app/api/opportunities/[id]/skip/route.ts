import { NextResponse } from "next/server";
import { db } from "@/lib/db";
import type { Opportunity } from "@/lib/types";

/**
 * Atla — fırsatı reddeder.
 *
 * Reddetme de bir öğrenme sinyali: EDITH'in "bu iyi bir fırsat" dediği ama
 * patronun atladığı içerikler, zamanla neyin gerçekten değerli olduğunu öğretir.
 * Bu yüzden sessizce silmiyoruz, decisions_log'a yazıyoruz.
 */
export async function POST(
  _request: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;

  const { data, error } = await db
    .from("opportunities")
    .select("*")
    .eq("id", id)
    .single();

  if (error || !data) {
    return NextResponse.json({ error: "Fırsat bulunamadı" }, { status: 404 });
  }

  const opportunity = data as Opportunity;

  await db.from("opportunities").update({ status: "rejected" }).eq("id", id);

  await db.from("decisions_log").insert({
    opportunity_id: id,
    ai_action: opportunity.ai_action,
    user_action: "skip",
    draft_before: opportunity.ai_draft,
    draft_after: null,
  });

  return NextResponse.json({ ok: true });
}
