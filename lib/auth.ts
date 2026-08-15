/**
 * Tek kullanıcılı oturum — imzalı çerez.
 *
 * Web Crypto kullanılıyor (node:crypto değil): aynı kod hem Next.js
 * middleware'inin edge çalışma zamanında hem sunucu route'larında çalışsın diye.
 */

const COOKIE_NAME = "edith_session";
const MAX_AGE_SECONDS = 60 * 60 * 24 * 30; // 30 gün

function secret(): string {
  const value = process.env.SESSION_SECRET;
  if (!value) throw new Error("Eksik ortam değişkeni: SESSION_SECRET");
  return value;
}

async function hmac(message: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret()),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const signature = await crypto.subtle.sign(
    "HMAC",
    key,
    new TextEncoder().encode(message)
  );
  return Array.from(new Uint8Array(signature))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

/** Sabit süreli karşılaştırma — imza tahmininde zamanlama sızıntısı olmasın. */
function safeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

export async function createSessionToken(): Promise<string> {
  const expires = Date.now() + MAX_AGE_SECONDS * 1000;
  const payload = String(expires);
  return `${payload}.${await hmac(payload)}`;
}

export async function verifySessionToken(token: string | undefined): Promise<boolean> {
  if (!token) return false;

  const [payload, signature] = token.split(".");
  if (!payload || !signature) return false;

  const expected = await hmac(payload);
  if (!safeEqual(signature, expected)) return false;

  return Number(payload) > Date.now();
}

export function checkPassword(input: string): boolean {
  const expected = process.env.DASHBOARD_PASSWORD;
  if (!expected) throw new Error("Eksik ortam değişkeni: DASHBOARD_PASSWORD");
  return safeEqual(input, expected);
}

export const SESSION_COOKIE = COOKIE_NAME;
export const SESSION_MAX_AGE = MAX_AGE_SECONDS;
