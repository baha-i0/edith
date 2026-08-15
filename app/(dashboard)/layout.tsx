import Link from "next/link";

// Yalnızca gerçekten çalışan sayfalar. /sohbet ve /hafiza sonraki fazlarda
// eklenecek — ölü link koymaktansa menüye hiç yazmıyoruz.
const NAV = [
  { href: "/", label: "Fırsatlar" },
  { href: "/gecmis", label: "Geçmiş" },
  { href: "/ayarlar", label: "Ayarlar" },
];

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="min-h-dvh">
      <header className="sticky top-0 z-20 border-b border-border bg-obsidian/95 backdrop-blur">
        <div className="mx-auto flex max-w-5xl items-center gap-4 px-4 py-3">
          <span className="font-mono text-sm tracking-[0.25em] text-gold">EDITH</span>
          <nav className="flex gap-1 overflow-x-auto">
            {NAV.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                className="touch-target hoverable flex items-center rounded-lg px-3 text-sm text-inkMuted"
              >
                {item.label}
              </Link>
            ))}
          </nav>
        </div>
      </header>

      <main className="mx-auto max-w-5xl px-4 py-4">{children}</main>
    </div>
  );
}
