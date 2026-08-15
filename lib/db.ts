import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import type { Settings } from "./types";

/**
 * Supabase istemcisi — service_role anahtarıyla, YALNIZCA sunucu tarafında.
 *
 * Bu dosya hiçbir client component'ten import edilmemeli: service_role anahtarı
 * RLS'i baypas eder, tarayıcıya sızarsa veritabanının tamamı açılır. Anahtar
 * `NEXT_PUBLIC_` öneki taşımadığı için Next.js onu istemci paketine koymaz.
 *
 * İstemci tembel kurulur (modül yüklenirken değil): `next build` sırasında
 * route'lar toplanırken bu modül import ediliyor ve o anda çalışma zamanı
 * sırları henüz tanımlı olmayabiliyor. Modül seviyesinde kurmak build'i
 * gereksiz yere düşürürdü.
 */
function required(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`Eksik ortam değişkeni: ${name}`);
  return value;
}

let client: SupabaseClient | null = null;

function getClient(): SupabaseClient {
  if (!client) {
    client = createClient(
      required("SUPABASE_URL"),
      required("SUPABASE_SERVICE_KEY"),
      { auth: { persistSession: false } }
    );
  }
  return client;
}

/**
 * `db.from(...)` çağrıları gerçek istemciye yönlendirilir; istemci ilk
 * kullanımda kurulur. Kullanım tarafı değişmez.
 */
export const db = new Proxy({} as SupabaseClient, {
  get(_target, prop, receiver) {
    return Reflect.get(getClient(), prop, receiver);
  },
});

export async function getSettings(): Promise<Settings> {
  const { data, error } = await db.from("settings").select("*").eq("id", 1).single();
  if (error) throw new Error(`Ayarlar okunamadı: ${error.message}`);
  return data as Settings;
}
