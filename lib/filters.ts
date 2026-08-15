import { db } from "./db";
import type { RawItem } from "./types";

/**
 * Ucuz, kod-tabanlı ön filtre — AI'ya gitmeden önceki kapı.
 *
 * Amaç maliyet: tarama başına yüzlerce içerik geliyor, hepsini modele göndermek
 * hem pahalı hem gereksiz. Buradaki kontroller saniyenin altında çalışır ve
 * adayların büyük kısmını eler; modele yalnızca gerçekten bakılmaya değer olan
 * içerik gider.
 */

export interface FilterResult {
  kept: RawItem[];
  dropped: { item: RawItem; reason: string }[];
}

/** Bu kelimeler geçmiyorsa içerik bizim alanımızda değil demektir. */
const TOPIC_HINTS = [
  // felsefe / stoacılık
  "stoic", "stoicism", "philosophy", "philosopher", "marcus aurelius", "seneca",
  "epictetus", "meditations", "nietzsche", "camus", "absurd", "virtue",
  // zihinsel dayanıklılık
  "discipline", "habit", "resilience", "mindset", "motivation", "consistency",
  "burnout", "procrastinat", "self-improvement", "journaling", "adversity",
  // geliştirici / uygulama
  "indie", "app", "ios", "swift", "launch", "side project", "app store",
  "solo dev", "react native", "expo",
];

const MAX_AGE_HOURS = 24;
const MIN_TEXT_LENGTH = 40;

function hasTopicOverlap(item: RawItem): boolean {
  const haystack = `${item.title ?? ""} ${item.text ?? ""}`.toLowerCase();
  return TOPIC_HINTS.some((hint) => haystack.includes(hint));
}

export async function applyFilters(items: RawItem[]): Promise<FilterResult> {
  const kept: RawItem[] = [];
  const dropped: { item: RawItem; reason: string }[] = [];

  // Daha önce analiz ettiklerimizi tek sorguda öğren (N+1 sorgudan kaçın).
  const ids = items.map((i) => i.externalId);
  const seen = new Set<string>();

  if (ids.length > 0) {
    const { data } = await db
      .from("opportunities")
      .select("external_id")
      .in("external_id", ids);
    for (const row of data ?? []) seen.add(row.external_id as string);
  }

  const now = Date.now();

  for (const item of items) {
    if (seen.has(item.externalId)) {
      dropped.push({ item, reason: "zaten analiz edilmiş" });
      continue;
    }
    if (item.nsfw) {
      dropped.push({ item, reason: "NSFW" });
      continue;
    }
    if (item.alreadyReplied) {
      dropped.push({ item, reason: "zaten yanıtlanmış" });
      continue;
    }

    const ageHours = (now - item.createdAt.getTime()) / 3_600_000;
    if (ageHours > MAX_AGE_HOURS) {
      dropped.push({ item, reason: `çok eski (${Math.round(ageHours)}s)` });
      continue;
    }

    const bodyLength = (item.title ?? "").length + (item.text ?? "").length;
    if (bodyLength < MIN_TEXT_LENGTH) {
      dropped.push({ item, reason: "içerik çok kısa" });
      continue;
    }

    // Yorum sayısı çoktan patlamışsa geç kaldık; yorumumuz dibe gömülür.
    if (item.numComments > 120) {
      dropped.push({ item, reason: "thread doymuş" });
      continue;
    }

    if (!hasTopicOverlap(item)) {
      dropped.push({ item, reason: "konu alakasız" });
      continue;
    }

    kept.push(item);
  }

  return { kept, dropped };
}

/**
 * Momentum: taze + hareketli thread'ler öne çıkar.
 *
 * Kodda hesaplanıyor, AI'a sorulmuyor — saat farkı ve oy sayısı nesnel veriler;
 * modele sormak hem para harcar hem tutarsız sonuç verir.
 */
export function momentumScore(item: RawItem): number {
  const ageHours = Math.max(
    0.25,
    (Date.now() - item.createdAt.getTime()) / 3_600_000
  );

  // Tazelik: ilk 2 saat tam puan, 24 saatte sıfıra iner.
  const freshness = Math.max(0, 1 - Math.max(0, ageHours - 2) / 22);

  // Hız: saatteki oy artışı. 10 oy/saat üstü tam puan sayılır.
  const velocity = Math.min(1, item.score / ageHours / 10);

  return Math.round((freshness * 0.65 + velocity * 0.35) * 100);
}
