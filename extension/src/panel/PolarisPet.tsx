import { useEffect, useId, useState, type CSSProperties } from "react";
import "./pet.css";

export type PetMood = "idle" | "working" | "listening" | "speaking" | "waiting";
const labels: Record<PetMood, string> = {
  idle: "Polaris yanında",
  working: "Polaris çalışıyor",
  listening: "Polaris seni dinliyor",
  speaking: "Polaris konuşuyor",
  waiting: "Polaris onayını bekliyor",
};

/** A local, interactive mascot. Its gestures never run assistant tools. */
export function PolarisPet({
  mood = "idle",
  size = 140,
  compact = false,
}: {
  mood?: PetMood;
  size?: number;
  compact?: boolean;
}) {
  const id = useId();
  const [hovered, setHovered] = useState(false);
  const [celebrating, setCelebrating] = useState(false);
  const [look, setLook] = useState({ x: 0, y: 0 });
  useEffect(() => {
    if (!celebrating) return;
    const timer = setTimeout(() => setCelebrating(false), 1800);
    return () => clearTimeout(timer);
  }, [celebrating]);
  const greeting = hovered || celebrating;
  const style = {
    width: size,
    height: size * 0.95,
    "--pet-look-x": `${look.x}px`,
    "--pet-look-y": `${look.y}px`,
    "--pet-tilt": `${look.x * 0.7}deg`,
  } as CSSProperties;
  return (
    <button
      type="button"
      className={`polaris-pet-button pet-${mood}${greeting ? " pet-greeting" : ""}${celebrating ? " pet-celebrate" : ""}${compact ? " pet-compact" : ""}`}
      style={style}
      aria-label={`${labels[mood]}; selamlaşmak için tıkla`}
      title="Üzerime gel, birlikte parlayalım ✨"
      data-mood={mood}
      onPointerEnter={() => setHovered(true)}
      onPointerLeave={() => {
        setHovered(false);
        setLook({ x: 0, y: 0 });
      }}
      onPointerMove={(event) => {
        const bounds = event.currentTarget.getBoundingClientRect();
        if (!bounds.width || !bounds.height) return;
        setLook({
          x: Math.max(
            -3,
            Math.min(3, ((event.clientX - bounds.left) / bounds.width) * 6 - 3),
          ),
          y: Math.max(
            -2,
            Math.min(2, ((event.clientY - bounds.top) / bounds.height) * 4 - 2),
          ),
        });
      }}
      onFocus={() => setHovered(true)}
      onBlur={() => {
        setHovered(false);
        setLook({ x: 0, y: 0 });
      }}
      onClick={() => setCelebrating(true)}
    >
      <svg viewBox="0 0 200 190" width="100%" height="100%" aria-hidden="true">
        <defs>
          <linearGradient id={`${id}-gold`} x1="0" y1="0" x2="0.8" y2="1">
            <stop stopColor="#fff7cf" />
            <stop offset="0.48" stopColor="#ffe188" />
            <stop offset="1" stopColor="#edb254" />
          </linearGradient>
          <linearGradient id={`${id}-tail`} x1="0" y1="1" x2="1" y2="0">
            <stop stopColor="#86b5c5" stopOpacity="0" />
            <stop offset="0.45" stopColor="#b4c9c2" stopOpacity="0.42" />
            <stop offset="1" stopColor="#f4cc76" stopOpacity="0.9" />
          </linearGradient>
          <radialGradient id={`${id}-aura`}>
            <stop stopColor="#ffe9a3" stopOpacity="0.6" />
            <stop offset="1" stopColor="#ffe9a3" stopOpacity="0" />
          </radialGradient>
          <clipPath id={`${id}-clip`}>
            <path d="M100 17 Q105 17 109 26 L121 48 L148 52 Q160 54 152 64 L132 85 L137 111 Q139 122 128 117 L100 104 L72 117 Q61 122 63 111 L68 85 L48 64 Q40 54 52 52 L79 48 L91 26 Q95 17 100 17Z" />
          </clipPath>
        </defs>
        <ellipse
          className="pet-aura"
          cx="126"
          cy="98"
          rx="68"
          ry="68"
          fill={`url(#${id}-aura)`}
        />
        <g className="pet-comet-tail">
          <path
            d="M118 89 C77 70 32 102 8 149 C42 119 80 139 130 118Z"
            fill={`url(#${id}-tail)`}
          />
          <path
            d="M116 95 C77 79 35 107 14 134"
            fill="none"
            stroke="#dfc48b"
            strokeWidth="3"
            strokeLinecap="round"
            opacity="0.65"
          />
          <path
            d="M115 106 C80 96 49 119 27 143"
            fill="none"
            stroke="#9fbdc9"
            strokeWidth="2.5"
            strokeLinecap="round"
            opacity="0.6"
          />
          <g className="pet-trail-particles" fill="#d5b365">
            <circle cx="37" cy="119" r="2" />
            <circle cx="62" cy="128" r="2.4" />
            <circle cx="19" cy="151" r="1.5" />
            <path d="M50 97 L52 103 L58 105 L52 107 L50 113 L48 107 L42 105 L48 103Z" />
          </g>
        </g>
        <g className="pet-character">
          <g transform="translate(26 27)">
            <g className="pet-star-head">
              <path
                d="M100 17 Q105 17 109 26 L121 48 L148 52 Q160 54 152 64 L132 85 L137 111 Q139 122 128 117 L100 104 L72 117 Q61 122 63 111 L68 85 L48 64 Q40 54 52 52 L79 48 L91 26 Q95 17 100 17Z"
                fill={`url(#${id}-gold)`}
                stroke="#34445a"
                strokeWidth="3.1"
                strokeLinejoin="round"
              />
              <path
                d="M96 31 L85 52 L59 58"
                fill="none"
                stroke="#fffbea"
                strokeWidth="5"
                strokeLinecap="round"
                opacity="0.9"
              />
              <g clipPath={`url(#${id}-clip)`}>
                <path
                  className="pet-sheen"
                  d="M54 13 L76 13 L143 129 L121 129Z"
                  fill="white"
                  opacity="0.55"
                />
              </g>
              <ellipse
                cx="76"
                cy="86"
                rx="8"
                ry="4.5"
                fill="#e89886"
                opacity="0.58"
              />
              <ellipse
                cx="124"
                cy="86"
                rx="8"
                ry="4.5"
                fill="#e89886"
                opacity="0.58"
              />
              <g className="pet-eyes">
                <ellipse cx="85" cy="72" rx="9.5" ry="12" fill="#fffdf3" />
                <ellipse cx="115" cy="72" rx="9.5" ry="12" fill="#fffdf3" />
                <g className="pet-pupils" fill="#293e50">
                  <ellipse cx="86" cy="73" rx="5.8" ry="8" />
                  <ellipse cx="116" cy="73" rx="5.8" ry="8" />
                  <g fill="#fff">
                    <circle cx="84" cy="69" r="2.2" />
                    <circle cx="114" cy="69" r="2.2" />
                    <circle cx="88" cy="76" r="1" />
                    <circle cx="118" cy="76" r="1" />
                  </g>
                </g>
              </g>
              {greeting ? (
                <path d="M91 89 Q100 103 109 89Z" fill="#34445a" />
              ) : mood === "speaking" ? (
                <ellipse
                  className="pet-mouth"
                  cx="100"
                  cy="93"
                  rx="6"
                  ry="7"
                  fill="#34445a"
                />
              ) : mood === "listening" ? (
                <path
                  d="M94 92 Q100 96 106 92"
                  fill="none"
                  stroke="#34445a"
                  strokeWidth="3"
                  strokeLinecap="round"
                />
              ) : (
                <path
                  d="M93 90 Q100 99 107 90"
                  fill="none"
                  stroke="#34445a"
                  strokeWidth="3"
                  strokeLinecap="round"
                />
              )}
              <path
                className="pet-brows"
                d="M78 55 Q85 52 91 55 M109 55 Q115 52 122 55"
                fill="none"
                stroke="#34445a"
                strokeWidth="2"
                strokeLinecap="round"
              />
            </g>
          </g>
        </g>
        <g className="pet-stardust" fill="#cfaa5e">
          <path d="M166 27 L169 34 L176 37 L169 40 L166 47 L163 40 L156 37 L163 34Z" />
          <path d="M176 139 L178 144 L183 146 L178 148 L176 153 L174 148 L169 146 L174 144Z" />
          <circle cx="89" cy="33" r="2.5" />
          <circle cx="191" cy="102" r="2" />
        </g>
        {mood === "listening" && (
          <g
            className="pet-soundwaves"
            fill="none"
            stroke="#76999a"
            strokeWidth="3"
            strokeLinecap="round"
          >
            <path d="M181 80 Q187 89 181 98" />
            <path d="M188 73 Q199 89 188 105" />
          </g>
        )}
        {mood === "waiting" && (
          <g fill="#f2cb82" stroke="#34445a" strokeWidth="2">
            <circle cx="174" cy="38" r="11" />
            <path
              d="M170 38 L173 41 L178 34"
              fill="none"
              strokeLinecap="round"
            />
          </g>
        )}
      </svg>
      <span className="pet-greeting-bubble" aria-hidden="true">
        {celebrating ? "Birlikte parlayalım!" : "Merhaba! ✨"}
      </span>
    </button>
  );
}
