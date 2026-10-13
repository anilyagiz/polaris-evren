import { useId } from "react";

export type PetMood = "idle" | "thinking" | "listening" | "speaking";

/** Polaris' original vector mascot; no external images or services required. */
export function PolarisPet({
  mood = "idle",
  size = 80,
  animated = true,
}: {
  mood?: PetMood;
  size?: number;
  animated?: boolean;
}) {
  const id = useId();
  const label = {
    idle: "Polaris yıldız maskotu",
    thinking: "Polaris çalışıyor",
    listening: "Polaris dinliyor",
    speaking: "Polaris konuşuyor",
  }[mood];
  return (
    <svg
      className={`polaris-pet pet-${mood}${animated ? " pet-animated" : ""}`}
      width={size}
      height={size}
      viewBox="0 0 120 120"
      role="img"
      aria-labelledby={`${id}-title`}
    >
      <title id={`${id}-title`}>{label}</title>
      <defs>
        <linearGradient id={`${id}-gold`} x1="0" y1="0" x2="0.7" y2="1">
          <stop offset="0" stopColor="#fff1aa" />
          <stop offset="0.55" stopColor="#ffdc69" />
          <stop offset="1" stopColor="#efb747" />
        </linearGradient>
      </defs>
      <ellipse
        className="pet-shadow"
        cx="60"
        cy="110"
        rx="24"
        ry="3.5"
        fill="#647657"
        opacity="0.17"
      />
      <g className="pet-body">
        <path
          d="M60 10 Q64 10 68 20 L78 39 L100 43 Q110 45 103 54 L87 72 L91 96 Q93 107 83 102 L60 91 L37 102 Q27 107 29 96 L33 72 L17 54 Q10 45 20 43 L42 39 L52 20 Q56 10 60 10Z"
          fill={`url(#${id}-gold)`}
          stroke="#3c493b"
          strokeWidth="3.5"
          strokeLinejoin="round"
        />
        <path
          d="M56 24 L47 42 L27 47"
          fill="none"
          stroke="#fff9d7"
          strokeWidth="4"
          strokeLinecap="round"
          opacity="0.75"
        />
        <ellipse cx="39" cy="70" rx="7" ry="4" fill="#e99871" opacity="0.65" />
        <ellipse cx="81" cy="70" rx="7" ry="4" fill="#e99871" opacity="0.65" />
        <g className="pet-eyes">
          <ellipse cx="47" cy="58" rx="6.5" ry="9" fill="#303b35" />
          <ellipse cx="73" cy="58" rx="6.5" ry="9" fill="#303b35" />
          <g className="pet-eye-glints" fill="#fffdf2">
            <ellipse cx="45" cy="55" rx="2.1" ry="2.7" />
            <ellipse cx="71" cy="55" rx="2.1" ry="2.7" />
            <circle cx="49" cy="61" r="1.1" />
            <circle cx="75" cy="61" r="1.1" />
          </g>
        </g>
        {mood === "speaking" ? (
          <g>
            <ellipse
              className="pet-speaking-mouth"
              cx="60"
              cy="75"
              rx="5"
              ry="6"
              fill="#3c493b"
            />
            <path
              d="M57 78 Q60 75 63 78"
              stroke="#ef9b88"
              strokeWidth="3"
              strokeLinecap="round"
            />
          </g>
        ) : mood === "listening" ? (
          <path
            d="M56 73 Q60 77 64 73"
            fill="none"
            stroke="#3c493b"
            strokeWidth="3"
            strokeLinecap="round"
          />
        ) : (
          <path
            d="M54 72 Q60 80 66 72"
            fill="none"
            stroke="#3c493b"
            strokeWidth="3"
            strokeLinecap="round"
          />
        )}
        {mood === "thinking" && (
          <path
            d="M68 44 Q74 41 79 44"
            fill="none"
            stroke="#3c493b"
            strokeWidth="2"
            strokeLinecap="round"
          />
        )}
      </g>
      <g className="pet-sparkles" fill="#d0a346">
        <path d="M105 17 L107 22 L112 24 L107 26 L105 31 L103 26 L98 24 L103 22Z" />
        <path d="M13 77 L15 81 L19 83 L15 85 L13 89 L11 85 L7 83 L11 81Z" />
      </g>
      {mood === "listening" && (
        <g
          className="pet-listen-waves"
          fill="none"
          stroke="#70936c"
          strokeWidth="2.5"
          strokeLinecap="round"
        >
          <path d="M101 60 Q108 67 101 74" />
          <path d="M107 55 Q118 67 107 79" />
        </g>
      )}
    </svg>
  );
}
