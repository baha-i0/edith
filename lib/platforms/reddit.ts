import type { RawItem } from "../types";
import type { Outcome, PlatformClient, PublishTarget } from "./platform";

/**
 * Reddit istemcisi — resmi OAuth API'si üzerinden, doğrudan `fetch` ile.
 *
 * Neden hazır kütüphane (snoowrap) değil: snoowrap 2021'den beri bakımsız ve
 * modern Node sürümlerinde sorun çıkarıyor. İhtiyacımız olan yüzey küçük
 * (listeleme, kurallar, yorum, gönderi, durum) — kendi istemcimiz hem güncel
 * kalır hem bir bağımlılık eksilir.
 */

const OAUTH = "https://oauth.reddit.com";
const WWW = "https://www.reddit.com";

function env(name: string): string {
  const v = process.env[name];
  if (!v) throw new Error(`Eksik ortam değişkeni: ${name}`);
  return v;
}

let cachedToken: { value: string; expiresAt: number } | null = null;

async function getToken(): Promise<string> {
  if (cachedToken && Date.now() < cachedToken.expiresAt - 60_000) {
    return cachedToken.value;
  }

  const basic = Buffer.from(
    `${env("REDDIT_CLIENT_ID")}:${env("REDDIT_CLIENT_SECRET")}`
  ).toString("base64");

  const res = await fetch(`${WWW}/api/v1/access_token`, {
    method: "POST",
    headers: {
      Authorization: `Basic ${basic}`,
      "Content-Type": "application/x-www-form-urlencoded",
      "User-Agent": env("REDDIT_USER_AGENT"),
    },
    body: new URLSearchParams({
      grant_type: "password",
      username: env("REDDIT_USERNAME"),
      password: env("REDDIT_PASSWORD"),
    }),
  });

  if (!res.ok) {
    throw new Error(`Reddit token alınamadı (${res.status}): ${await res.text()}`);
  }

  const json = (await res.json()) as { access_token: string; expires_in: number };
  cachedToken = {
    value: json.access_token,
    expiresAt: Date.now() + json.expires_in * 1000,
  };
  return cachedToken.value;
}

/** Reddit OAuth kotası dakikada 100 istek; aramıza küçük bir nefes koyuyoruz. */
let lastCall = 0;
async function throttle() {
  const gap = Date.now() - lastCall;
  if (gap < 700) await new Promise((r) => setTimeout(r, 700 - gap));
  lastCall = Date.now();
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  await throttle();
  const token = await getToken();
  const res = await fetch(`${OAUTH}${path}`, {
    ...init,
    headers: {
      ...init?.headers,
      Authorization: `Bearer ${token}`,
      "User-Agent": env("REDDIT_USER_AGENT"),
    },
  });

  if (!res.ok) {
    throw new Error(`Reddit API ${path} başarısız (${res.status}): ${await res.text()}`);
  }
  return (await res.json()) as T;
}

interface ListingChild {
  kind: string;
  data: {
    id: string;
    name: string;
    title?: string;
    selftext?: string;
    body?: string;
    author?: string;
    permalink: string;
    created_utc: number;
    score: number;
    num_comments?: number;
    over_18?: boolean;
    removed_by_category?: string | null;
    subreddit: string;
  };
}

interface Listing {
  data: { children: ListingChild[] };
}

export class RedditClient implements PlatformClient {
  readonly name = "reddit";

  /**
   * Hedef topluluklarda taze gönderileri listeler.
   *
   * Bilinçli olarak `/new` kullanılıyor: momentum'un tamamı zamanlamada ve bir
   * thread'e ilk saatlerde girmek görünürlüğü kat kat artırıyor. `/hot` zaten
   * doymuş, yüzlerce yorumlu thread'leri getirir — oraya yazılan yorum görülmez.
   */
  async discover(communities: string[], limit = 25): Promise<RawItem[]> {
    const items: RawItem[] = [];

    for (const community of communities) {
      try {
        const listing = await api<Listing>(
          `/r/${encodeURIComponent(community)}/new?limit=${limit}`
        );

        for (const child of listing.data.children) {
          const d = child.data;
          items.push({
            platform: "reddit",
            community,
            kind: "post",
            externalId: d.name, // t3_xxxxx
            url: `https://reddit.com${d.permalink}`,
            title: d.title,
            text: d.selftext ?? "",
            author: d.author,
            createdAt: new Date(d.created_utc * 1000),
            score: d.score,
            numComments: d.num_comments ?? 0,
            nsfw: Boolean(d.over_18),
          });
        }
      } catch (err) {
        // Tek bir topluluk (özel/banlı/silinmiş) tüm taramayı düşürmemeli.
        console.error(`[reddit] r/${community} taranamadı:`, (err as Error).message);
      }
    }

    return items;
  }

  async fetchRules(community: string): Promise<unknown> {
    return api(`/r/${encodeURIComponent(community)}/about/rules`);
  }

  async publish(target: PublishTarget): Promise<string> {
    if (target.kind === "comment") {
      if (!target.parentId) throw new Error("Yorum için parentId gerekli");

      const json = await api<{
        json: { errors: string[][]; data?: { things: { data: { permalink?: string; id: string } }[] } };
      }>("/api/comment", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          api_type: "json",
          thing_id: target.parentId,
          text: target.body,
        }),
      });

      if (json.json.errors?.length) {
        throw new Error(`Reddit yorumu reddetti: ${JSON.stringify(json.json.errors)}`);
      }

      const thing = json.json.data?.things?.[0]?.data;
      return thing?.permalink
        ? `https://reddit.com${thing.permalink}`
        : `https://reddit.com/comments/${thing?.id ?? "bilinmiyor"}`;
    }

    if (!target.community || !target.title) {
      throw new Error("Gönderi için community ve title gerekli");
    }

    const json = await api<{ json: { errors: string[][]; data?: { url?: string } } }>(
      "/api/submit",
      {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          api_type: "json",
          sr: target.community,
          kind: "self",
          title: target.title,
          text: target.body,
        }),
      }
    );

    if (json.json.errors?.length) {
      throw new Error(`Reddit gönderiyi reddetti: ${JSON.stringify(json.json.errors)}`);
    }
    return json.json.data?.url ?? "";
  }

  /**
   * Yayınlanan içeriğin akıbeti. `removed` en güçlü negatif öğrenme sinyalidir:
   * mod silmişse o taslak yaklaşımı bir daha tekrarlanmamalı.
   */
  async checkOutcome(externalId: string): Promise<Outcome | null> {
    const listing = await api<Listing>(`/api/info?id=${encodeURIComponent(externalId)}`);
    const d = listing.data.children[0]?.data;
    if (!d) return null;

    return {
      score: d.score,
      replies: d.num_comments ?? 0,
      removed: Boolean(d.removed_by_category),
    };
  }
}

export const reddit = new RedditClient();
