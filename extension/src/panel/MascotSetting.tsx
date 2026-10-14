import { useEffect, useState } from "react";
import { normalizeBackend } from "./protocol";

/** "Masaüstü maskotu" switch: the tray app shows/hides the desktop star. */
export function MascotSetting({ backend }: { backend: string }) {
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const response = await fetch(`${normalizeBackend(backend)}/settings/mascot`, {
          cache: "no-store",
        });
        if (!response.ok) throw new Error(String(response.status));
        const data = (await response.json()) as { enabled?: unknown };
        if (alive) setEnabled(data.enabled !== false);
      } catch {
        if (alive) setEnabled(null);
      }
    })();
    return () => {
      alive = false;
    };
  }, [backend]);

  if (enabled === null) return null; // older server or offline: hide the row

  const change = async (next: boolean) => {
    setBusy(true);
    setError("");
    try {
      const response = await fetch(`${normalizeBackend(backend)}/settings/mascot`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: next }),
      });
      if (!response.ok) throw new Error(String(response.status));
      const data = (await response.json()) as { enabled?: unknown };
      setEnabled(data.enabled !== false);
    } catch {
      setError("Ayar kaydedilemedi; Polaris çalışıyor mu?");
    } finally {
      setBusy(false);
    }
  };

  return (
    <label className="setting-row">
      <span>
        <strong>Masaüstü maskotu</strong>
        <small>
          {enabled
            ? "Yıldız ekranın köşesinde durur; tıklayıp görev verebilirsin."
            : "Kapalı. Açınca birkaç saniye içinde ekranın köşesinde belirir."}
        </small>
        {error && <small role="alert">{error}</small>}
      </span>
      <input
        type="checkbox"
        role="switch"
        className="switch"
        aria-label="Masaüstü maskotu"
        checked={enabled}
        disabled={busy}
        onChange={(event) => void change(event.target.checked)}
      />
    </label>
  );
}
