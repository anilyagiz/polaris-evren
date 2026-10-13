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
      title="Üzerime gel, sana el sallayayım ✨"
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
            <stop stopColor="#fff2b9" />
            <stop offset="0.48" stopColor="#ffdc77" />
            <stop offset="1" stopColor="#eba84b" />
          </linearGradient>
          <linearGradient id={`${id}-screen`} x1="0" y1="0" x2="1" y2="1">
            <stop stopColor="#263d56" />
            <stop offset="1" stopColor="#162840" />
          </linearGradient>
        </defs>
        <ellipse
          className="pet-ground"
          cx="100"
          cy="173"
          rx="49"
          ry="6"
          fill="currentColor"
          opacity="0.12"
        />
        <g className="pet-character">
          {/* Tiny boots and knees make the star a seated character. */}
          <path
            d="M78 115 Q64 130 69 150 M122 115 Q136 130 130 150"
            fill="none"
            stroke="#36455b"
            strokeWidth="9"
            strokeLinecap="round"
          />
          <path
            d="M66 145 Q51 143 52 152 Q54 160 77 155 L78 145Z"
            fill="#738ea0"
            stroke="#34445a"
            strokeWidth="3"
          />
          <path
            d="M123 145 L124 156 Q147 160 149 152 Q151 143 132 145Z"
            fill="#738ea0"
            stroke="#34445a"
            strokeWidth="3"
          />
          <g className="pet-left-arm">
            <path
              d="M61 84 Q42 90 50 109 Q54 116 68 112"
              fill="none"
              stroke="#37475a"
              strokeWidth="9"
              strokeLinecap="round"
            />
            <ellipse
              cx="69"
              cy="111"
              rx="8"
              ry="6"
              fill="#ffe398"
              stroke="#37475a"
              strokeWidth="2.5"
            />
          </g>
          <g className="pet-right-arm">
            <path
              d="M139 84 Q158 88 155 104 Q153 114 136 110"
              fill="none"
              stroke="#37475a"
              strokeWidth="9"
              strokeLinecap="round"
            />
            <path
              d="M135 104 Q126 105 129 113 Q138 120 145 111 L145 105Z"
              fill="#ffe398"
              stroke="#37475a"
              strokeWidth="2.5"
            />
          </g>
          <g className="pet-star-head">
            <path
              d="M100 17 Q105 17 109 26 L121 48 L148 52 Q160 54 152 64 L132 85 L137 111 Q139 122 128 117 L100 104 L72 117 Q61 122 63 111 L68 85 L48 64 Q40 54 52 52 L79 48 L91 26 Q95 17 100 17Z"
              fill={`url(#${id}-gold)`}
              stroke="#34445a"
              strokeWidth="3.4"
              strokeLinejoin="round"
            />
            <path
              d="M96 31 L85 52 L59 58"
              fill="none"
              stroke="#fff9db"
              strokeWidth="5"
              strokeLinecap="round"
              opacity="0.85"
            />
            <ellipse
              cx="76"
              cy="86"
              rx="8"
              ry="4.5"
              fill="#e89886"
              opacity="0.62"
            />
            <ellipse
              cx="124"
              cy="86"
              rx="8"
              ry="4.5"
              fill="#e89886"
              opacity="0.62"
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
          <path
            d="M77 111 Q100 120 123 111 L119 121 Q100 128 81 121Z"
            fill="#76999a"
            stroke="#34445a"
            strokeWidth="2"
          />
          <path
            d="M112 120 L123 121 L126 139 L114 136Z"
            fill="#76999a"
            stroke="#34445a"
            strokeWidth="2"
          />
          {mood === "working" && (
            <g className="pet-laptop">
              <path
                d="M58 115 Q58 111 63 111 L137 111 Q142 111 142 115 L139 151 L61 151Z"
                fill="#a8bac8"
                stroke="#34445a"
                strokeWidth="3"
              />
              <path
                d="M63 118 L137 118 L135 145 L65 145Z"
                fill={`url(#${id}-screen)`}
              />
              <path
                className="pet-code-line line-one"
                d="M73 125 H89 M93 125 H104"
                stroke="#91c7bf"
                strokeWidth="2.5"
                strokeLinecap="round"
              />
              <path
                className="pet-code-line line-two"
                d="M73 132 H100 M73 138 H83"
                stroke="#f2cb82"
                strokeWidth="2.5"
                strokeLinecap="round"
              />
              <path
                d="M118 126 L120 131 L125 132 L121 136 L122 141 L118 138 L114 141 L115 136 L111 132 L116 131Z"
                fill="#f2cb82"
              />
              <path
                d="M53 150 H147 L154 157 Q154 161 145 161 H55 Q46 161 46 157Z"
                fill="#c5d1d9"
                stroke="#34445a"
                strokeWidth="3"
              />
              <path
                d="M88 152 H112"
                stroke="#7c94a7"
                strokeWidth="3"
                strokeLinecap="round"
              />
              <ellipse
                className="pet-typing-hand hand-left"
                cx="68"
                cy="113"
                rx="8"
                ry="5"
                fill="#ffe398"
                stroke="#34445a"
                strokeWidth="2"
              />
              <ellipse
                className="pet-typing-hand hand-right"
                cx="132"
                cy="113"
                rx="8"
                ry="5"
                fill="#ffe398"
                stroke="#34445a"
                strokeWidth="2"
              />
            </g>
          )}
        </g>
        <g className="pet-stardust" fill="#cfaa5e">
          <path d="M32 37 L35 44 L42 47 L35 50 L32 57 L29 50 L22 47 L29 44Z" />
          <path d="M166 77 L168 82 L173 84 L168 86 L166 91 L164 86 L159 84 L164 82Z" />
          <circle cx="153" cy="34" r="2.5" />
          <circle cx="42" cy="130" r="2" />
        </g>
        {mood === "listening" && (
          <g
            className="pet-soundwaves"
            fill="none"
            stroke="#76999a"
            strokeWidth="3"
            strokeLinecap="round"
          >
            <path d="M160 57 Q166 66 160 75" />
            <path d="M169 50 Q180 66 169 82" />
          </g>
        )}
        {mood === "waiting" && (
          <g fill="#f2cb82" stroke="#34445a" strokeWidth="2">
            <circle cx="161" cy="34" r="11" />
            <path
              d="M157 34 L160 37 L165 30"
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
