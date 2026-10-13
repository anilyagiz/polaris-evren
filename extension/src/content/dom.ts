/**
 * Shared DOM helpers for the content layer: visibility, accessible names,
 * roles, text extraction and "wait until the page settles".
 *
 * Kept dependency-free on purpose (plain TS + DOM API, see role prompt).
 */

export const REF_ATTRIBUTE = "data-ai-ref";

/** Tags whose text is never part of the readable content. */
const TEXT_EXCLUDED_SELECTOR =
  "script, style, noscript, template, svg, nav, footer, aside, [aria-hidden='true'], [hidden]";

/** Block-ish containers; used to keep paragraph breaks in extracted text. */
const BLOCK_SELECTOR =
  "p, div, li, tr, td, th, h1, h2, h3, h4, h5, h6, section, article, header, main, " +
  "blockquote, pre, figcaption, dd, dt, summary, label, option";

export function collapseWhitespace(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}

export function truncate(value: string, max: number): string {
  return value.length <= max ? value : value.slice(0, max);
}

export function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Find an element previously numbered by `snapshot()`. */
export function elementByRef(ref: number): HTMLElement | null {
  return document.querySelector<HTMLElement>(`[${REF_ATTRIBUTE}="${ref}"]`);
}

export function isDisabled(element: Element): boolean {
  if ("disabled" in element && (element as { disabled?: boolean }).disabled === true) {
    return true;
  }
  return element.getAttribute("aria-disabled") === "true";
}

/** Anything thinner than this is a screen-reader shim, not a click target. */
const MIN_INTERACTIVE_SIZE = 2;

/** How far up we look for a clipping container before giving up. */
const MAX_CLIP_DEPTH = 20;

/**
 * True when an `overflow: hidden/clip/auto/scroll` ancestor collapses the
 * element away: a zero-sized container, or a rect fully outside it.
 *
 * `checkVisibility()` does not cover this, and the pattern is common
 * (collapsed menus, off-screen carousels), so without it the LLM gets refs it
 * can never click.
 *
 * The walk stops at `position: fixed`, on the element itself or on any ancestor:
 * a fixed box is laid out against the viewport, so nothing above it clips it.
 * Without that stop, a fixed mega-menu or modal nested in a short
 * `overflow:hidden` header was reported hidden while covering half the screen,
 * and the LLM could not see a single item in it. (A transformed, filtered or
 * `contain`ed ancestor does become the containing block of a fixed box and can
 * clip it; we accept that rare stray ref rather than lose every fixed panel.)
 */
function isClippedAway(element: Element, rect: DOMRect, position: string): boolean {
  if (position === "fixed") {
    return false;
  }

  let parent = element.parentElement;
  let depth = 0;

  while (parent !== null && parent !== document.documentElement && depth < MAX_CLIP_DEPTH) {
    const style = window.getComputedStyle(parent);
    const overflow = `${style.overflow} ${style.overflowX} ${style.overflowY}`;
    if (/hidden|clip|scroll|auto/.test(overflow)) {
      const box = parent.getBoundingClientRect();
      if (box.width < MIN_INTERACTIVE_SIZE || box.height < MIN_INTERACTIVE_SIZE) {
        return true;
      }
      if (
        rect.right <= box.left ||
        rect.left >= box.right ||
        rect.bottom <= box.top ||
        rect.top >= box.bottom
      ) {
        return true;
      }
    }
    if (style.position === "fixed") {
      return false;
    }
    parent = parent.parentElement;
    depth += 1;
  }
  return false;
}

/** The classic `.sr-only` / `.visually-hidden` recipes. */
function isScreenReaderOnly(style: CSSStyleDeclaration): boolean {
  if (style.clipPath === "inset(50%)") {
    return true;
  }
  const clip = style.clip.replace(/px|\s/g, "");
  return clip === "rect(0,0,0,0)" || clip === "rect(1,1,1,1)";
}

/**
 * Visible = has a real box, is not display/visibility/opacity hidden, is not
 * marked aria-hidden, is not clipped away by an ancestor and is not a
 * screen-reader-only shim. Uses `checkVisibility()` when available
 * (Chromium 105+) because it also covers `content-visibility`.
 *
 * `knownRect` lets a caller that has already measured the element skip a second
 * layout read, and keeps the visibility verdict and the reported bbox in sync.
 */
