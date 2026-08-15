import { z } from "zod";
import { getProvider } from "./ai/provider";
import { buildMemoryBlock } from "./memory";
import { buildSkillBlock, skillsForAction } from "./skills";
import type { Action, AggressionLevel, Analysis, Community, RawItem } from "./types";

/**
 * Fırsat analizi ve taslak üretimi.
 *
 * Modelin çıktısı burada üç kapıdan geçer: şema doğrulaması (zod), uzunluk
 * bütçesi ve çekim gücü eşiği. Üçü de prompt'ta ayrıca isteniyor ama prompt bir
 * temenni — model bırakılırsa uzatır ve genel-geçer cümle kurar. Kapılar kodda.
 */

const AnalysisSchema = z.object({
  action: z.enum(["ENGAGE", "NETWORK", "APP_SHARE", "SKIP"]),
  relevance_score: z.number().int().min(0).max(100),
  network_score: z.number().int().min(0).max(100),
  promotion_score: z.number().int().min(0).max(100),
  spam_risk: z.number().int().min(0).max(100),
  community_fit: z.number().int().min(0).max(100),
  magnetism_score: z.number().int().min(0).max(100),
  reason: z.string().min(1),
  draft: z.string(),
  missing_info: z.array(z.string()).default([]),
});

export const COMMENT_WORD_MAX = 80;
export const POST_WORD_MAX = 150;
export const MAGNETISM_MIN = 60;

/** Taslağı anında zayıflatan dolgu kalıpları — çıkarsa yeniden yazdırılır. */
const FILLER_PATTERNS = [
  /^great question/i,
  /^this is such an? (important|interesting|great)/i,
  /^i love this (thread|post|question)/i,
  /hope this helps/i,
  /good luck on your journey/i,
  /just my two cents/i,
  /i'?m no expert,? but/i,
  /^i totally agree/i,
  /this resonates with me/i,
  /wishing you (all )?the best/i,
];

export function wordCount(text: string): number {
  return text.trim().split(/\s+/).filter(Boolean).length;
}

export function findFiller(text: string): string | null {
  for (const pattern of FILLER_PATTERNS) {
    const match = text.match(pattern);
    if (match) return match[0];
  }
  return null;
}

/** Atak seviyesinin üsluba yansıması. İzinlere DEĞİL, yalnızca tona dokunur. */
function aggressionGuidance(level: AggressionLevel): string {
  switch (level) {
    case "temkinli":
      return "Ölçülü ol. Tartışmalı iddialardan kaçın, katkı odaklı kal. Şüphedeysen SKIP.";
    case "atak":
      return "İddialı ol. Net bir görüş belirt, gerekirse popüler kanıya nazikçe karşı çık. Sıradan bir cevap yazmaktansa hiç yazma.";
    default:
      return "Dengeli ol: net bir görüş belirt ama tartışma açma.";
  }
}

interface AnalyzeInput {
  item: RawItem;
  community: Community;
  aggression: AggressionLevel;
}

const SYSTEM_PREAMBLE = `Sen EDITH'sin — Baha'nın Reddit büyüme asistanı. Görevin: verilen Reddit içeriğini değerlendirmek ve uygunsa Baha'nın ağzından bir yanıt taslağı yazmak.

Taslaklar İNGİLİZCE yazılır (Reddit kitlesi İngilizce). Analiz alanları (reason) Türkçe yazılır.

Yanıtını YALNIZCA şu şemada geçerli bir JSON nesnesi olarak ver:
{
  "action": "ENGAGE" | "NETWORK" | "APP_SHARE" | "SKIP",
  "relevance_score": 0-100,
  "network_score": 0-100,
  "promotion_score": 0-100,
  "spam_risk": 0-100,
  "community_fit": 0-100,
  "magnetism_score": 0-100,
  "reason": "Türkçe, tek cümle: neden bu aksiyon",
  "draft": "İngilizce yanıt metni. action SKIP ise boş string.",
  "missing_info": ["Baha hakkında bilmen gereken ama sana verilmemiş bilgiler"]
}

magnetism_score'u KENDİ taslağın için dürüstçe puanla: bu yorum birinin profilime tıklamasını sağlar mı? Sıradan bir onay cümlesiyse düşük puan ver.

Baha'ya ait sahip olmadığın hiçbir sayıyı veya deneyimi UYDURMA. İhtiyacın varsa missing_info'ya yaz ve draft'ı boş bırak.`;

