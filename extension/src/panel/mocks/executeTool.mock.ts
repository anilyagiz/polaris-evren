// Used only for UI demos; live mode never reports a fake successful action.
export async function executeBrowserTool(
  _name: string,
  _args: Record<string, unknown>,
  _tabId?: number,
): Promise<{ ok: boolean; data: unknown | null; error: string | null }> {
  return {
    ok: false,
    data: null,
    error: "Kişi 2’nin tarayıcı köprüsü henüz hazır değil.",
  };
}