export function isVisible(element: Element, knownRect?: DOMRect): boolean {
  const rect = knownRect ?? element.getBoundingClientRect();
  if (rect.width < MIN_INTERACTIVE_SIZE || rect.height < MIN_INTERACTIVE_SIZE) {
    return false;
  }
  if (element.closest("[aria-hidden='true']") !== null) {
    return false;
  }

  // One computed style for all three checks below.
  const style = window.getComputedStyle(element);
  if (isScreenReaderOnly(style) || isClippedAway(element, rect, style.position)) {
    return false;
  }

  const withCheck = element as Element & {
    checkVisibility?: (options?: Record<string, boolean>) => boolean;
  };
  if (typeof withCheck.checkVisibility === "function") {
    return withCheck.checkVisibility({
      checkOpacity: true,
      checkVisibilityCSS: true,
      contentVisibilityAuto: true,
      opacityProperty: true,
      visibilityProperty: true,
    });
  }

  return (
    style.display !== "none" &&
    style.visibility !== "hidden" &&
    style.visibility !== "collapse" &&
    Number.parseFloat(style.opacity || "1") > 0
  );
}

/**
 * Cheap visibility check for text extraction.
 *
 * Deliberately NOT geometric: a block container can report a zero-width rect
 * while rendering its text perfectly well, and requiring a box silently drops
 * headings and paragraphs from `read` / `text_excerpt`. Here we only ask
 * "is this rendered at all", which is also much cheaper than `isVisible()` —
 * text-heavy pages have hundreds of distinct parents.
 */
export function isRendered(element: Element): boolean {
  const withCheck = element as Element & {
    checkVisibility?: (options?: Record<string, boolean>) => boolean;
  };
  if (typeof withCheck.checkVisibility === "function") {
    return withCheck.checkVisibility({
      checkOpacity: true,
      checkVisibilityCSS: true,
      contentVisibilityAuto: true,
      opacityProperty: true,
      visibilityProperty: true,
    });
  }
  const style = window.getComputedStyle(element);
  return (
    style.display !== "none" &&
    style.visibility !== "hidden" &&
    style.visibility !== "collapse" &&
    Number.parseFloat(style.opacity || "1") > 0
  );
}

function textFromLabelledBy(element: Element): string {
  const ids = element.getAttribute("aria-labelledby");
  if (ids === null) {
    return "";
  }
  return ids
    .split(/\s+/)
    .map((id) => document.getElementById(id)?.innerText ?? "")
    .join(" ");
}

