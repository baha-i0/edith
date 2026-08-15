import { NextResponse } from "next/server";
import { db } from "@/lib/db";
import { reddit } from "@/lib/platforms/reddit";
import { alreadyEngaged, checkRateLimit } from "@/lib/rateLimit";
import { containsLink } from "@/lib/rules";
import type { Opportunity } from "@/lib/types";

/**
 * Onay → gerçek yayın.
 *
 * Sıralama önemli: insan onayı BAŞLANGIÇ değil, son adımdan önceki adım.
 * Onaydan sonra hâlâ iki kapı var (hız limiti ve eksik-bilgi kontrolü), çünkü
 * elin kayması da hesabı yakabilir ve o hatanın geri dönüşü yok.
 */
export async function POST(
  request: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  const { draft } = (await request.json()) as { draft?: string };

  const { data, error } = await db
    .from("opportunities")
    .select("*")
    .eq("id", id)
    .single();

  if (error || !data) {
    return NextResponse.json({ error: "Fırsat bulunamadı" }, { status: 404 });
  }

  const opportunity = data as Opportunity;

  if (opportunity.status !== "pending") {
    return NextResponse.json(
      { error: `Bu fırsat zaten işlenmiş (${opportunity.status})` },
      { status: 409 }
    );
  }

  const body = (draft ?? "").trim();
  if (!body) {
    return NextResponse.json({ error: "Taslak boş" }, { status: 400 });
  }

  // EDITH bilmediği kişisel detayı uydurmadı; patron kendi yazmadıysa yayınlanmaz.
  if (opportunity.missing_info?.length && body === (opportunity.ai_draft ?? "")) {
    return NextResponse.json(
      { error: "Eksik bilgi var — taslağı sen yazmadan yayınlanamaz" },
      { status: 400 }
    );
  }

  const action = opportunity.final_action ?? opportunity.ai_action ?? "ENGAGE";

  if (action === "SKIP") {
    return NextResponse.json(
      { error: "SKIP olarak işaretlenmiş bir fırsat yayınlanamaz" },
      { status: 400 }
    );
  }

  // Kurallar motoru link yasağı işaretlediyse burada da tutulur: taslak elle
  // düzenlenmiş olabilir ve link sonradan eklenmiş olabilir.
  if (opportunity.blocked_reason?.includes("link") && containsLink(body)) {
    return NextResponse.json(
      { error: "Bu toplulukta link yasak — taslaktaki linki kaldır" },
      { status: 400 }
    );
  }

  if (await alreadyEngaged(opportunity.platform, opportunity.external_id)) {
    return NextResponse.json(
      { error: "Bu thread'e zaten yanıt verilmiş" },
      { status: 409 }
    );
  }

  const limit = await checkRateLimit(
    opportunity.platform,
    opportunity.community,
    action
  );
  if (!limit.allowed) {
    return NextResponse.json({ error: limit.reason }, { status: 429 });
  }

  let postedUrl: string;
  try {
    postedUrl = await reddit.publish({
      kind: "comment",
      parentId: opportunity.external_id,
      body,
    });
  } catch (err) {
    const message = (err as Error).message;
    await db
      .from("opportunities")
      .update({ status: "failed", error_message: message })
      .eq("id", id);

    return NextResponse.json({ error: `Yayınlanamadı: ${message}` }, { status: 502 });
  }

  await db
    .from("opportunities")
    .update({
      status: "posted",
      posted_url: postedUrl,
      posted_at: new Date().toISOString(),
      ai_draft: body,
      final_action: action,
    })
    .eq("id", id);

  // Öğrenmenin ham sinyali. draft_before ≠ draft_after ise patron üslubu
  // düzeltmiş demektir; learn.yml en çok bu farktan öğrenir.
  await db.from("decisions_log").insert({
    opportunity_id: id,
    ai_action: opportunity.ai_action,
    user_action: body === (opportunity.ai_draft ?? "") ? "approve" : "edit",
    draft_before: opportunity.ai_draft,
    draft_after: body,
  });

  return NextResponse.json({ ok: true, url: postedUrl });
}
