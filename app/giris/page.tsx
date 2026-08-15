"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

export default function GirisPage() {
  const router = useRouter();
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");

    const res = await fetch("/api/giris", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });

    if (res.ok) {
      router.replace("/");
      router.refresh();
    } else {
      setError("Şifre yanlış.");
      setBusy(false);
    }
  }

  return (
    <main className="flex min-h-dvh items-center justify-center p-6">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4">
        <div className="text-center">
          <h1 className="font-mono text-3xl tracking-[0.3em] text-gold">EDITH</h1>
          <p className="mt-2 text-sm text-inkMuted">Reddit büyüme asistanı</p>
        </div>

        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="Şifre"
          autoComplete="current-password"
          // 16px: iOS Safari daha küçük punto'da alanı otomatik yakınlaştırır.
          className="w-full rounded-lg border border-border bg-surface px-4 py-3 text-base outline-none focus:border-gold"
        />

        {error && <p className="text-sm text-danger">{error}</p>}

        <button type="submit" disabled={busy} className="btn-primary w-full">
          {busy ? "Giriliyor…" : "Gir"}
        </button>
      </form>
    </main>
  );
}