function textFromAssociatedLabel(element: Element): string {
  const labels = (element as HTMLInputElement).labels;
  if (labels && labels.length > 0) {
    return Array.from(labels)
      .map((label) => label.innerText)
      .join(" ");
  }
  const id = element.getAttribute("id");
  if (id !== null && id !== "") {
    // A lone backslash would escape the closing quote and throw a SyntaxError
    // out of the whole snapshot, so escape it together with the quote.
    const escaped = id.replace(/["\\]/g, "\\$&");
    const label = document.querySelector<HTMLLabelElement>(`label[for="${escaped}"]`);
    if (label !== null) {
      return label.innerText;
    }
  }
  return element.closest("label")?.innerText ?? "";
}

/**
 * Accessible name, in the order required by the spec:
 * aria-label → aria-labelledby → <label> → placeholder → innerText → title →
 * alt → value.
 *
 * A `<select>` is the one element that is never named by its own content: its
 * `innerText` is the whole option list ("Marka seçin Logitech Razer Keychron…")
 * and its `value` the raw slug of the selected option ("ist"). Neither is a
 * label, and the selected option is already reported as the element's value, so
 * both candidates are skipped for selects.
 */
export function accessibleName(element: HTMLElement, maxLength: number): string {
  const namedByContent = !(element instanceof HTMLSelectElement);
  const candidates: string[] = [
    element.getAttribute("aria-label") ?? "",
    textFromLabelledBy(element),
    textFromAssociatedLabel(element),
    element.getAttribute("placeholder") ?? "",
    namedByContent ? element.innerText ?? "" : "",
    element.getAttribute("title") ?? "",
    element.getAttribute("alt") ?? "",
    namedByContent ? (element as HTMLInputElement).value ?? "" : "",
  ];

  for (const candidate of candidates) {
    const name = collapseWhitespace(candidate);
    if (name !== "") {
      return truncate(name, maxLength);
    }
  }
  return "";
}

/** Explicit ARIA role first, then a tag/type based mapping. */
export function roleOf(element: HTMLElement): string {
  const explicit = element.getAttribute("role");
  if (explicit !== null && explicit.trim() !== "") {
    return explicit.trim().split(/\s+/)[0] ?? "generic";
  }

  const tag = element.tagName.toLowerCase();
  switch (tag) {
    case "a":
      return element.hasAttribute("href") ? "link" : "generic";
    case "button":
      return "button";
    case "summary":
      return "button";
    case "select":
      return "combobox";
    case "textarea":
      return "textbox";
    case "input": {
      const type = (element as HTMLInputElement).type.toLowerCase();
      if (type === "checkbox") return "checkbox";
      if (type === "radio") return "radio";
      // `type=image` is a submit button with a picture on it, not a text field.
      if (type === "submit" || type === "button" || type === "reset" || type === "image")
        return "button";
      if (type === "range") return "slider";
      if (type === "file") return "button";
      return "textbox";
    }
    default:
      if (element.isContentEditable) {
        return "textbox";
      }
      return "generic";
  }
}

/** The element the user would consider "the page content". */
export function mainContentRoot(): HTMLElement {
  return (
    document.querySelector<HTMLElement>("main") ??
    document.querySelector<HTMLElement>("article") ??
    document.querySelector<HTMLElement>("[role='main']") ??
    document.body
  );
}

/**
 * Visible text of `root`, skipping chrome (nav/footer/aside/script/style) and
 * hidden subtrees, with paragraph breaks preserved.
 *
 * "Chrome" is only skipped when it sits *inside* `root`. An excluded ancestor
 * above `root` must not silence it: `browser.read(ref)` on a sidebar card, a
 * nav link or a footer phone number asks for exactly that text, and matching
 * `closest()` against the whole document returned "" for every one of them.
 */
export function extractVisibleText(root: HTMLElement, maxLength: number): string {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const visibilityCache = new Map<Element, boolean>();
  const parts: string[] = [];
  let length = 0;
  let lastBlock: Element | null = null;

  while (walker.nextNode() !== null) {
    const node = walker.currentNode;
    const raw = node.nodeValue;
    if (raw === null || raw.trim() === "") {
      continue;
    }
    const parent = node.parentElement;
    if (parent === null) {
      continue;
    }
    const excluded = parent.closest(TEXT_EXCLUDED_SELECTOR);
    if (excluded !== null && excluded !== root && root.contains(excluded)) {
      continue;
    }

    let visible = visibilityCache.get(parent);
    if (visible === undefined) {
      visible = isRendered(parent);
      visibilityCache.set(parent, visible);
    }
    if (!visible) {
      continue;
    }

    const block = parent.closest(BLOCK_SELECTOR);
    if (block !== lastBlock && parts.length > 0) {
      parts.push("\n");
      length += 1;
    }
    lastBlock = block;

    const chunk = collapseWhitespace(raw);
    parts.push(chunk, " ");
    length += chunk.length + 1;
    if (length >= maxLength) {
      break;
    }
  }

  const text = parts
    .join("")
    .replace(/[ \t]+/g, " ")
    .replace(/ *\n */g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();

  return truncate(text, maxLength);
}

/**
 * Resolve once the DOM has been quiet for `quietMs`, or after `maxMs`.
 * Used after click/type so the next snapshot sees the settled page.
 */
export function waitForDomIdle(quietMs = 300, maxMs = 1500): Promise<void> {
  return new Promise((resolve) => {
    let quietTimer = 0;
    const observer = new MutationObserver(() => {
      window.clearTimeout(quietTimer);
      quietTimer = window.setTimeout(finish, quietMs);
    });

    const hardTimer = window.setTimeout(finish, maxMs);
    quietTimer = window.setTimeout(finish, quietMs);

    function finish(): void {
      window.clearTimeout(quietTimer);
      window.clearTimeout(hardTimer);
      observer.disconnect();
      resolve();
    }

    observer.observe(document.documentElement, {
      childList: true,
      subtree: true,
      attributes: true,
      characterData: true,
    });
  });
}

/** Bring an element into view and let the scroll settle. */
export async function scrollIntoView(element: HTMLElement): Promise<void> {
  element.scrollIntoView({ block: "center", inline: "nearest" });
  await delay(120);
}
