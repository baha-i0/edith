import type { Config } from "tailwindcss";

// Dark academia + altın — Zihinsel Güç'ün görsel kimliğiyle aynı dil.
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        obsidian: "#0B0B0D",
        surface: "#141417",
        surfaceRaised: "#1C1C21",
        border: "#2A2A31",
        gold: "#C9A227",
        goldSoft: "#E3C766",
        ink: "#EDEAE3",
        inkMuted: "#9C978C",
        danger: "#C4573F",
        success: "#5C8A5C",
      },
      fontFamily: {
        // Sistem fontları — dashboard bir araç, marka yüzeyi değil.
        sans: ["-apple-system", "BlinkMacSystemFont", "Segoe UI", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      spacing: {
        // Alt aksiyon bandı + iOS safe area
        "safe-bottom": "env(safe-area-inset-bottom)",
      },
    },
  },
  plugins: [],
};

export default config;
