import { db } from "./db";
import type { Fact, Lesson } from "./types";

/**
 * Kalıcı bellek — dersler (kurallar) ve olgular.
 *
 * ⚠️ Bu ayrım şema seviyesinde bilinçli: "Bu 6 ay sonra da doğru olacak mı?"
 * Evet ise KURAL (lessons), hayır ise OLGU (facts, tarih damgalı). Bu projede
 * bir kez tarihe bağlı bir bilgi kalıcı kural gibi saklandı, sessizce yanlışa
 * döndü ve aylarca karar yönlendirdi. Aynı hatayı tekrarlamamak için olguların
 * bir son kullanma tarihi var ve bayatlayan olgu prompt'a hiç girmez.
 */

/** YALNIZCA onaylanmış dersler döner — `proposed` olanlar prompt'a giremez. */
export async function getActiveLessons(): Promise<Lesson[]> {
  const { data, error } = await db
    .from("lessons")
    .select("*")
    .eq("status", "active")
    .order("created_at", { ascending: true });

  if (error) throw new Error(`Dersler okunamadı: ${error.message}`);
  return (data ?? []) as Lesson[];
}

/** Bayatlamamış olgular. `stale_after` geçmişse olgu görünmez olur. */
export async function getFreshFacts(): Promise<Fact[]> {
  const today = new Date().toISOString().slice(0, 10);
  const { data, error } = await db
    .from("facts")
    .select("*")
    .or(`stale_after.is.null,stale_after.gte.${today}`)
    .order("as_of", { ascending: false });

  if (error) throw new Error(`Olgular okunamadı: ${error.message}`);
  return (data ?? []) as Fact[];
}

/** Belleği prompt'a girecek düz metne çevirir. */
export async function buildMemoryBlock(): Promise<string> {
  const [lessons, facts] = await Promise.all([getActiveLessons(), getFreshFacts()]);

  const parts: string[] = [];

  if (lessons.length > 0) {
    parts.push(
      "## Öğrenilmiş kurallar (bunlara UYULACAK)\n" +
        lessons.map((l) => `- ${l.text}`).join("\n")
    );
  }

  if (facts.length > 0) {
    parts.push(
      "## Güncel olgular (tarih damgalı — daha yenisini biliyorsan ona güven)\n" +
        facts.map((f) => `- [${f.as_of}] ${f.text}`).join("\n")
    );
  }

  return parts.join("\n\n");
}

/** Bir dersin kaç kez uygulandığını sayar (hangi kuralın işe yaradığını görmek için). */
export async function markLessonsApplied(lessonIds: string[]): Promise<void> {
  if (lessonIds.length === 0) return;
  await db.rpc("increment_lessons_applied", { ids: lessonIds }).then(
    () => undefined,
    // RPC tanımlı değilse sayaç kritik değil — sessizce geç.
    () => undefined
  );
}

/** Sohbetten ya da otomatik çıkarımdan gelen ders önerisi. Onaya düşer. */
export async function proposeLesson(input: {
  text: string;
  category?: string;
  source: "boss" | "ai_inference";
  evidence?: string;
}): Promise<void> {
  // Patronun doğrudan söylediği kural yine de onaya düşer: sohbette geçen bir
  // cümleyi yanlış anlayıp kalıcı kural yapmak, hiç öğrenmemekten kötüdür.
  const { error } = await db.from("lessons").insert({
    text: input.text,
    category: input.category ?? null,
    source: input.source,
    status: "proposed",
    evidence: input.evidence ?? null,
  });

  if (error) throw new Error(`Ders kaydedilemedi: ${error.message}`);
}
