/**
 * The visible "hand": a Polaris cursor that moves over the page while the
 * assistant acts, so the user can watch it work like a person would.
 *
 *  - a small gold four-point star pointer with a soft glow,
 *  - glides (eased, ~300–450 ms) from its last position to the target centre,
 *  - a press ripple on click, a typing caret while text is typed progressively,
 *  - a brief outline around the target and a tiny Turkish label bubble.
 *
 * Everything lives in a CLOSED Shadow DOM under one fixed, zero-sized,
 * `pointer-events: none` host at the maximum z-index: the page can neither see
 * nor restyle it, it never takes a click, and it has no layout footprint. After
 * the host is attached, every change happens INSIDE the shadow root, so the
 * page's MutationObservers (and our own `waitForDomIdle`) never see the cursor
 * animate.
 *
 * `prefers-reduced-motion: reduce` (or an environment without `matchMedia`,
 * e.g. jsdom) → no glide, no ripple, no progressive typing: the cursor simply
 * appears at the target and the actions run at full speed.
 */

import { accessibleName, delay, roleOf } from "./dom";

export const HAND_HOST_ID = "polaris-hand-host";

/** Glide duration bounds; the actual value scales with the distance. */
const GLIDE_MIN_MS = 300;
const GLIDE_MAX_MS = 450;
/** How long the "press" is held before the real click fires. */
const PRESS_MS = 140;
/** Total budget for progressive typing; the final value is set afterwards. */
const TYPE_BUDGET_MS = 1000;
const CHAR_MIN_MS = 25;
const CHAR_MAX_MS = 38;
/** The cursor fades out after this much inactivity. */
const IDLE_HIDE_MS = 2600;
const STAR_SIZE = 26;
const LABEL_MAX = 60;

interface HandParts {
  root: ShadowRoot;
  cursor: HTMLElement;
  outline: HTMLElement;
  label: HTMLElement;
  labelText: HTMLElement;
  caret: HTMLElement;
}

let parts: HandParts | null = null;
let position: { x: number; y: number } | null = null;
let hideTimer: ReturnType<typeof setTimeout> | undefined;

const STAR_SVG = `
<svg viewBox="0 0 32 32" width="${STAR_SIZE}" height="${STAR_SIZE}" aria-hidden="true">
  <defs>
    <radialGradient id="g" cx="50%" cy="45%" r="60%">
      <stop offset="0%" stop-color="#fff7d6"/>
      <stop offset="55%" stop-color="#f6c945"/>
      <stop offset="100%" stop-color="#d99a11"/>
    </radialGradient>
  </defs>
  <path d="M16 1.5 L19.2 12.8 L30.5 16 L19.2 19.2 L16 30.5 L12.8 19.2 L1.5 16 L12.8 12.8 Z"
        fill="url(#g)" stroke="#8a5a00" stroke-width="0.8" stroke-linejoin="round"/>
</svg>`;

const STYLE = `
  :host { all: initial; }
  .layer { position: fixed; inset: 0 auto auto 0; width: 0; height: 0; pointer-events: none; }
  .cursor {
    position: fixed; left: 0; top: 0;
    width: ${STAR_SIZE}px; height: ${STAR_SIZE}px;
    margin: ${-STAR_SIZE / 2}px 0 0 ${-STAR_SIZE / 2}px;
    pointer-events: none;
    filter: drop-shadow(0 0 6px rgba(246, 201, 69, 0.85)) drop-shadow(0 2px 4px rgba(15, 23, 42, 0.35));
    transition-property: transform, opacity;
    transition-timing-function: cubic-bezier(0.22, 0.8, 0.26, 1), ease;
    transition-duration: 0ms, 220ms;
    will-change: transform;
    opacity: 1;
  }
  .cursor svg { display: block; transition: transform 120ms ease; }
  .cursor.pressed svg { transform: scale(0.78); }
  .cursor.typing svg { animation: pl-pulse 700ms ease-in-out infinite; }
  .hidden { opacity: 0 !important; }
  .ripple {
    position: fixed; width: 34px; height: 34px; margin: -17px 0 0 -17px;
    border-radius: 50%; border: 2px solid rgba(246, 201, 69, 0.95);
    background: rgba(246, 201, 69, 0.18); pointer-events: none;
    animation: pl-ripple 420ms ease-out forwards;
  }
  .outline {
    position: fixed; box-sizing: border-box; pointer-events: none;
    border: 2px solid rgba(246, 201, 69, 0.95); border-radius: 7px;
    box-shadow: 0 0 0 3px rgba(246, 201, 69, 0.25), 0 0 14px rgba(246, 201, 69, 0.45);
    transition: opacity 200ms ease; opacity: 0; display: none;
  }
  .outline.on { opacity: 1; display: block; }
  .label {
    position: fixed; left: 0; top: 0; max-width: 280px; pointer-events: none;
    padding: 5px 9px; border-radius: 9px;
    font: 600 12px/1.3 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    color: #fff8e1; background: rgba(23, 27, 38, 0.92);
    border: 1px solid rgba(246, 201, 69, 0.75);
    box-shadow: 0 6px 18px rgba(15, 23, 42, 0.35);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    transition: opacity 180ms ease; opacity: 0;
  }
  .label.on { opacity: 1; }
  .caret {
    display: none; width: 2px; height: 1em; margin-left: 2px; vertical-align: -2px;
    background: #f6c945; animation: pl-blink 900ms steps(1) infinite;
  }
  .label.typing .caret { display: inline-block; }
  @keyframes pl-ripple { from { transform: scale(0.3); opacity: 0.9; } to { transform: scale(1.9); opacity: 0; } }
  @keyframes pl-blink { 50% { opacity: 0; } }
  @keyframes pl-pulse { 50% { transform: scale(0.9); } }
  @media (prefers-reduced-motion: reduce) {
    .cursor, .cursor svg, .label, .outline { transition: none !important; }
    .cursor.typing svg, .caret, .ripple { animation: none !important; }
    .ripple { display: none; }
  }
`;