export async function analyze(input: AnalyzeInput): Promise<Analysis> {
  const { item, community, aggression } = input;
  const provider = getProvider();

  const [memory, skills] = await Promise.all([
    buildMemoryBlock(),
    buildSkillBlock(skillsForAction(null)),
  ]);

  const system = [
    SYSTEM_PREAMBLE,
    aggressionGuidance(aggression),
    skills,
    memory,
  ]
    .filter(Boolean)
    .join("\n\n---\n\n");

  const user = buildUserPrompt(item, community);

  let analysis = await callAndValidate(provider, system, user);

  // SKIP ise taslak kalitesi konuşulmaz.
  if (analysis.action === "SKIP") return analysis;

  // Eksik bilgi varsa taslak üretilmemeli — patrona sorulacak.
  if (analysis.missing_info.length > 0) {
    return { ...analysis, draft: "" };
  }

  const maxWords = item.kind === "post" ? POST_WORD_MAX : COMMENT_WORD_MAX;
  const problems = collectProblems(analysis, maxWords);

  if (problems.length > 0) {
    // Tek bir düzeltme turu: modele nesi bozuk olduğunu söyleyip yeniden yazdır.
    const retryUser = `${user}

Önceki taslağın reddedildi. Sorunlar:
${problems.map((p) => `- ${p}`).join("\n")}

Önceki taslak:
"""${analysis.draft}"""

Aynı JSON şemasıyla yeniden yaz. Bu sefer ${maxWords} kelimeyi AŞMA ve dolgu kalıbı kullanma.`;

    try {
      const retried = await callAndValidate(provider, system, retryUser);
      if (collectProblems(retried, maxWords).length < problems.length) {
        analysis = retried;
      }
    } catch (err) {
      console.error("[scoring] yeniden yazdırma başarısız:", (err as Error).message);
    }
  }

  return analysis;
}

function collectProblems(analysis: Analysis, maxWords: number): string[] {
  const problems: string[] = [];

  const words = wordCount(analysis.draft);
  if (words > maxWords) {
    problems.push(`Taslak çok uzun: ${words} kelime, sınır ${maxWords}.`);
  }
  if (words === 0) {
    problems.push("Taslak boş.");
  }

  const filler = findFiller(analysis.draft);
  if (filler) {
    problems.push(`Yasak dolgu kalıbı kullanılmış: "${filler}".`);
  }

  if (analysis.magnetism_score < MAGNETISM_MIN) {
    problems.push(
      `Çekim gücü düşük (${analysis.magnetism_score}). Spesifik bir görüş, somut bir detay ve alıntılanabilir bir cümle gerekiyor.`
    );
  }

  return problems;
}

async function callAndValidate(
  provider: ReturnType<typeof getProvider>,
  system: string,
  user: string
): Promise<Analysis> {
  let lastError = "";

  // İki deneme: model bazen JSON'u markdown bloğuna sarar ya da alan atlar.
  for (let attempt = 0; attempt < 2; attempt++) {
    const raw = await provider.complete({ system, user, json: true, temperature: 0.8 });

    try {
      const parsed = AnalysisSchema.parse(JSON.parse(stripCodeFence(raw)));
      return parsed;
    } catch (err) {
      lastError = (err as Error).message;
    }
  }

  // Geçerli yapı alınamadıysa tahmin yürütmeyiz — fırsat atlanır.
  throw new Error(`AI geçerli JSON üretemedi: ${lastError}`);
}

function stripCodeFence(text: string): string {
  const trimmed = text.trim();
  if (!trimmed.startsWith("```")) return trimmed;
  return trimmed.replace(/^```(?:json)?\s*/i, "").replace(/```\s*$/, "");
}

function buildUserPrompt(item: RawItem, community: Community): string {
  return `## Değerlendirilecek içerik

Topluluk: r/${community.name} (kategori: ${community.category ?? "bilinmiyor"}, self-promo toleransı: ${community.self_promo_tolerance ?? "bilinmiyor"})
Başlık: ${item.title ?? "(yok)"}
Yazar: ${item.author ?? "bilinmiyor"}
Yaş: ${Math.round((Date.now() - item.createdAt.getTime()) / 3_600_000)} saat
Oy: ${item.score} · Yorum: ${item.numComments}

Metin:
"""
${(item.text ?? "").slice(0, 3000)}
"""

## Topluluk kuralları
${community.rules_summary?.slice(0, 2000) ?? "(kurallar okunamadı — güvenli tarafta kal)"}`;
}

export type { Action };
