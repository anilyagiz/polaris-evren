/**
 * Pure helpers of the desktop mascot (mascot.tsx): mood mapping and the
 * JS -> Python bridge of the pywebview host window (tray/mascot.py).
 */
import type { PetMood } from "./PolarisPet";

export interface MoodInputs {
  /** need_confirm is pending. */
  confirming: boolean;
  /** Push-to-talk is recording or transcribing. */
  recording: boolean;
  /** The task input is focused and has text in it. */
  typing: boolean;
  /** A user_msg is running (no final/error yet). */
  busy: boolean;
  /** TTS is playing. */
  speaking: boolean;
}

/** Highest priority first: waiting > listening > working > speaking > idle. */
export function mascotMood(state: MoodInputs): PetMood {
  if (state.confirming) return "waiting";
  if (state.recording || state.typing) return "listening";
  if (state.busy) return "working";
  if (state.speaking) return "speaking";
  return "idle";
}

/** Answer / reminder bubbles hide after this long unless the pointer is on them. */
export const BUBBLE_AUTO_HIDE_MS = 12000;
/** Single click waits this long so a double click can win. */
export const CLICK_DELAY_MS = 260;
/** Pointer travel (screen px) after which a press is a drag, not a click. */
export const DRAG_SLOP_PX = 4;

export type HostMethod =
  | "open_main"
  | "set_expanded"
  | "hide_mascot"
  | "set_hit_regions";

export interface HitRegion {
  kind: "circle" | "rect";
  x: number;
  y: number;
  w: number;
  h: number;
}
type Box = Pick<DOMRect, "left" | "top" | "width" | "height">;

/**
 * Clickable areas for the colour-keyed host window (tray/mascot.py paints them
 * underneath the page). They must stay inside opaque pixels: a small circle
 * in the star's body (measured on screen: body centre at 56% of the pet box
 * height, inner pentagon radius ~13% of its width) and the bubble minus its
 * rounded corners. A bigger circle peeks out between the star's arms.
 */
export function hitRegions(pet: Box | null, bubble: Box | null): HitRegion[] {
  const regions: HitRegion[] = [];
  if (pet && pet.width > 0) {
    const r = pet.width * 0.12;
    const cx = pet.left + pet.width / 2;
    const cy = pet.top + pet.height * 0.56;
    regions.push({ kind: "circle", x: cx - r, y: cy - r, w: 2 * r, h: 2 * r });
  }
  if (bubble && bubble.width > 12 && bubble.height > 12)
    regions.push({
      kind: "rect",
      x: bubble.left + 6,
      y: bubble.top + 6,
      w: bubble.width - 12,
      h: bubble.height - 12,
    });
  return regions.map((region) => ({
    kind: region.kind,
    x: Math.round(region.x),
    y: Math.round(region.y),
    w: Math.round(region.w),
    h: Math.round(region.h),
  }));
}

type HostApi = Partial<Record<HostMethod, (...args: unknown[]) => unknown>>;

function hostApi(): HostApi | null {
  try {
    const bridge = (window as { pywebview?: { api?: HostApi } }).pywebview;
    return bridge?.api ?? null;
  } catch {
    return null;
  }
}

/** Call the pywebview js_api; false (and no error) outside the mascot window. */
export function hostCall(method: HostMethod, ...args: unknown[]): boolean {
  const fn = hostApi()?.[method];
  if (typeof fn !== "function") return false;
  try {
    void Promise.resolve(fn(...args)).catch(() => undefined);
    return true;
  } catch {
    return false;
  }
}
