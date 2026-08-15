import { db } from "@/lib/db";
import type { Opportunity } from "@/lib/types";
import { Feed } from "@/components/Feed";

// Onay kuyruğu her zaman taze görünmeli — önbellekten servis edilmez.
export const dynamic = "force-dynamic";

export default async function FeedPage() {
  const { data, error } = await db
    .from("opportunities")
    .select("*")
    .eq("status", "pending")
    // Sıralama bilinçli: önce çekim gücü, sonra momentum. Kaçırılmaması gereken
    // fırsat "iyi yazılabilecek olan taze thread"; ham alaka tek başına yeterli değil.
    .order("magnetism_score", { ascending: false, nullsFirst: false })
    .order("momentum_score", { ascending: false, nullsFirst: false })
    .limit(50);

  if (error) {
    return (
      <p className="card text-danger">
        Fırsatlar okunamadı: {error.message}
      </p>
    );
  }

  return <Feed opportunities={(data ?? []) as Opportunity[]} />;
}
