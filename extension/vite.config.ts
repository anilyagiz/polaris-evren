import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { crx } from "@crxjs/vite-plugin";
import { fileURLToPath } from "node:url";
import manifest from "./manifest.json";

// The panel imports shared/fixtures (one level above this package) for mock mode.
const repoRoot = fileURLToPath(new URL("..", import.meta.url));

export default defineConfig({
  plugins: [react(), crx({ manifest })],
  server: { fs: { allow: [repoRoot] } },
  build: {
    rollupOptions: {
      input: {
        permission: "src/panel/permission.html",
        pet: "src/panel/pet-preview.html",
        mascot: "src/panel/mascot.html",
      },
    },
  },
});
