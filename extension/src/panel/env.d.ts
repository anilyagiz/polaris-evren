/// <reference types="vite/client" />
declare const __BROWSER_BRIDGE_READY__: boolean;
declare module "virtual:panel-browser-tool" {
  export function executeBrowserTool(
    name: string,
    args: Record<string, unknown>,
    tabId?: number,
  ): Promise<{ ok: boolean; data: unknown | null; error: string | null }>;
}
