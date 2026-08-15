"use client";

import { useState } from "react";
import type { AggressionLevel, Settings } from "@/lib/types";

const LEVELS: { value: AggressionLevel; label: string; hint: string }[] = [
  {
    value: "temkinli",
    label: "Temkinli",
    hint: "Ölçülü katkı, tartışmalı iddialardan kaçınır, şüphedeyse atlar.",
  },
  {
    value: "dengeli",
    label: "Dengeli",
    hint: "Net görüş belirtir ama tartışma açmaz. Önerilen başlangıç.",
  },
  {
    value: "atak",
    label: "Atak",
    hint: "İddialı, gerekirse popüler kanıya karşı çıkar. Hesap geçmişi oturunca geç.",
  },
];

export function SettingsForm({ initial }: { initial: Settings }) {
  const [level, setLevel] = useState<AggressionLevel>(initial.aggression_level);
  const [weeklyShare, setWeeklyShare] = useState(initial.max_app_share_per_week);
  const [dailyCap, setDailyCap] = useState(initial.max_comments_per_day);
  const [paused, setPaused] = useState(initial.paused);
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [error, setError] = useState("");

  async function save(patch: Record<string, unknown>) {
    setSaving(true);
    setError("");

    const res = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    });

    setSaving(false);

    if (!res.ok) {
      const body = (await res.json().catch(() => ({}))) as { error?: string };
      setError(body.error ?? "Kaydedilemedi");
      return;
    }

    setSavedAt(Date.now());
  }

  return (
    <div className="space-y-5">
      <div className="card space-y-3">
        <p className="text-xs uppercase tracking-wider text-inkMuted">Atak seviyesi</p>

        {LEVELS.map((item) => (
          <label
            key={item.value}
            className={`flex touch-target cursor-pointer items-start gap-3 rounded-lg border p-3 ${
              level === item.value ? "border-gold bg-surfaceRaised" : "border-border"
            }`}
          >
            <input
              type="radio"
              name="aggression"
              className="mt-1"
              checked={level === item.value}
              onChange={() => {
                setLevel(item.value);
                void save({ aggression_level: item.value });
              }}
            />
            <span>
              <span className="block text-sm font-medium">{item.label}</span>
              <span className="block text-xs text-inkMuted">{item.hint}</span>
            </span>
          </label>
        ))}
      </div>

      <div className="card space-y-4">
        <p className="text-xs uppercase tracking-wider text-inkMuted">Hız limitleri</p>

        <label className="block">
          <span className="text-sm">Topluluk başına haftalık tanıtım (APP_SHARE)</span>
          <input
            type="number"
            min={0}
            value={weeklyShare}
            onChange={(e) => setWeeklyShare(Number(e.target.value))}
            onBlur={() => void save({ max_app_share_per_week: weeklyShare })}
            className="mt-1 w-full touch-target rounded-lg border border-border bg-surfaceRaised px-3 text-base outline-none focus:border-gold"
          />
        </label>

        <label className="block">
          <span className="text-sm">Günlük toplam yayın tavanı</span>
          <input
            type="number"
            min={0}
            value={dailyCap}
            onChange={(e) => setDailyCap(Number(e.target.value))}
            onBlur={() => void save({ max_comments_per_day: dailyCap })}
            className="mt-1 w-full touch-target rounded-lg border border-border bg-surfaceRaised px-3 text-base outline-none focus:border-gold"
          />
        </label>
      </div>

      <div className="card flex items-center justify-between">
        <div>
          <p className="text-sm font-medium">Sistemi duraklat</p>
          <p className="text-xs text-inkMuted">
            Açıksa hiçbir onay Reddit&apos;e gönderilmez, tarama yine de çalışır.
          </p>
        </div>
        <button
          type="button"
          onClick={() => {
            const next = !paused;
            setPaused(next);
            void save({ paused: next });
          }}
          className={`touch-target rounded-full px-4 text-sm font-semibold ${
            paused ? "bg-danger text-ink" : "bg-surfaceRaised text-inkMuted"
          }`}
        >
          {paused ? "Duraklatıldı" : "Aktif"}
        </button>
      </div>

      <p className="text-xs text-inkMuted" aria-live="polite">
        {saving ? "Kaydediliyor…" : savedAt ? "Kaydedildi." : ""}
      </p>
      {error && <p className="text-sm text-danger">{error}</p>}
    </div>
  );
}
