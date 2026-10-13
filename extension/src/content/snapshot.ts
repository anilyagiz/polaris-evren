/**
 * DOM → numbered element list.
 *
 * The LLM never sees pixels: it sees `[12] button "Sepete ekle"` and answers
 * with `click(12)`. Refs live on the page as `data-ai-ref` attributes and are
 * renumbered from 1 on every snapshot, so a stale ref can never silently point
 * at the wrong element — it simply does not resolve.
 */

import {
  MAX_ELEMENTS,
  MAX_NAME_LENGTH,
  MAX_TEXT_EXCERPT,
  type Snapshot,
  type SnapshotElement,
} from "./types";
import {
  type ClipCache,
  REF_ATTRIBUTE,
  accessibleName,
  collapseWhitespace,
  extractVisibleText,
  isDisabled,
  isVisible,
  mainContentRoot,
  roleOf,
  truncate,
} from "./dom";

const CANDIDATE_SELECTOR = [
  "a[href]",
  "button",
  "input:not([type=hidden])",
  "textarea",
  "select",
  "summary",
  "[role=button]",
  "[role=link]",
  "[role=checkbox]",
  "[role=radio]",
  "[role=tab]",
  "[role=menuitem]",
  "[role=option]",
  "[role=combobox]",
  "[role=textbox]",
  "[contenteditable=true]",
  "[onclick]",
  '[tabindex]:not([tabindex="-1"])',
].join(", ");

/** Hard cap on how many nodes we inspect, so huge pages stay under ~300 ms. */
const MAX_CANDIDATES = 1200;

/** Form controls are worth listing even without a name (the LLM can still type). */
const NAMELESS_ALLOWED_ROLES = new Set(["textbox", "checkbox", "radio", "combobox", "slider"]);

interface Candidate {
  element: HTMLElement;
  role: string;
  name: string;
  value: string | null;
  rect: DOMRect;
  priority: number;
}

function isPasswordField(element: HTMLElement): boolean {
  if (!(element instanceof HTMLInputElement)) {
    return false;
  }
  const autocomplete = (element.getAttribute("autocomplete") ?? "").toLowerCase();
  return (
    element.type.toLowerCase() === "password" ||
    autocomplete.includes("password") ||
    autocomplete.includes("cc-")
  );
}

function valueOf(element: HTMLElement): string | null {
  if (element instanceof HTMLInputElement) {
    const type = element.type.toLowerCase();
    if (isPasswordField(element)) {
      return element.value === "" ? "" : "••••";
    }
    if (type === "checkbox" || type === "radio") {
      return element.checked ? "true" : "false";
    }
    return truncate(collapseWhitespace(element.value), MAX_NAME_LENGTH);
  }
  if (element instanceof HTMLTextAreaElement) {
    return truncate(collapseWhitespace(element.value), MAX_NAME_LENGTH);
  }
  if (element instanceof HTMLSelectElement) {
    const selected = element.selectedOptions[0];
    return selected === undefined ? "" : truncate(collapseWhitespace(selected.text), MAX_NAME_LENGTH);
  }
  return null;
}

/**
 * 0 = inside the viewport, 1 = within one screen below it, 2 = anywhere else.
 * Stable sorting keeps DOM order inside each bucket, which reads naturally.
 *
 * Both axes count. A closed off-canvas drawer (`left: -320px`, or translated
 * out) overlaps the viewport vertically, so judging on `top`/`bottom` alone put
 * its whole link list in bucket 0 — ahead of the search box actually on screen,
 * and ahead of everything the 150-element budget then had no room for.
 * Scrolling can reach a row below the fold; it cannot reach a closed drawer.
 */
function priorityOf(rect: DOMRect, viewportWidth: number, viewportHeight: number): number {
  if (rect.right <= 0 || rect.left >= viewportWidth) {
    return 2;
  }
  if (rect.bottom > 0 && rect.top < viewportHeight) {
    return 0;
  }
  if (rect.top >= viewportHeight && rect.top < viewportHeight * 2) {
    return 1;
  }
  return 2;
}

