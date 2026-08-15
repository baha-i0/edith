import { db, getSettings } from "./db";
import type { Action, Platform } from "./types";

/**
 * Hız limitleri — onaydan SONRAKİ son kapı.
 *
 * Kurallar motoru "bu içerik uygun mu" sorusuna bakar; burası "bugün zaten çok
 * mu paylaştık" sorusuna. İkisi ayrı: tek tek kusursuz olan on yorum, aynı gün
 * içinde art arda atıldığında spam görünür ve hesabı yakar.
 *
 * Bilinçli olarak insan onayından SONRA çalışır — patronun elinin kayması da
 * hesabı yakmasın diye. Onay, limiti aşma yetkisi vermez.
 */

export interface LimitVerdict {
  allowed: boolean;
  reason?: string;
}

export async function checkRateLimit(
  platform: Platform,
  community: string,
  action: Action
): Promise<LimitVerdict> {
  const settings = await getSettings();

  if (settings.paused) {
    return { allowed: false, reason: "Sistem duraklatılmış (ayarlar → paused)" };
  }

  const now = Date.now();
  const dayAgo = new Date(now - 86_400_000).toISOString();
  const weekAgo = new Date(now - 7 * 86_400_000).toISOString();

  // Günlük toplam yayın tavanı
  const { count: todayCount, error: todayErr } = await db
    .from("opportunities")
    .select("id", { count: "exact", head: true })
    .eq("status", "posted")
    .gte("posted_at", dayAgo);

  if (todayErr) throw new Error(`Hız limiti okunamadı: ${todayErr.message}`);

  if ((todayCount ?? 0) >= settings.max_comments_per_day) {
    return {
      allowed: false,
      reason: `Günlük yayın tavanına ulaşıldı (${settings.max_comments_per_day}). Yarın devam.`,
    };
  }

  // Topluluk başına haftalık APP_SHARE tavanı
  if (action === "APP_SHARE") {
    const { count: weekCount, error: weekErr } = await db
      .from("opportunities")
      .select("id", { count: "exact", head: true })
      .eq("status", "posted")
      .eq("platform", platform)
      .eq("community", community)
      .eq("final_action", "APP_SHARE")
      .gte("posted_at", weekAgo);

    if (weekErr) throw new Error(`Hız limiti okunamadı: ${weekErr.message}`);

    if ((weekCount ?? 0) >= settings.max_app_share_per_week) {
      return {
        allowed: false,
        reason: `r/${community} için haftalık tanıtım hakkı dolu (${settings.max_app_share_per_week}). Bu topluluğa bu hafta yalnızca katkı yorumu.`,
      };
    }
  }

  return { allowed: true };
}

/** Aynı thread'e ikinci kez yanıt vermeyi engeller. */
export async function alreadyEngaged(
  platform: Platform,
  externalId: string
): Promise<boolean> {
  const { count } = await db
    .from("opportunities")
    .select("id", { count: "exact", head: true })
    .eq("platform", platform)
    .eq("external_id", externalId)
    .eq("status", "posted");

  return (count ?? 0) > 0;
}
