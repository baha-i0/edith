"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import type { Opportunity } from "@/lib/types";
import { OpportunityCard } from "./OpportunityCard";
import { DetailPanel } from "./DetailPanel";

/**
 * Fırsat akışı.
 *
 * Telefonda: tek kolon liste; bir karta dokununca detay tam ekran açılır ve
 * aksiyon butonları ekranın alt bandına sabitlenir (başparmak erişimi).
 * iPad'de: solda liste, sağda detay — aynı bileşenler, farklı yerleşim.
 */
export function Feed({ opportunities }: { opportunities: Opportunity[] }) {
  const router = useRouter();
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const selected = opportunities.find((o) => o.id === selectedId) ?? null;

  function afterAction() {
    setSelectedId(null);
    router.refresh();
  }

  if (opportunities.length === 0) {
    return (
      <div className="card text-center text-inkMuted">
        <p className="text-sm">Onay bekleyen fırsat yok.</p>
        <p className="mt-1 text-xs">
          Tarama her 3 saatte bir çalışıyor. Yeni fırsatlar burada belirir.
        </p>
      </div>
    );
  }

  return (
    <div className="md:grid md:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)] md:gap-4">
      <div className="space-y-3">
        <p className="text-xs uppercase tracking-wider text-inkMuted">
          🔥 {opportunities.length} önerilen aksiyon
        </p>

        {opportunities.map((opportunity) => (
          <OpportunityCard
            key={opportunity.id}
            opportunity={opportunity}
            selected={opportunity.id === selectedId}
            onSelect={() => setSelectedId(opportunity.id)}
          />
        ))}
      </div>

      {/* iPad ve üstü: sağ sütunda sabit detay paneli. */}
      <div className="hidden md:block">
        {selected ? (
          <div className="sticky top-20">
            <DetailPanel
              opportunity={selected}
              onDone={afterAction}
              onClose={() => setSelectedId(null)}
              variant="panel"
            />
          </div>
        ) : (
          <p className="card text-sm text-inkMuted">
            Detayı görmek için bir fırsat seç.
          </p>
        )}
      </div>

      {/* Telefon: tam ekran örtü. */}
      {selected && (
        <div className="fixed inset-0 z-30 overflow-y-auto bg-obsidian md:hidden">
          <DetailPanel
            opportunity={selected}
            onDone={afterAction}
            onClose={() => setSelectedId(null)}
            variant="fullscreen"
          />
        </div>
      )}
    </div>
  );
}
