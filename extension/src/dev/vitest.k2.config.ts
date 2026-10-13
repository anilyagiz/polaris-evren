/**
 * Vitest config for the K2 (browser computer-use) layer.
 *
 * The repository root `vitest.config.ts` belongs to K1 and only includes
 * `src/panel/**`, so the content-script and bridge tests would never run under
 * it. This config is the K2 counterpart:
 *
 *     npx vitest run --config src/dev/vitest.k2.config.ts
 *
 * `environment: "jsdom"` is required: every file under test touches `document`,
 * `window` or `chrome`. jsdom does NOT do layout, so the snapshot tests stub
 * `getBoundingClientRect`, `checkVisibility` and `innerText` themselves — see
 * the header of `src/content/snapshot.test.ts`.
 */

import { defineConfig } from "vitest/config";
import { fileURLToPath } from "node:url";

export default defineConfig({
  // Resolve relative to the extension package, not to the caller's cwd.
  root: fileURLToPath(new URL("../..", import.meta.url)),
  test: {
    environment: "jsdom",
    include: ["src/content/**/*.test.ts", "src/bridge/**/*.test.ts"],
    restoreMocks: true,
  },
});
