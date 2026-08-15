import { db } from "@/lib/db";
import type { Opportunity } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function GecmisPage() {
  const { data, error } = await db
    .from("opportunities")
    .select("*")
    .in("status", ["posted", "failed"])
    .order("posted_at", { ascending: false, nullsFirst: false })
    .limit(50);

  if (error) {
    return <p className="card text-danger">Geçmiş okunamadı: {error.message}</p>;
  }

  const rows = (data ?? []) as Opportunity[];

  if (rows.length === 0) {
    return (
      <p className="card text-sm text-inkMuted">
        Henüz yayınlanmış içerik yok.
      </p>
    );
  }

  return (
    <div className="space-y-3">
      <p className="text-xs uppercase tracking-wider text-inkMuted">
        Son {rows.length} yayın
      </p>

      {rows.map((o) => (
        <div key={o.id} className="card">
          <div className="flex items-center gap-2 text-xs text-inkMuted">
            <span className="font-mono text-goldSoft">r/{o.community}</span>
            <span>·</span>
            <span>{o.final_action}</span>
            {o.status === "failed" && (
              <span className="badge bg-danger/15 text-danger">başarısız</span>
            )}
          </div>

          <p className="mt-2 line-clamp-1 text-sm text-inkMuted">
            {o.source_title}
          </p>

          <p className="mt-2 whitespace-pre-wrap text-sm">{o.ai_draft}</p>

          {o.error_message && (
            <p className="mt-2 text-xs text-danger">{o.error_message}</p>
          )}

          {o.posted_url && (
            <a
              href={o.posted_url}
              target="_blank"
              rel="noreferrer"
              className="mt-2 inline-block text-xs text-goldSoft underline"
            >
              Reddit&apos;te gör ↗
            </a>
          )}
        </div>
      ))}
    </div>
  );
}
