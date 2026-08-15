import type { RawItem } from "../types";

/**
 * Platform arayüzü — Reddit ilk implementasyon, tek olan değil.
 *
 * Instagram/Facebook/TikTok eklendiğinde `discover` çoğu platformda boş dizi
 * döner: Meta ve TikTok API'leri başkalarının içeriğinde arama/yorum İZNİ
 * VERMEZ, yalnızca kendi hesabına yayın ve kendi gönderinin yorumlarına yanıt
 * sunar. Arayüz bunu destekliyor — `discover`ı olmayan bir platform yalnızca
 * `publish` + `monitor` uygular, sistemin geri kalanı aynı çalışır.
 */
export interface PlatformClient {
  readonly name: string;

  /** Hedef topluluklarda yeni fırsat adayları bul. */
  discover(communities: string[], limit: number): Promise<RawItem[]>;

  /** Topluluk kurallarını çek (kurallar motoru için). */
  fetchRules(community: string): Promise<unknown>;

  /** Onaylanmış içeriği yayınla. Yayınlanan içeriğin URL'sini döner. */
  publish(target: PublishTarget): Promise<string>;

  /** Yayınlanmış bir içeriğin güncel durumunu oku (upvote, yanıt, silinme). */
  checkOutcome(externalId: string): Promise<Outcome | null>;
}

export interface PublishTarget {
  kind: "comment" | "post";
  /** comment için: yanıtlanacak içeriğin id'si. post için: topluluk adı. */
  parentId?: string;
  community?: string;
  title?: string;
  body: string;
}

export interface Outcome {
  score: number;
  replies: number;
  removed: boolean;
}