/** True when the hand must not animate (user setting, or no media queries). */
export function prefersReducedMotion(): boolean {
  if (typeof window.matchMedia !== "function") {
    return true;
  }
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return true;
  }
}

function ensureHand(): HandParts {
  if (parts !== null && parts.root.host.isConnected) {
    return parts;
  }
  document.getElementById(HAND_HOST_ID)?.remove();

  const host = document.createElement("div");
  host.id = HAND_HOST_ID;
  host.setAttribute("aria-hidden", "true");
  // Zero-sized and fixed: no layout impact, never a click target.
  host.style.cssText =
    "position:fixed;top:0;left:0;width:0;height:0;margin:0;padding:0;border:0;" +
    "overflow:visible;pointer-events:none;z-index:2147483647;contain:layout style;";

  const root = host.attachShadow({ mode: "closed" });
  const style = document.createElement("style");
  style.textContent = STYLE;
  const layer = document.createElement("div");
  layer.className = "layer";

  const outline = document.createElement("div");
  outline.className = "outline";
  const cursor = document.createElement("div");
  cursor.className = "cursor hidden";
  cursor.innerHTML = STAR_SVG; // static markup, no page data
  const label = document.createElement("div");
  label.className = "label";
  const labelText = document.createElement("span");
  const caret = document.createElement("span");
  caret.className = "caret";
  label.append(labelText, caret);

  layer.append(outline, cursor, label);
  root.append(style, layer);
  document.documentElement.appendChild(host);

  parts = { root, cursor, outline, label, labelText, caret };
  return parts;
}