/**
 * Drop wrappers that duplicate an inner candidate (e.g. `<a>` around a
 * `<button>` with the same label): the innermost one is the better target.
 *
 * Walking each candidate's ancestor chain picks out exactly the same wrappers
 * as comparing every pair inside a same-name bucket, at O(candidates × depth)
 * instead of O(bucket²): a product grid of 150 identically labelled "Sepete
 * ekle" cards (450 candidates, one bucket) spent ~135k `contains()` calls to
 * reach this answer, and 600 cards ~960k.
 */
function dropRedundantAncestors(candidates: Candidate[]): Candidate[] {
  const byElement = new Map<Element, Candidate>();
  for (const candidate of candidates) {
    if (candidate.name !== "") {
      byElement.set(candidate.element, candidate);
    }
  }

  const redundant = new Set<Element>();
  for (const candidate of candidates) {
    if (candidate.name === "") {
      continue;
    }
    let parent = candidate.element.parentElement;
    while (parent !== null) {
      if (byElement.get(parent)?.name === candidate.name) {
        redundant.add(parent);
      }
      parent = parent.parentElement;
    }
  }

  return candidates.filter((candidate) => !redundant.has(candidate.element));
}

function clearPreviousRefs(): void {
  for (const element of document.querySelectorAll(`[${REF_ATTRIBUTE}]`)) {
    element.removeAttribute(REF_ATTRIBUTE);
  }
}

export function takeSnapshot(): Snapshot {
  clearPreviousRefs();

  const viewportWidth = window.innerWidth;
  const viewportHeight = window.innerHeight;

  // Measure first, inspect second. Reading a rect is cheap once layout is up to
  // date; the expensive part per candidate is `isVisible()` (a computed style
  // per ancestor) and `accessibleName()` (`innerText`). So the budget is spent
  // on the nodes nearest the viewport instead of on the first 1200 in DOM
  // order: on a long page the user has already scrolled through — an infinite
  // feed, a big catalogue — everything on screen sits *past* node 1200, and a
  // DOM-order cut handed the LLM 150 refs from the top of the page and nothing
  // it could actually see.
  const measured = Array.from(document.querySelectorAll<HTMLElement>(CANDIDATE_SELECTOR)).map(
    (element) => {
      const rect = element.getBoundingClientRect();
      return { element, rect, priority: priorityOf(rect, viewportWidth, viewportHeight) };
    },
  );
  if (measured.length > MAX_CANDIDATES) {
    // Stable, so DOM order survives inside each priority bucket.
    measured.sort((a, b) => a.priority - b.priority);
    measured.length = MAX_CANDIDATES;
  }

  // Candidates share nearly all of their ancestors, so the clipping walk is
  // measured once per ancestor instead of once per candidate.
  const clipCache: ClipCache = new Map();

  const candidates: Candidate[] = [];
  for (const { element, rect, priority } of measured) {
    if (isDisabled(element) || !isVisible(element, rect, clipCache)) {
      continue;
    }
    const role = roleOf(element);
    const name = accessibleName(element, MAX_NAME_LENGTH);
    if (name === "" && !NAMELESS_ALLOWED_ROLES.has(role)) {
      continue;
    }
    candidates.push({ element, role, name, value: valueOf(element), rect, priority });
  }

  const ordered = dropRedundantAncestors(candidates)
    .sort((a, b) => a.priority - b.priority)
    .slice(0, MAX_ELEMENTS);

  const elements: SnapshotElement[] = ordered.map((candidate, index) => {
    const ref = index + 1;
    candidate.element.setAttribute(REF_ATTRIBUTE, String(ref));
    return {
      ref,
      role: candidate.role,
      name: candidate.name,
      value: candidate.value,
      bbox: [
        Math.round(candidate.rect.left),
        Math.round(candidate.rect.top),
        Math.round(candidate.rect.width),
        Math.round(candidate.rect.height),
      ],
    };
  });

  return {
    source: "browser",
    url: window.location.href,
    title: document.title,
    elements,
    text_excerpt: extractVisibleText(mainContentRoot(), MAX_TEXT_EXCERPT),
    shot: null,
  };
}
