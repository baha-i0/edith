import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "EDITH",
  description: "Reddit Growth & Networking Assistant — Zihinsel Güç",
  manifest: "/manifest.webmanifest",
  appleWebApp: {
    capable: true,
    title: "EDITH",
    statusBarStyle: "black-translucent",
  },
};

export const viewport: Viewport = {
  themeColor: "#0B0B0D",
  width: "device-width",
  initialScale: 1,
  // Kullanıcı yakınlaştırmasını engellemiyoruz: erişilebilirlik için gerekli.
  viewportFit: "cover",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="tr">
      <body>{children}</body>
    </html>
  );
}