function viewportStart(): { x: number; y: number } {
  return { x: Math.round(window.innerWidth * 0.82), y: Math.round(window.innerHeight * 0.86) };
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function setCursorAt(cursor: HTMLElement, x: number, y: number): void {
  cursor.style.transform = `translate(${Math.round(x)}px, ${Math.round(y)}px)`;
}

function placeLabel(label: HTMLElement, x: number, y: number): void {
  const width = window.innerWidth || 1024;
  const height = window.innerHeight || 768;
  const left = clamp(x + 16, 8, Math.max(8, width - 290));
  const top = y + 44 > height ? y - 36 : y + 18;
  label.style.transform = `translate(${Math.round(left)}px, ${Math.round(clamp(top, 8, height - 30))}px)`;
}

function placeOutline(outline: HTMLElement, rect: DOMRect): void {
  if (rect.width === 0 && rect.height === 0) {
    outline.classList.remove("on");
    return;
  }
  outline.style.left = `${Math.round(rect.left) - 3}px`;
  outline.style.top = `${Math.round(rect.top) - 3}px`;
  outline.style.width = `${Math.round(rect.width) + 6}px`;
  outline.style.height = `${Math.round(rect.height) + 6}px`;
  outline.classList.add("on");
}

function scheduleHide(): void {
  clearTimeout(hideTimer);
  hideTimer = setTimeout(() => {
    if (parts === null) {
      return;
    }
    parts.cursor.classList.add("hidden");
    parts.label.classList.remove("on", "typing");
    parts.outline.classList.remove("on");
  }, IDLE_HIDE_MS);
}

function showLabel(hand: HandParts, text: string): void {
  const clean = text.replace(/\s+/g, " ").trim();
  hand.labelText.textContent = clean.length > LABEL_MAX ? `${clean.slice(0, LABEL_MAX - 1)}…` : clean;
  hand.label.classList.toggle("on", clean !== "");
}

/** Glide the cursor to (x, y). Resolves when it has arrived. */
async function glideTo(hand: HandParts, x: number, y: number): Promise<void> {
  const from = position ?? viewportStart();
  const reduced = prefersReducedMotion();
  const wasHidden = hand.cursor.classList.contains("hidden");

  if (reduced) {
    hand.cursor.style.transitionDuration = "0ms, 0ms";
    setCursorAt(hand.cursor, x, y);
    hand.cursor.classList.remove("hidden");
    position = { x, y };
    return;
  }

  if (wasHidden || position === null) {
    // Commit the start point without a transition, then animate from there.
    hand.cursor.style.transitionDuration = "0ms, 220ms";
    setCursorAt(hand.cursor, from.x, from.y);
    void hand.cursor.getBoundingClientRect();
  }
  const distance = Math.hypot(x - from.x, y - from.y);
  const ms = Math.round(clamp(GLIDE_MIN_MS + distance * 0.25, GLIDE_MIN_MS, GLIDE_MAX_MS));
  hand.cursor.classList.remove("hidden");
  hand.cursor.style.transitionDuration = `${ms}ms, 220ms`;
  setCursorAt(hand.cursor, x, y);
  position = { x, y };
  await delay(ms);
}

/**
 * Move the hand onto `element`, outline it and show `label`.
 * Never throws: the hand is decoration, the action must run regardless.
 */
export async function pointAt(element: Element, label: string): Promise<void> {
  try {
    const hand = ensureHand();
    clearTimeout(hideTimer);
    const rect = element.getBoundingClientRect();
    const x = rect.left + rect.width / 2;
    const y = rect.top + rect.height / 2;
    hand.label.classList.remove("typing");
    showLabel(hand, label);
    placeLabel(hand.label, x, y);
    placeOutline(hand.outline, rect);
    await glideTo(hand, x, y);
    scheduleHide();
  } catch {
    /* decoration only */
  }
}

/** Show the hand without a target (scroll, whole-page read). */
export async function announce(label: string, where: "center" | "down" | "up" = "center"): Promise<void> {
  try {
    const hand = ensureHand();
    clearTimeout(hideTimer);
    const x = Math.round((window.innerWidth || 1024) * 0.88);
    const base = (window.innerHeight || 768) / 2;
    const y = Math.round(where === "down" ? base + 120 : where === "up" ? base - 120 : base);
    hand.outline.classList.remove("on");
    hand.label.classList.remove("typing");
    showLabel(hand, label);
    placeLabel(hand.label, x, y);
    await glideTo(hand, x, y);
    scheduleHide();
  } catch {
    /* decoration only */
  }
}

/** Press animation + ripple at the current position; resolves before the click. */
export async function press(): Promise<void> {
  try {
    if (parts === null || position === null || prefersReducedMotion()) {
      return;
    }
    const hand = parts;
    const ripple = document.createElement("div");
    ripple.className = "ripple";
    ripple.style.left = `${Math.round(position.x)}px`;
    ripple.style.top = `${Math.round(position.y)}px`;
    hand.root.lastElementChild?.appendChild(ripple);
    hand.cursor.classList.add("pressed");
    await delay(PRESS_MS);
    hand.cursor.classList.remove("pressed");
    setTimeout(() => ripple.remove(), 480);
    scheduleHide();
  } catch {
    /* decoration only */
  }
}

/**
 * Type `text` progressively: `write(partial)` is called with growing prefixes
 * (~25–38 ms per character, at most ~1 s in total — long texts advance several
 * characters per frame). The caller sets the FINAL value itself afterwards, so
 * a page that rewrites the field mid-way can never leave it half typed.
 *
 * Skipped entirely under reduced motion.
 */
export async function typeProgressively(text: string, write: (partial: string) => void): Promise<void> {
  if (text === "" || prefersReducedMotion()) {
    return;
  }
  const hand = parts;
  hand?.cursor.classList.add("typing");
  hand?.label.classList.add("typing");
  try {
    const chars = Array.from(text);
    // At most one step per CHAR_MIN_MS inside the budget; long texts advance
    // several characters per step so the total never exceeds the budget.
    const maxSteps = Math.floor(TYPE_BUDGET_MS / CHAR_MIN_MS);
    const chunk = Math.max(1, Math.ceil(chars.length / maxSteps));
    const steps = Math.ceil(chars.length / chunk);
    const stepMs = Math.min(CHAR_MAX_MS, Math.floor(TYPE_BUDGET_MS / steps));
    for (let index = chunk; index < chars.length; index += chunk) {
      write(chars.slice(0, index).join(""));
      await delay(stepMs);
    }
  } finally {
    hand?.cursor.classList.remove("typing");
    hand?.label.classList.remove("typing");
    scheduleHide();
  }
}

// ---------------------------------------------------------------------------
// Turkish step labels
// ---------------------------------------------------------------------------

const BACK_VOWELS = "aıouâû";
const FRONT_VOWELS = "eiöüî";

/** Turkish dative suffix for a quoted name: “Ara”ya, “Gönder”e, “Sepete ekle”ye. */
export function dativeSuffix(word: string): string | null {
  const lower = word.toLocaleLowerCase("tr");
  for (let i = lower.length - 1; i >= 0; i -= 1) {
    const char = lower[i] ?? "";
    const back = BACK_VOWELS.includes(char);
    const front = FRONT_VOWELS.includes(char);
    if (back || front) {
      const vowel = back ? "a" : "e";
      const last = lower[lower.length - 1] ?? "";
      const endsWithVowel = BACK_VOWELS.includes(last) || FRONT_VOWELS.includes(last);
      return endsWithVowel ? `y${vowel}` : vowel;
    }
  }
  return null;
}

function shortName(element: Element): { name: string; cut: boolean } {
  const raw = element instanceof HTMLElement ? accessibleName(element, 80) : "";
  const clean = raw.replace(/\s+/g, " ").trim();
  if (clean.length <= 28) {
    return { name: clean, cut: false };
  }
  return { name: `${clean.slice(0, 27).trimEnd()}…`, cut: true };
}

function isSearchField(element: Element): boolean {
  if (!(element instanceof HTMLElement)) {
    return false;
  }
  if (roleOf(element) === "searchbox" || (element as HTMLInputElement).type === "search") {
    return true;
  }
  const hay = [
    element.getAttribute("name"),
    element.getAttribute("placeholder"),
    element.getAttribute("aria-label"),
    element.id,
  ]
    .join(" ")
    .toLocaleLowerCase("tr");
  return /\b(ara|arama|search|sorgu|query|q)\b/.test(hay);
}

export type HandAction = "click" | "type" | "read" | "highlight";

/** One short Turkish sentence describing what the hand is about to do. */
export function describeStep(action: HandAction, element: Element): string {
  const { name, cut } = shortName(element);
  const quoted = name === "" ? "" : `“${name}”`;
  switch (action) {
    case "click": {
      if (quoted === "") return "Buraya tıklıyorum";
      const suffix = cut ? null : dativeSuffix(name);
      return suffix === null ? `${quoted} öğesine tıklıyorum` : `${quoted}${suffix} tıklıyorum`;
    }
    case "type":
      if (isSearchField(element)) return "Arama kutusuna yazıyorum";
      if (element instanceof HTMLSelectElement) {
        return quoted === "" ? "Seçim yapıyorum" : `${quoted} için seçim yapıyorum`;
      }
      return quoted === "" ? "Metin kutusuna yazıyorum" : `${quoted} alanına yazıyorum`;
    case "read":
      return quoted === "" ? "Bu bölümü okuyorum" : `${quoted} bölümünü okuyorum`;
    case "highlight":
      return quoted === "" ? "Şunu gösteriyorum" : `Şunu gösteriyorum: ${quoted}`;
    default:
      return "";
  }
}

/** Optional label override from the tool args (`label`), else the derived one. */
export function labelFor(args: Record<string, unknown>, fallback: string): string {
  const custom = args.label;
  if (typeof custom === "string" && custom.trim() !== "") {
    return custom.trim().slice(0, 80);
  }
  return fallback;
}

/** Test helper: drop the overlay and forget the last position. */
export function resetHand(): void {
  clearTimeout(hideTimer);
  parts?.root.host.remove();
  document.getElementById(HAND_HOST_ID)?.remove();
  parts = null;
  position = null;
}

/** Test/inspection helper: the cursor's last target point. */
export function handPosition(): { x: number; y: number } | null {
  return position === null ? null : { ...position };
}
