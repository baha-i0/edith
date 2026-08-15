import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { FlatCompat } from "@eslint/eslintrc";

// Kendi ESLint yapılandırması: bu klasör şu an ana uygulamanın reposu içinde
// duruyor ve yapılandırma olmazsa Expo projesinin flat config'ini devralıp
// çöküyor. Ayrı repoya taşındığında da olduğu gibi çalışır.
const compat = new FlatCompat({
  baseDirectory: dirname(fileURLToPath(import.meta.url)),
});

export default [
  { ignores: [".next/**", "node_modules/**"] },
  ...compat.extends("next/core-web-vitals", "next/typescript"),
];
