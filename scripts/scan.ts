/**
 * scan — fırsat tarama hattı.
 *
 * Discover → ucuz filtre → kurallar → AI analiz → kurallar motoru → pending kayıt.
 * GitHub Actions'ta 2-3 saatte bir çalışır; elle de çalıştırılabilir (npm run scan).
 *
 * Bu script hiçbir şey YAYINLAMAZ. Yalnızca onay kuyruğunu doldurur.
 */
import { db, getSettings } from "../lib/db";
import { applyFilters, momentumScore } from "../lib/filters";
import { reddit } from "../lib/platforms/reddit";
import { applyRules, ensureRules } from "../lib/rules";
import { analyze } from "../lib/scoring";
import type { Community } from "../lib/types";

/** Tarama başına AI'a gidecek en fazla içerik — maliyet freni. */
const MAX_ANALYSES_PER_RUN = 12;

async function main() {
  const settings = await getSettings();

  if (settings.paused) {
    console.log("[scan] Sistem duraklatılmış, tarama atlandı.");
    return;
  }

  const { data: communityRows, error } = await db
    .from("communities")
    .select("*")
    .eq("platform", "reddit")
    .eq("active", true);

  if (error) throw new Error(`Topluluklar okunamadı: ${error.message}`);

  const communities = (communityRows ?? []) as Community[];
  if (communities.length === 0) {
    console.log("[scan] Aktif topluluk yok.");
    return;
  }

  console.log(`[scan] ${communities.length} topluluk taranıyor…`);

  const raw = await reddit.discover(
    communities.map((c) => c.name),
    25
  );
  console.log(`[scan] ${raw.length} ham içerik çekildi.`);

  const { kept, dropped } = await applyFilters(raw);
  console.log(
    `[scan] Ucuz filtre: ${kept.length} kaldı, ${dropped.length} elendi (AI'ya gitmedi).`
  );

  // En yüksek momentumlular önce — bütçe dolduğunda geride kalanlar zaten
  // bayatlamış olanlar olsun.
  const ranked = kept
    .map((item) => ({ item, momentum: momentumScore(item) }))
    .sort((a, b) => b.momentum - a.momentum)
    .slice(0, MAX_ANALYSES_PER_RUN);

  let created = 0;
  let skipped = 0;

  for (const { item, momentum } of ranked) {
    const community = communities.find((c) => c.name === item.community);
    if (!community) continue;

    try {
      const withRules = await ensureRules(community);
      const analysis = await analyze({
        item,
        community: withRules,
        aggression: settings.aggression_level,
      });

      if (analysis.action === "SKIP") {
        skipped++;
        continue;
      }

      const verdict = applyRules(analysis.action, withRules, analysis.draft);

      const { error: insertError } = await db.from("opportunities").insert({
        platform: "reddit",
        community: item.community,
        kind: item.kind,
        external_id: item.externalId,
        source_url: item.url,
        source_title: item.title ?? null,
        source_text: (item.text ?? "").slice(0, 8000),
        source_author: item.author ?? null,
        source_created_at: item.createdAt.toISOString(),

        relevance_score: analysis.relevance_score,
        network_score: analysis.network_score,
        promotion_score: analysis.promotion_score,
        spam_risk: analysis.spam_risk,
        community_fit: analysis.community_fit,
        magnetism_score: analysis.magnetism_score,
        momentum_score: momentum,

        ai_action: analysis.action,
        final_action: verdict.action,
        blocked_reason: verdict.blockedReason,
        ai_reason: analysis.reason,
        ai_draft: analysis.draft,
        missing_info: analysis.missing_info.length ? analysis.missing_info : null,
        status: "pending",
      });

      if (insertError) {
        // unique(platform, external_id) — yarış durumunda çift kayıt normaldir.
        if (insertError.code === "23505") continue;
        throw new Error(insertError.message);
      }

      created++;
      if (verdict.blockedReason) {
        console.log(`[scan] r/${item.community}: ${verdict.blockedReason}`);
      }
    } catch (err) {
      console.error(
        `[scan] ${item.url} analiz edilemedi:`,
        (err as Error).message
      );
    }
  }

  await db
    .from("communities")
    .update({ last_scanned_at: new Date().toISOString() })
    .in(
      "id",
      communities.map((c) => c.id)
    );

  console.log(
    `[scan] Bitti. ${created} yeni fırsat onay kuyruğunda, ${skipped} içerik SKIP.`
  );
}

main().catch((err) => {
  console.error("[scan] Hata:", err);
  process.exit(1);
});
