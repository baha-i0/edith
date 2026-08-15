"use client";

import { useState } from "react";
import type { Opportunity } from "@/lib/types";

const COMMENT_WORD_MAX = 80;
const POST_WORD_MAX = 150;

function countWords(text: string): number {
  return text.trim().split(/\s+/).filter(Boolean).length;
}

export function DetailPanel({
  opportunity: o,
  onDone,
  onClose,
  variant,
}: {
  opportunity: Opportunity;
  onDone: () => void;
  onClose: () => void;
  variant: "panel" | "fullscreen";
}) {
  const [draft, setDraft] = useState(o.ai_draft ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // APP_SHARE geri alınamaz ve en riskli aksiyon — yanlışlıkla dokunmaya karşı
  // ikinci bir onay ister.
  const [confirming, setConfirming] = useState(false);

  const action = o.final_action ?? o.ai_action ?? "SKIP";
  const maxWords = o.kind === "post" ? POST_WORD_MAX : COMMENT_WORD_MAX;
  const words = countWords(draft);
  const overBudget = words > maxWords;
  const blockedByMissingInfo = Boolean(o.missing_info?.length);

  async function send(path: "approve" | "skip") {
    setBusy(true);
    setError("");

    const res = await fetch(`/api/opportunities/${o.id}/${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ draft }),
    });

    if (res.ok) {
      onDone();
      return;
    }

    const body = (await res.json().catch(() => ({}))) as { error?: string };
    setError(body.error ?? "İşlem başarısız.");
    setBusy(false);
    setConfirming(false);
  }

  function onApproveTap() {
    if (action === "APP_SHARE" && !confirming) {
      setConfirming(true);
      return;
    }
    void send("approve");
  }

  return (
    <div className={variant === "fullscreen" ? "flex min-h-dvh flex-col" : ""}>
      <div
        className={
          variant === "fullscreen"
            ? "flex-1 space-y-4 p-4 pb-40"
            : "card space-y-4"
        }
      >
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="font-mono text-xs text-goldSoft">r/{o.community}</p>
            <h2 className="mt-1 text-base font-semibold">
              {o.source_title ?? "(başlıksız)"}
            </h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="btn-ghost shrink-0 px-3"
            aria-label="Kapat"
          >
            ✕
          </button>
        </div>

        <a
          href={o.source_url}
          target="_blank"
          rel="noreferrer"
          className="block text-xs text-goldSoft underline"
        >
          Reddit&apos;te aç ↗
        </a>

        {o.source_text && (
          <p className="max-h-48 overflow-y-auto whitespace-pre-wrap rounded-lg bg-surfaceRaised p-3 text-sm text-inkMuted">
            {o.source_text}
          </p>
        )}

        {o.ai_reason && (
          <div>
            <p className="text-xs uppercase tracking-wider text-inkMuted">
              EDITH&apos;in gerekçesi
            </p>
            <p className="mt-1 text-sm">{o.ai_reason}</p>
          </div>
        )}

        {o.blocked_reason && (
          <p className="rounded-lg border border-danger/40 bg-danger/10 p-3 text-sm text-danger">
            🔒 {o.blocked_reason}
          </p>
        )}

        {blockedByMissingInfo && (
          <div className="rounded-lg border border-goldSoft/40 bg-goldSoft/10 p-3 text-sm">
            <p className="font-medium text-goldSoft">
              EDITH bu bilgileri bilmiyor ve uydurmadı:
            </p>
            <ul className="mt-1 list-inside list-disc text-inkMuted">
              {o.missing_info?.map((info) => (
                <li key={info}>{info}</li>
              ))}
            </ul>
            <p className="mt-2 text-xs text-inkMuted">
              Taslağı sen yazarsan onaylayabilirsin.
            </p>
          </div>
        )}

        <div>
          <div className="flex items-center justify-between">
            <label htmlFor="draft" className="text-xs uppercase tracking-wider text-inkMuted">
              Taslak
            </label>
            <span
              className={`font-mono text-xs ${
                overBudget ? "text-danger" : "text-inkMuted"
              }`}
            >
              {words}/{maxWords} kelime
            </span>
          </div>

          <textarea
            id="draft"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            rows={variant === "fullscreen" ? 10 : 8}
            // 16px taban punto: iOS Safari daha küçüğünde alanı yakınlaştırır.
            className="mt-2 w-full rounded-lg border border-border bg-surfaceRaised p-3 text-base outline-none focus:border-gold"
            placeholder="Taslak metni…"
          />

          {overBudget && (
            <p className="mt-1 text-xs text-danger">
              Bütçe aşıldı. Uzun yorum Reddit&apos;te okunmaz — kısalt.
            </p>
          )}
        </div>

        {error && <p className="text-sm text-danger">{error}</p>}
      </div>

      {/* Aksiyon bandı: telefonda ekranın altına sabit, başparmak erişiminde.
          Klavye açıkken de görünür kalsın diye fixed. */}
      <div
        className={
          variant === "fullscreen"
            ? "fixed inset-x-0 bottom-0 z-40 border-t border-border bg-obsidian/95 p-4 pb-[calc(1rem+env(safe-area-inset-bottom))] backdrop-blur"
            : "mt-3 flex gap-2"
        }
      >
        <div className={variant === "fullscreen" ? "flex gap-2" : "contents"}>
          <button
            type="button"
            onClick={() => void send("skip")}
            disabled={busy}
            className="btn-ghost flex-1"
          >
            Atla
          </button>

          <button
            type="button"
            onClick={onApproveTap}
            disabled={busy || !draft.trim() || overBudget}
            className={`flex-1 ${confirming ? "btn bg-danger text-ink" : "btn-primary"}`}
          >
            {busy
              ? "Gönderiliyor…"
              : confirming
                ? "Emin misin? Yayınla"
                : "Onayla ve yayınla"}
          </button>
        </div>
      </div>
    </div>
  );
}
