/**
 * Entry point for `test/snapshot.probe.js`, the console probe.
 *
 * The probe used to be a hand-written JS mirror of the content layer, which
 * drifted the moment the real code changed. It is now GENERATED from this
 * file, so it can never disagree with production:
 *
 *   npx esbuild src/dev/probe.entry.ts --bundle --format=iife --target=chrome110 \
 *     --outfile=test/snapshot.probe.js
 *
 * Paste the generated file into any page's DevTools console, then:
 *   __aiProbe.measure(5)  -> timing + element counts for test/NOTES.md
 *   __aiProbe.print()     -> the exact `[12] button "Sepete ekle"` list the LLM sees
 */

import * as actions from "../content/actions";
import { takeSnapshot } from "../content/snapshot";
import type { Snapshot } from "../content/types";

const CANDIDATE_COUNT_SELECTOR = [
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

interface Measurement {
  url: string;
  dom_nodes: number;
  candidates: number;
  elements: number;
  capped: boolean;
  median_ms: number;
  max_ms: number;
  excerpt_len: number;
  nameless: number;
  first6: string[];
}

function measure(runs = 5): Measurement {
  const times: number[] = [];
  let snapshot: Snapshot = takeSnapshot();

  for (let index = 0; index < runs; index += 1) {
    const started = performance.now();
    snapshot = takeSnapshot();
    times.push(performance.now() - started);
  }
  times.sort((a, b) => a - b);

  const round = (value: number): number => Math.round(value * 10) / 10;
  return {
    url: window.location.href.slice(0, 70),
    dom_nodes: document.querySelectorAll("*").length,
    candidates: document.querySelectorAll(CANDIDATE_COUNT_SELECTOR).length,
    elements: snapshot.elements.length,
    capped: snapshot.elements.length >= 150,
    median_ms: round(times[Math.floor(times.length / 2)] ?? 0),
    max_ms: round(times[times.length - 1] ?? 0),
    excerpt_len: snapshot.text_excerpt.length,
    nameless: snapshot.elements.filter((element) => element.name === "").length,
    first6: snapshot.elements
      .slice(0, 6)
      .map((element) => `[${element.ref}] ${element.role} "${element.name}"`),
  };
}

function print(): string {
  return takeSnapshot()
    .elements.map((element) => `[${element.ref}] ${element.role} "${element.name}"`)
    .join("\n");
}

const probe = {
  snapshot: takeSnapshot,
  measure,
  print,
  click: actions.click,
  type: actions.type,
  scroll: actions.scroll,
  read: actions.read,
  highlight: actions.highlight,
};

declare global {
  interface Window {
    __aiProbe?: typeof probe;
  }
}

window.__aiProbe = probe;
