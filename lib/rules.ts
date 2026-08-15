import { db } from "./db";
import { reddit } from "./platforms/reddit";
import type { Action, Community } from "./types";

/**
 * Community Rules Engine — AI'ın önerisinden BAĞIMSIZ güvenlik katmanı.
 *
 * Buradaki kararlar modele sorulmaz, modelin önerisinin üstüne uygulanır.
 * Sebep: model iyi bir tanıtım metni yazma konusunda ikna edici olabilir ama
 * topluluğun kuralını çiğnediğini fark etmeyebilir. Kural ihlalinin bedeli
 * silinen yorum ve nihayetinde banlanan hesap — bu yüzden karar mekanizması
 * modelden ayrı tutuldu.
 */

const RULES_TTL_DAYS = 14;

/** Kural metinlerinde self-promo yasağına işaret eden kalıplar. */
const PROMO_BAN_PATTERNS = [
  "no self-promo",
  "no self promotion",
  "self-promotion is not allowed",
  "self-promotion is prohibited",
  "no advertising",
  "no advertisement",
  "no promotion",
  "no soliciting",
  "no spam",
  "do not promote",
  "no marketing",
  "no apps",
  "no product",
  "no referral",
];

const LINK_BAN_PATTERNS = [
  "no links",
  "no external links",
  "links are not allowed",
  "no blogspam",
];

export interface RuleVerdict {
  /** Kurallar uygulandıktan sonraki nihai aksiyon. */
  action: Action;
  /** Aksiyon düşürüldüyse nedeni; düşürülmediyse null. */
  blockedReason: string | null;
  /** Taslakta link bulunmasına izin var mı. */
  linksAllowed: boolean;
}

interface RedditRulesResponse {
  rules?: { short_name?: string; description?: string }[];
}

/** Topluluk kurallarını getirir; cache bayatladıysa yeniler. */
export async function ensureRules(community: Community): Promise<Community> {
  const fetchedAt = community.rules_fetched_at
    ? new Date(community.rules_fetched_at).getTime()
    : 0;
  const ageDays = (Date.now() - fetchedAt) / 86_400_000;

  if (community.rules_cache && ageDays < RULES_TTL_DAYS) return community;

  try {
    const raw = (await reddit.fetchRules(community.name)) as RedditRulesResponse;
    const summary = (raw.rules ?? [])
      .map((r) => `${r.short_name ?? ""}: ${r.description ?? ""}`)
      .join("\n")
      .slice(0, 4000);

    const { data } = await db
      .from("communities")
      .update({
        rules_cache: raw,
        rules_summary: summary,
        rules_fetched_at: new Date().toISOString(),
      })
      .eq("id", community.id)
      .select()
      .single();

    return (data as Community) ?? community;
  } catch (err) {
    console.error(
      `[rules] r/${community.name} kuralları çekilemedi:`,
      (err as Error).message
    );
    // Kuralları bilmiyoruz → aşağıdaki karar mantığı güvenli tarafa düşecek.
    return community;
  }
}

/**
 * AI'ın önerdiği aksiyonu topluluk kurallarına göre sınırlar.
 *
 * Kural: aksiyon yalnızca AŞAĞI çekilebilir, asla yukarı. Model ENGAGE dediyse
 * kurallar motoru bunu APP_SHARE'e yükseltmez.
 */
export function applyRules(
  proposed: Action,
  community: Community,
  draft: string
): RuleVerdict {
  const rulesText = (community.rules_summary ?? "").toLowerCase();
  const knowsRules = Boolean(community.rules_summary);

  const promoBanned =
    PROMO_BAN_PATTERNS.some((p) => rulesText.includes(p)) ||
    community.self_promo_tolerance === "none";

  const linksBanned = LINK_BAN_PATTERNS.some((p) => rulesText.includes(p));
  const linksAllowed = !linksBanned;

  if (proposed !== "APP_SHARE") {
    // Tanıtım içermeyen aksiyonlar kurallardan etkilenmez, ama taslakta izinsiz
    // link varsa yine de işaretlenir.
    if (!linksAllowed && containsLink(draft)) {
      return {
        action: proposed,
        blockedReason: "Toplulukta link yasak — taslaktaki link kaldırılmalı",
        linksAllowed,
      };
    }
    return { action: proposed, blockedReason: null, linksAllowed };
  }

  if (promoBanned) {
    return {
      action: "ENGAGE",
      blockedReason:
        "Topluluk kuralları self-promotion'ı yasaklıyor — APP_SHARE bloklandı, katkı yorumuna düşürüldü",
      linksAllowed,
    };
  }

  if (!knowsRules) {
    // Emin değilsek daha güvenli aksiyona düşeriz. Bilinmezlik, izin değildir.
    return {
      action: "ENGAGE",
      blockedReason:
        "Topluluk kuralları okunamadı — güvenli tarafta kalmak için APP_SHARE düşürüldü",
      linksAllowed,
    };
  }

  if (community.self_promo_tolerance === "low") {
    return {
      action: "APP_SHARE",
      blockedReason:
        "Toplulukta self-promo toleransı düşük — yalnızca doğrudan sorulmuşsa onayla",
      linksAllowed,
    };
  }

  return { action: "APP_SHARE", blockedReason: null, linksAllowed };
}

export function containsLink(text: string): boolean {
  return /https?:\/\/|apps\.apple\.com|\bapp\.link\b/i.test(text);
}
