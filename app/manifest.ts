import type { MetadataRoute } from "next";

/** Ana ekrana eklenince uygulama gibi açılsın diye. */
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "EDITH",
    short_name: "EDITH",
    description: "Reddit Growth & Networking Assistant",
    start_url: "/",
    display: "standalone",
    background_color: "#0B0B0D",
    theme_color: "#0B0B0D",
    icons: [
      { src: "/icon.png", sizes: "512x512", type: "image/png", purpose: "any" },
    ],
  };
}
