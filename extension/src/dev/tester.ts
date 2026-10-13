/**
 * Development-only page: call any `browser.*` tool against the active tab and
 * inspect the raw JSON result. Not part of the product build surface; handy
 * when debugging a site where the Executor misbehaves.
 */

import { executeBrowserTool } from "../bridge/executeTool";
import type { Snapshot, SnapshotElement, ToolResponse } from "../content/types";

interface Preset {
  name: string;
  args: Record<string, unknown>;
}

const PRESETS: Preset[] = [
  { name: "browser.snapshot", args: {} },
  { name: "browser.click", args: { ref: 1 } },
  { name: "browser.type", args: { ref: 1, text: "mekanik klavye", submit: false } },
  { name: "browser.scroll", args: { direction: "down" } },
  { name: "browser.navigate", args: { url: "https://search.brave.com" } },
  { name: "browser.read", args: {} },
  { name: "browser.highlight", args: { ref: 1 } },
];

const toolsEl = document.getElementById("tools") as HTMLDivElement;
const argsEl = document.getElementById("args") as HTMLTextAreaElement;
const tabIdEl = document.getElementById("tabId") as HTMLSelectElement;
const refreshTabsEl = document.getElementById("refreshTabs") as HTMLButtonElement;
const runEl = document.getElementById("run") as HTMLButtonElement;
const outEl = document.getElementById("out") as HTMLPreElement;
const metaEl = document.getElementById("meta") as HTMLParagraphElement;
const elementsEl = document.getElementById("elements") as HTMLDivElement;

let selected = PRESETS[0] as Preset;

function renderTools(): void {
  toolsEl.replaceChildren();
  for (const preset of PRESETS) {
    const button = document.createElement("button");
    button.textContent = preset.name.replace("browser.", "");
    button.setAttribute("aria-pressed", String(preset.name === selected.name));
    button.addEventListener("click", () => {
      selected = preset;
      argsEl.value = JSON.stringify(preset.args, null, 2);
      renderTools();
    });
    toolsEl.appendChild(button);
  }
}

function isSnapshot(data: unknown): data is Snapshot {
  return (
    typeof data === "object" &&
    data !== null &&
    Array.isArray((data as { elements?: unknown }).elements)
  );
}

function renderElements(elements: SnapshotElement[]): void {
  elementsEl.replaceChildren();
  const table = document.createElement("table");
  const head = document.createElement("tr");
  for (const title of ["ref", "role", "name", "value", "bbox"]) {
    const th = document.createElement("th");
    th.textContent = title;
    head.appendChild(th);
  }
  table.appendChild(head);

  for (const element of elements) {
    const row = document.createElement("tr");
    const cells = [
      String(element.ref),
      element.role,
      element.name,
      element.value ?? "",
      element.bbox.join(", "),
    ];
    for (const value of cells) {
      const td = document.createElement("td");
      td.textContent = value;
      row.appendChild(td);
    }
    table.appendChild(row);
  }
  elementsEl.appendChild(table);
}

/**
 * List the tabs we could drive. The tester page is itself a tab, so leaving the
 * target implicit would resolve to this page — which the bridge correctly
 * refuses as a restricted URL. Pick explicitly instead.
 */
async function loadTabs(): Promise<void> {
  const ownPrefix = chrome.runtime.getURL("");
  const tabs = await chrome.tabs.query({ windowType: "normal" });
  const usable = tabs.filter(
    (tab) => tab.id !== undefined && !(tab.url ?? "").startsWith(ownPrefix),
  );

  tabIdEl.replaceChildren();
  if (usable.length === 0) {
    const option = document.createElement("option");
    option.textContent = "sürülebilir sekme yok — bir sayfa aç";
    option.value = "";
    tabIdEl.appendChild(option);
    return;
  }

  for (const tab of usable) {
    const option = document.createElement("option");
    option.value = String(tab.id);
    const title = (tab.title ?? tab.url ?? "").slice(0, 60);
    option.textContent = `${tab.id} · ${title}`;
    option.selected = tab.active === true;
    tabIdEl.appendChild(option);
  }
}

async function run(): Promise<void> {
  elementsEl.replaceChildren();
  metaEl.className = "meta";

  let args: Record<string, unknown>;
  try {
    args = JSON.parse(argsEl.value || "{}") as Record<string, unknown>;
  } catch (error: unknown) {
    metaEl.className = "meta error";
    metaEl.textContent = `Argüman JSON'u geçersiz: ${String(error)}`;
    return;
  }

  const rawTabId = tabIdEl.value.trim();
  if (rawTabId === "") {
    metaEl.className = "meta error";
    metaEl.textContent = "Önce bir hedef sekme seç.";
    return;
  }
  const tabId = Number.parseInt(rawTabId, 10);

  runEl.disabled = true;
  metaEl.textContent = `${selected.name} çalışıyor…`;
  const started = performance.now();
  const response: ToolResponse = await executeBrowserTool(selected.name, args, tabId);
  const elapsed = Math.round(performance.now() - started);
  runEl.disabled = false;

  outEl.textContent = JSON.stringify(response, null, 2);
  const count = isSnapshot(response.data) ? response.data.elements.length : null;
  metaEl.className = response.ok ? "meta" : "meta error";
  metaEl.textContent = [
    `${selected.name} · ${elapsed} ms`,
    response.ok ? "ok" : `hata: ${response.error ?? ""}`,
    count === null ? "" : `${count} öğe`,
  ]
    .filter((part) => part !== "")
    .join(" · ");

  if (isSnapshot(response.data)) {
    renderElements(response.data.elements);
  }
}

runEl.addEventListener("click", () => {
  void run();
});

refreshTabsEl.addEventListener("click", () => {
  void loadTabs();
});

argsEl.value = JSON.stringify(selected.args, null, 2);
renderTools();
void loadTabs();
