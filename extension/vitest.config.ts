import { defineConfig } from "vitest/config";
import { fileURLToPath } from "node:url";

export default defineConfig({
  resolve: {
    alias: {
      "virtual:panel-browser-tool": fileURLToPath(
        new URL("./src/panel/mocks/executeTool.mock.ts", import.meta.url),
      ),
    },
  },
  define: { __BROWSER_BRIDGE_READY__: "false" },
  esbuild: { jsx: "automatic" },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
    restoreMocks: true,
  },
});
