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
 */
function priorityOf(rect: DOMRect, viewportHeight: number): number {
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
 */
function dropRedundantAncestors(candidates: Candidate[]): Candidate[] {
  const byName = new Map<string, Candidate[]>();
  for (const candidate of candidates) {
    if (candidate.name === "") {
      continue;
    }
    const bucket = byName.get(candidate.name);
    if (bucket === undefined) {
      byName.set(candidate.name, [candidate]);
    } else {
      bucket.push(candidate);
    }
  }

  const redundant = new Set<HTMLElement>();
  for (const bucket of byName.values()) {
    if (bucket.length < 2) {
      continue;
    }
    for (const outer of bucket) {
      for (const inner of bucket) {
        if (outer !== inner && outer.element.contains(inner.element)) {
          redundant.add(outer.element);
          break;
        }
      }
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

  const viewportHeight = window.innerHeight;
  const nodes = Array.from(document.querySelectorAll<HTMLElement>(CANDIDATE_SELECTOR)).slice(
    0,
    MAX_CANDIDATES,
  );

  const candidates: Candidate[] = [];
  for (const element of nodes) {
    if (isDisabled(element) || !isVisible(element)) {
      continue;
    }
    const role = roleOf(element);
    const name = accessibleName(element, MAX_NAME_LENGTH);
    if (name === "" && !NAMELESS_ALLOWED_ROLES.has(role)) {
      continue;
    }
    const rect = element.getBoundingClientRect();
    candidates.push({
      element,
      role,
      name,
      value: valueOf(element),
      rect,
      priority: priorityOf(rect, viewportHeight),
    });
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
