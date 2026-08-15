import { readFile } from "node:fs/promises";
import { join } from "node:path";
import type { Action } from "./types";

/**
 * Skill yükleyici.
 *
 * Skill'ler markdown dosyaları; hepsini her prompt'a koymak token israfı olur,
 * o yüzden işe göre seçilirler. `kimlik` ve `cekim-gucu` her taslakta yüklenir —
 * biri kimin adına yazdığımızı, diğeri nasıl yazdığımızı tanımlar; ikisi de
 * pazarlık konusu değil.
 */

const SKILLS_DIR = join(process.cwd(), "skills");

export type SkillName =
  | "kimlik"
  | "cekim-gucu"
  | "reddit-etiketi"
  | "zihinsel-guc-marka"
  | "urun-gercekleri"
  | "dijital-pazarlama";

const cache = new Map<SkillName, string>();

export async function loadSkill(name: SkillName): Promise<string> {
  const cached = cache.get(name);
  if (cached) return cached;

  const content = await readFile(join(SKILLS_DIR, `${name}.md`), "utf8");
  cache.set(name, content);
  return content;
}

/** Bir taslak üretimi için hangi skill'ler gerekiyor. */
export function skillsForAction(action: Action | null): SkillName[] {
  const base: SkillName[] = ["kimlik", "cekim-gucu", "reddit-etiketi"];

  // Uygulamadan söz edilecekse ürün gerçekleri ve marka tonu şart: uydurulmuş
  // bir özellik ya da yanlış ton, tanıtımın kendisinden daha çok zarar verir.
  if (action === "APP_SHARE") {
    return [...base, "urun-gercekleri", "zihinsel-guc-marka"];
  }
  return base;
}

export async function buildSkillBlock(names: SkillName[]): Promise<string> {
  const loaded = await Promise.all(names.map((n) => loadSkill(n)));
  return loaded.join("\n\n---\n\n");
}

/** Sohbet için: strateji soruları pazarlama bilgisini de ister. */
export async function buildChatSkillBlock(): Promise<string> {
  return buildSkillBlock([
    "kimlik",
    "cekim-gucu",
    "reddit-etiketi",
    "dijital-pazarlama",
    "urun-gercekleri",
  ]);
}
