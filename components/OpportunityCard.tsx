"use client";

import type { Action, Opportunity } from "@/lib/types";

const ACTION_STYLE: Record<Action, string> = {
  ENGAGE: "bg-surfaceRaised text-ink",
  NETWORK: "bg-surfaceRaised text-goldSoft",
  APP_SHARE: "bg-gold text-obsidian",
  SKIP: "bg-surfaceRaised text-inkMuted",
};

function hoursAgo(iso: string | null): string {
  if (!iso) return "";
  const hours = (Date.now() - new Date(iso).getTime()) / 3_600_000;
  if (hours < 1) return `${Math.round(hours * 60)} dk önce`;
  return `${Math.round(hours)} saat önce`;
}

export function OpportunityCard({
  opportunity: o,
  selected,
  onSelect,
}: {
  opportunity: Opportunity;
  selected: boolean;
  onSelect: () => void;
}) {
  const action = o.final_action ?? o.ai_action ?? "SKIP";
  const hot = (o.momentum_score ?? 0) >= 70;

  return (
    <button
      type="button"
      onClick={onSelect}
      className={`card hoverable w-full text-left ${
        selected ? "border-gold" : ""
      }`}
    >
      <div className="flex items-center gap-2 text-xs text-inkMuted">
        <span className="font-mono text-goldSoft">r/{o.community}</span>
        <span>·</span>
        <span>{hoursAgo(o.source_created_at)}</span>
        {hot && <span className="text-gold">· ⚡ yükselişte</span>}
      </div>

      <p className="mt-2 line-clamp-2 text-sm font-medium text-ink">
        {o.source_title ?? "(başlıksız)"}
      </p>

      {/* Skorlar tek satırda sıkışık rozet — hover'a gizlenmiş bilgi yok,
          dokunmatikte hover diye bir şey olmadığı için her şey görünür. */}
      <div className="mt-3 flex flex-wrap items-center gap-2 font-mono text-xs">
        <span className="badge bg-surfaceRaised text-inkMuted">
          alaka {o.relevance_score ?? "–"}
        </span>
        <span className="badge bg-surfaceRaised text-inkMuted">
          ağ {o.network_score ?? "–"}
        </span>
        <span className="badge bg-surfaceRaised text-goldSoft">
          ⚡çekim {o.magnetism_score ?? "–"}
        </span>
        <span
          className={`badge ${
            (o.spam_risk ?? 0) > 40
              ? "bg-danger/20 text-danger"
              : "bg-surfaceRaised text-inkMuted"
          }`}
        >
          risk {o.spam_risk ?? "–"}
        </span>
      </div>

      <div className="mt-3 flex items-center gap-2">
        <span className={`badge ${ACTION_STYLE[action]}`}>{action}</span>

        {o.blocked_reason && (
          <span className="badge bg-danger/15 text-danger">kural uyarısı</span>
        )}
        {o.missing_info && o.missing_info.length > 0 && (
          <span className="badge bg-goldSoft/15 text-goldSoft">eksik bilgi</span>
        )}
      </div>
    </button>
  );
}
