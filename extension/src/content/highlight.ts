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

/** Draw a blinking box over `element`; resolves once it is removed. */
export async function highlightElement(element: HTMLElement): Promise<void> {
  await scrollIntoView(element);

  const root = ensureShadow();
  const rect = element.getBoundingClientRect();
  const box = document.createElement("div");
  box.className = "box";
  box.style.left = `${Math.round(rect.left) - 2}px`;
  box.style.top = `${Math.round(rect.top) - 2}px`;
  box.style.width = `${Math.round(rect.width) + 4}px`;
  box.style.height = `${Math.round(rect.height) + 4}px`;
  root.appendChild(box);

  await delay(BLINK_MS);
  box.remove();

  if (root.querySelectorAll(".box").length === 0) {
    root.host.remove();
    shadow = null;
  }
}
