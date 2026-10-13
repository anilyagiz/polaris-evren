/**
 * Visual "this is the element I meant" marker for the XAI timeline.
 *
 * Drawn inside a closed Shadow DOM attached to <html>, so neither the page's
 * CSS nor its scripts can see or restyle it, and we cannot break the page
 * layout: the overlay is `position: fixed` and `pointer-events: none`.
 */

import { delay, scrollIntoView } from "./dom";

const HOST_ID = "ai-assistant-highlight-host";
const BLINK_MS = 1500;

let shadow: ShadowRoot | null = null;

const STYLE = `
  :host { all: initial; }
  .box {
    position: fixed;
    display: block;
    box-sizing: border-box;
    border: 3px solid #ff5a1f;
    border-radius: 6px;
    background: rgba(255, 90, 31, 0.08);
    box-shadow: 0 0 0 3px rgba(255, 90, 31, 0.25), 0 8px 24px rgba(15, 23, 42, 0.25);
    pointer-events: none;
    animation: ai-blink 750ms ease-in-out 2;
  }
  @keyframes ai-blink {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.15; }
  }
  @media (prefers-reduced-motion: reduce) {
    .box { animation: none; }
  }
`;

function ensureShadow(): ShadowRoot {
  if (shadow !== null && shadow.host.isConnected) {
    return shadow;
  }

  const existing = document.getElementById(HOST_ID);
  existing?.remove();

  const host = document.createElement("div");
  host.id = HOST_ID;
  host.setAttribute("aria-hidden", "true");
  host.style.cssText =
    "position:fixed;top:0;left:0;width:0;height:0;margin:0;padding:0;border:0;" +
    "pointer-events:none;z-index:2147483647;";
  document.documentElement.appendChild(host);

  const root = host.attachShadow({ mode: "closed" });
  const style = document.createElement("style");
  style.textContent = STYLE;
  root.appendChild(style);

  shadow = root;
  return root;
}

/** Put the box over the element's *current* viewport rect. */
function placeBox(box: HTMLElement, element: HTMLElement): void {
  const rect = element.getBoundingClientRect();
  if (rect.width === 0 && rect.height === 0) {
    // Detached or collapsed mid-blink: a 4×4 artifact in the corner would be
    // worse than showing nothing.
    box.style.display = "none";
    return;
  }
  box.style.display = "block";
  box.style.left = `${Math.round(rect.left) - 2}px`;
  box.style.top = `${Math.round(rect.top) - 2}px`;
  box.style.width = `${Math.round(rect.width) + 4}px`;
  box.style.height = `${Math.round(rect.height) + 4}px`;
}

/**
 * Keep the box glued to the element until it is removed, and return the stopper.
 *
 * Two reasons this cannot be a one-off measurement: `scrollIntoView()` honours
 * the page's `scroll-behavior: smooth`, so the rect taken right after it is
 * mid-animation (measured ~290 px off on a 400 ms scroll), and anything that
 * moves afterwards — the user scrolling, a sticky header collapsing, a lazy
 * image reflowing the column — would leave the box pointing at the wrong thing
 * for the rest of the blink.
 */
function followElement(box: HTMLElement, element: HTMLElement): () => void {
  if (typeof requestAnimationFrame !== "function") {
    return () => {};
  }

  let frame = requestAnimationFrame(function tick(): void {
    if (!box.isConnected) {
      return;
    }
    placeBox(box, element);
    frame = requestAnimationFrame(tick);
  });

  return () => cancelAnimationFrame(frame);
}

/** Draw a blinking box over `element`; resolves once it is removed. */
export async function highlightElement(element: HTMLElement): Promise<void> {
  await scrollIntoView(element);

  const root = ensureShadow();
  // One marker at a time: back-to-back highlight calls overlap by design (each
  // one blinks for 1.5 s), and a stale box next to the new one points the user
  // at the previous element.
  for (const stale of Array.from(root.querySelectorAll(".box"))) {
    stale.remove();
  }

  const box = document.createElement("div");
  box.className = "box";
  placeBox(box, element);
  root.appendChild(box);

  const stopFollowing = followElement(box, element);
  try {
    await delay(BLINK_MS);
  } finally {
    stopFollowing();
    box.remove();

    if (root.querySelectorAll(".box").length === 0 && root.host.isConnected) {
      root.host.remove();
      if (shadow === root) {
        shadow = null;
      }
    }
  }
}
