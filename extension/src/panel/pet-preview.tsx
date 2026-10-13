import { useState } from "react";
import { createRoot } from "react-dom/client";
import { PolarisPet, type PetMood } from "./PolarisPet";
import "./styles.css";
import "./pet-preview.css";

function PetPreview() {
  const [mood, setMood] = useState<PetMood>("working");
  const states: Array<{ value: PetMood; label: string }> = [
    { value: "idle", label: "Dinleniyor" },
    { value: "working", label: "Çalışıyor" },
    { value: "listening", label: "Dinliyor" },
    { value: "speaking", label: "Konuşuyor" },
    { value: "waiting", label: "Onay bekliyor" },
  ];
  return (
    <main className="pet-preview-page">
      <p className="eyebrow">POLARIS · KÜÇÜK ÇALIŞMA ARKADAŞIN</p>
      <h1>Bir yıldızdan biraz fazlası.</h1>
      <p className="pet-preview-intro">
        Üzerine gel, ışıldasın. Tıkla, yıldız tozu bıraksın.
      </p>
      <section className="pet-preview-stage" aria-label="Etkileşimli maskot">
        <PolarisPet size={260} mood={mood} />
      </section>
      <div className="pet-preview-states" aria-label="Maskot durumları">
        {states.map((state) => (
          <button
            key={state.value}
            type="button"
            aria-pressed={mood === state.value}
            onClick={() => setMood(state.value)}
          >
            {state.label}
          </button>
        ))}
      </div>
      <p className="pet-preview-note">
        Çalışırken kuyruklu yıldız gibi süzülür, dinlerken dikkat kesilir.
        <br />
        Bu tasarım önizlemesi gerçek bir görev başlatmaz.
      </p>
    </main>
  );
}
createRoot(document.getElementById("root")!).render(<PetPreview />);
