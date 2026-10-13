/**
 * Console probe — a plain-JS mirror of src/content/{dom,snapshot,actions}.ts.
 *
 * Paste into any page's DevTools console to measure the snapshot without
 * loading the extension:
 *
 *   __aiProbe.snapshot()        → Snapshot object
 *   __aiProbe.measure(5)        → { ms, elements } averaged over 5 runs
 *   __aiProbe.print()           → "[12] button \"Sepete ekle\"" lines (LLM view)
 *   __aiProbe.type(ref, text)   → React-safe typing, same guards as actions.ts
 *   __aiProbe.highlight(ref)    → shadow-DOM overlay
 *
 * KEEP IN SYNC with the TypeScript source. When Node is available, replace this
 * file with `esbuild src/content/snapshot.ts --bundle --format=iife`.
 */

(() => {
  const REF_ATTRIBUTE = "data-ai-ref";
  const MAX_ELEMENTS = 150;
  const MAX_NAME_LENGTH = 80;
  const MAX_TEXT_EXCERPT = 1500;
  const MAX_READ_LENGTH = 4000;
  const MAX_CANDIDATES = 1200;

  const CANDIDATE_SELECTOR = [
    "a[href]", "button", "input:not([type=hidden])", "textarea", "select", "summary",
    "[role=button]", "[role=link]", "[role=checkbox]", "[role=radio]", "[role=tab]",
    "[role=menuitem]", "[role=option]", "[role=combobox]", "[role=textbox]",
    "[contenteditable=true]", "[onclick]", '[tabindex]:not([tabindex="-1"])',
  ].join(", ");

  const TEXT_EXCLUDED_SELECTOR =
    "script, style, noscript, template, svg, nav, footer, aside, [aria-hidden='true'], [hidden]";
  const BLOCK_SELECTOR =
    "p, div, li, tr, td, th, h1, h2, h3, h4, h5, h6, section, article, header, main, " +
    "blockquote, pre, figcaption, dd, dt, summary, label, option";
  const NAMELESS_ALLOWED_ROLES = new Set(["textbox", "checkbox", "radio", "combobox", "slider"]);

  const collapse = (value) => value.replace(/\s+/g, " ").trim();
  const truncate = (value, max) => (value.length <= max ? value : value.slice(0, max));

  function isDisabled(element) {
    if (element.disabled === true) return true;
    return element.getAttribute("aria-disabled") === "true";
  }

  const MIN_INTERACTIVE_SIZE = 2;
  const MAX_CLIP_DEPTH = 20;

  function isClippedAway(element, rect) {
    let parent = element.parentElement;
    let depth = 0;
    while (parent && parent !== document.documentElement && depth < MAX_CLIP_DEPTH) {
      const style = getComputedStyle(parent);
      if (/hidden|clip|scroll|auto/.test(`${style.overflow} ${style.overflowX} ${style.overflowY}`)) {
        const box = parent.getBoundingClientRect();
        if (box.width < MIN_INTERACTIVE_SIZE || box.height < MIN_INTERACTIVE_SIZE) return true;
        if (
          rect.right <= box.left || rect.left >= box.right ||
          rect.bottom <= box.top || rect.top >= box.bottom
        ) return true;
      }
      parent = parent.parentElement;
      depth += 1;
    }
    return false;
  }

  function isScreenReaderOnly(element) {
    const style = getComputedStyle(element);
    if (style.clipPath === "inset(50%)") return true;
    const clip = style.clip.replace(/px|\s/g, "");
    return clip === "rect(0,0,0,0)" || clip === "rect(1,1,1,1)";
  }

  /** Cheap, non-geometric check used while walking text nodes. */
  function isRendered(element) {
    if (typeof element.checkVisibility === "function") {
      return element.checkVisibility({
        checkOpacity: true, checkVisibilityCSS: true, contentVisibilityAuto: true,
        opacityProperty: true, visibilityProperty: true,
      });
    }
    const s = getComputedStyle(element);
    return s.display !== "none" && s.visibility !== "hidden" && s.visibility !== "collapse" && parseFloat(s.opacity||"1") > 0;
  }

  function isVisible(element) {
    const rect = element.getBoundingClientRect();
    if (rect.width < MIN_INTERACTIVE_SIZE || rect.height < MIN_INTERACTIVE_SIZE) return false;
    if (element.closest("[aria-hidden='true']")) return false;
    if (isClippedAway(element, rect) || isScreenReaderOnly(element)) return false;
    if (typeof element.checkVisibility === "function") {
      return element.checkVisibility({
        checkOpacity: true, checkVisibilityCSS: true, contentVisibilityAuto: true,
        opacityProperty: true, visibilityProperty: true,
      });
    }
    const style = getComputedStyle(element);
    return (
      style.display !== "none" &&
      style.visibility !== "hidden" &&
      style.visibility !== "collapse" &&
      parseFloat(style.opacity || "1") > 0
    );
  }

  function textFromLabelledBy(element) {
    const ids = element.getAttribute("aria-labelledby");
    if (!ids) return "";
    return ids.split(/\s+/).map((id) => document.getElementById(id)?.innerText ?? "").join(" ");
  }

  function textFromAssociatedLabel(element) {
    const labels = element.labels;
    if (labels && labels.length > 0) {
      return Array.from(labels).map((label) => label.innerText).join(" ");
    }
    const id = element.getAttribute("id");
    if (id) {
      const label = document.querySelector(`label[for="${id.replace(/"/g, '\\"')}"]`);
      if (label) return label.innerText;
    }
    return element.closest("label")?.innerText ?? "";
  }

  function accessibleName(element, maxLength) {
    const candidates = [
      element.getAttribute("aria-label") ?? "",
      textFromLabelledBy(element),
      textFromAssociatedLabel(element),
      element.getAttribute("placeholder") ?? "",
      element.innerText ?? "",
      element.getAttribute("title") ?? "",
      element.getAttribute("alt") ?? "",
      element.value ?? "",
    ];
    for (const candidate of candidates) {
      const name = collapse(String(candidate));
      if (name !== "") return truncate(name, maxLength);
    }
    return "";
  }

  function roleOf(element) {
    const explicit = element.getAttribute("role");
    if (explicit && explicit.trim() !== "") return explicit.trim().split(/\s+/)[0];
    const tag = element.tagName.toLowerCase();
    if (tag === "a") return element.hasAttribute("href") ? "link" : "generic";
    if (tag === "button" || tag === "summary") return "button";
    if (tag === "select") return "combobox";
    if (tag === "textarea") return "textbox";
    if (tag === "input") {
      const type = (element.type || "text").toLowerCase();
      if (type === "checkbox") return "checkbox";
      if (type === "radio") return "radio";
      if (type === "submit" || type === "button" || type === "reset") return "button";
      if (type === "range") return "slider";
      if (type === "file") return "button";
      return "textbox";
    }
    return element.isContentEditable ? "textbox" : "generic";
  }

  function isPasswordField(element) {
    if (element.tagName !== "INPUT") return false;
    const autocomplete = (element.getAttribute("autocomplete") ?? "").toLowerCase();
    return (
      (element.type || "").toLowerCase() === "password" ||
      autocomplete.includes("password") ||
      autocomplete.includes("cc-")
    );
  }

  function valueOf(element) {
    const tag = element.tagName;
    if (tag === "INPUT") {
      const type = (element.type || "text").toLowerCase();
      if (isPasswordField(element)) return element.value === "" ? "" : "••••";
      if (type === "checkbox" || type === "radio") return element.checked ? "true" : "false";
      return truncate(collapse(element.value), MAX_NAME_LENGTH);
    }
    if (tag === "TEXTAREA") return truncate(collapse(element.value), MAX_NAME_LENGTH);
    if (tag === "SELECT") {
      const selected = element.selectedOptions[0];
      return selected ? truncate(collapse(selected.text), MAX_NAME_LENGTH) : "";
    }
    return null;
  }

  function priorityOf(rect, viewportHeight) {
    if (rect.bottom > 0 && rect.top < viewportHeight) return 0;
    if (rect.top >= viewportHeight && rect.top < viewportHeight * 2) return 1;
    return 2;
  }

  function dropRedundantAncestors(candidates) {
    const byName = new Map();
    for (const candidate of candidates) {
      if (candidate.name === "") continue;
      const bucket = byName.get(candidate.name);
      if (bucket) bucket.push(candidate);
      else byName.set(candidate.name, [candidate]);
    }
    const redundant = new Set();
    for (const bucket of byName.values()) {
      if (bucket.length < 2) continue;
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

  function mainContentRoot() {
    return (
      document.querySelector("main") ??
      document.querySelector("article") ??
      document.querySelector("[role='main']") ??
      document.body
    );
  }

  function extractVisibleText(root, maxLength) {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const cache = new Map();
    const parts = [];
    let length = 0;
    let lastBlock = null;

    while (walker.nextNode()) {
      const node = walker.currentNode;
      const raw = node.nodeValue;
      if (!raw || raw.trim() === "") continue;
      const parent = node.parentElement;
      if (!parent || parent.closest(TEXT_EXCLUDED_SELECTOR)) continue;

      let visible = cache.get(parent);
      if (visible === undefined) {
        visible = isRendered(parent);
        cache.set(parent, visible);
      }
      if (!visible) continue;

      const block = parent.closest(BLOCK_SELECTOR);
      if (block !== lastBlock && parts.length > 0) {
        parts.push("\n");
        length += 1;
      }
      lastBlock = block;

      const chunk = collapse(raw);
      parts.push(chunk, " ");
      length += chunk.length + 1;
      if (length >= maxLength) break;
    }

    return truncate(
      parts.join("").replace(/[ \t]+/g, " ").replace(/ *\n */g, "\n").replace(/\n{3,}/g, "\n\n").trim(),
      maxLength,
    );
  }

  function snapshot() {
    for (const element of document.querySelectorAll(`[${REF_ATTRIBUTE}]`)) {
      element.removeAttribute(REF_ATTRIBUTE);
    }

    const viewportHeight = window.innerHeight;
    const nodes = Array.from(document.querySelectorAll(CANDIDATE_SELECTOR)).slice(0, MAX_CANDIDATES);
    const candidates = [];

    for (const element of nodes) {
      if (isDisabled(element) || !isVisible(element)) continue;
      const role = roleOf(element);
      const name = accessibleName(element, MAX_NAME_LENGTH);
      if (name === "" && !NAMELESS_ALLOWED_ROLES.has(role)) continue;
      const rect = element.getBoundingClientRect();
      candidates.push({
        element, role, name, value: valueOf(element), rect,
        priority: priorityOf(rect, viewportHeight),
      });
    }

    const ordered = dropRedundantAncestors(candidates)
      .sort((a, b) => a.priority - b.priority)
      .slice(0, MAX_ELEMENTS);

    const elements = ordered.map((candidate, index) => {
      const ref = index + 1;
      candidate.element.setAttribute(REF_ATTRIBUTE, String(ref));
      return {
        ref,
        role: candidate.role,
        name: candidate.name,
        value: candidate.value,
        bbox: [
          Math.round(candidate.rect.left), Math.round(candidate.rect.top),
          Math.round(candidate.rect.width), Math.round(candidate.rect.height),
        ],
      };
    });

    return {
      source: "browser",
      url: location.href,
      title: document.title,
      elements,
      text_excerpt: extractVisibleText(mainContentRoot(), MAX_TEXT_EXCERPT),
      shot: null,
    };
  }

  const elementByRef = (ref) => document.querySelector(`[${REF_ATTRIBUTE}="${ref}"]`);

  function setNativeValue(element, value) {
    const prototype =
      element.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
    if (setter) setter.call(element, value);
    else element.value = value;
    element.dispatchEvent(
      typeof InputEvent === "function"
        ? new InputEvent("input", { bubbles: true, data: value, inputType: "insertText" })
        : new Event("input", { bubbles: true }),
    );
    element.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function type(ref, text, submit = false) {
    const element = elementByRef(ref);
    if (!element) return { ok: false, error: "ref bulunamadı, yeni snapshot al" };
    const autocomplete = (element.getAttribute("autocomplete") ?? "").toLowerCase();
    if (
      (element.tagName === "INPUT" && (element.type || "").toLowerCase() === "password") ||
      autocomplete.includes("password") ||
      autocomplete.includes("cc-")
    ) {
      return { ok: false, error: "password_field_blocked: şifre/kart alanına yazılmaz" };
    }
    element.focus?.({ preventScroll: true });
    if (element.tagName === "INPUT" || element.tagName === "TEXTAREA") {
      setNativeValue(element, text);
    } else if (element.isContentEditable) {
      const range = document.createRange();
      range.selectNodeContents(element);
      const selection = getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      document.execCommand("insertText", false, text);
    } else {
      return { ok: false, error: `bu öğeye yazılamaz (role=${roleOf(element)})` };
    }
    if (submit) {
      const form = element.closest("form");
      if (form) form.requestSubmit();
    }
    return { ok: true, submitted: submit };
  }

  function highlight(ref) {
    const element = elementByRef(ref);
    if (!element) return { ok: false, error: "ref bulunamadı, yeni snapshot al" };
    element.scrollIntoView({ block: "center", inline: "nearest" });
    document.getElementById("ai-assistant-highlight-host")?.remove();
    const host = document.createElement("div");
    host.id = "ai-assistant-highlight-host";
    host.setAttribute("aria-hidden", "true");
    host.style.cssText =
      "position:fixed;top:0;left:0;width:0;height:0;margin:0;padding:0;border:0;" +
      "pointer-events:none;z-index:2147483647;";
    document.documentElement.appendChild(host);
    const root = host.attachShadow({ mode: "closed" });
    const style = document.createElement("style");
    style.textContent =
      ":host{all:initial}.box{position:fixed;box-sizing:border-box;border:3px solid #ff5a1f;" +
      "border-radius:6px;background:rgba(255,90,31,.08);box-shadow:0 0 0 3px rgba(255,90,31,.25);" +
      "pointer-events:none;animation:b 750ms ease-in-out 2}" +
      "@keyframes b{0%,100%{opacity:1}50%{opacity:.15}}";
    root.appendChild(style);
    const rect = element.getBoundingClientRect();
    const box = document.createElement("div");
    box.className = "box";
    box.style.left = `${Math.round(rect.left) - 2}px`;
    box.style.top = `${Math.round(rect.top) - 2}px`;
    box.style.width = `${Math.round(rect.width) + 4}px`;
    box.style.height = `${Math.round(rect.height) + 4}px`;
    root.appendChild(box);
    setTimeout(() => host.remove(), 1500);
    return { ok: true };
  }

  function read(ref) {
    const root = ref === undefined ? mainContentRoot() : elementByRef(ref);
    if (!root) return { error: "ref bulunamadı, yeni snapshot al" };
    return { text: extractVisibleText(root, MAX_READ_LENGTH) };
  }

  function measure(runs = 5) {
    const times = [];
    let count = 0;
    for (let index = 0; index < runs; index += 1) {
      const started = performance.now();
      const result = snapshot();
      times.push(performance.now() - started);
      count = result.elements.length;
    }
    times.sort((a, b) => a - b);
    return {
      elements: count,
      median_ms: Math.round(times[Math.floor(times.length / 2)] * 10) / 10,
      max_ms: Math.round(times[times.length - 1] * 10) / 10,
      candidates: document.querySelectorAll(CANDIDATE_SELECTOR).length,
    };
  }

  const print = () =>
    snapshot()
      .elements.map((element) => `[${element.ref}] ${element.role} "${element.name}"`)
      .join("\n");

  window.__aiProbe = { snapshot, measure, print, type, read, highlight, elementByRef };
  return "__aiProbe hazır";
})();
