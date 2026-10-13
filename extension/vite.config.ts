import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { crx } from "@crxjs/vite-plugin";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import manifest from "./manifest.json";

// K2 owns the real bridge. A rebuild picks it up as soon as the file exists.
const bridge = fileURLToPath(
  new URL("./src/bridge/executeTool.ts", import.meta.url),
);
const fallback = fileURLToPath(
  new URL("./src/panel/mocks/executeTool.mock.ts", import.meta.url),
);

export default defineConfig({
  plugins: [react(), crx({ manifest })],
  resolve: {
    alias: {
      "virtual:panel-browser-tool": existsSync(bridge) ? bridge : fallback,
    },
  },
  define: { __BROWSER_BRIDGE_READY__: JSON.stringify(existsSync(bridge)) },
  build: {
    rollupOptions: { input: { permission: "src/panel/permission.html" } },
  },
});
