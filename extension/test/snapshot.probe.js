/* GENERATED from src/dev/probe.entry.ts - do not edit by hand. Rebuild: npx esbuild src/dev/probe.entry.ts --bundle --format=iife --target=chrome110 --outfile=test/snapshot.probe.js */
"use strict";
(() => {
  // src/content/types.ts
  var MAX_ELEMENTS = 150;
  var MAX_NAME_LENGTH = 80;
  var MAX_TEXT_EXCERPT = 1500;
  var MAX_READ_LENGTH = 4e3;
  function ok(data) {
    return { ok: true, data, error: null };
  }
  function fail(error) {
    return { ok: false, data: null, error };
  }

  // src/content/dom.ts
  var REF_ATTRIBUTE = "data-ai-ref";
  var TEXT_EXCLUDED_SELECTOR = "script, style, noscript, template, svg, nav, footer, aside, [aria-hidden='true'], [hidden]";
  var BLOCK_SELECTOR = "p, div, li, tr, td, th, h1, h2, h3, h4, h5, h6, section, article, header, main, blockquote, pre, figcaption, dd, dt, summary, label, option";
  function collapseWhitespace(value) {
    return value.replace(/\s+/g, " ").trim();
  }
  function truncate(value, max) {
    return value.length <= max ? value : value.slice(0, max);
  }
  function delay(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }
  function elementByRef(ref) {
    return document.querySelector(`[${REF_ATTRIBUTE}="${ref}"]`);
  }
  function isDisabled(element) {
    if ("disabled" in element && element.disabled === true) {
      return true;
    }
    return element.getAttribute("aria-disabled") === "true";
  }
  var MIN_INTERACTIVE_SIZE = 2;
  var MAX_CLIP_DEPTH = 20;
  function clipFactsOf(parent, cache) {
    const cached = cache?.get(parent);
    if (cached !== void 0) {
      return cached;
    }
    const style = window.getComputedStyle(parent);
    const overflow = `${style.overflow} ${style.overflowX} ${style.overflowY}`;
    const clips = /hidden|clip|scroll|auto/.test(overflow);
    const facts = {
      clips,
      box: clips ? parent.getBoundingClientRect() : null,
      fixed: style.position === "fixed"
    };
    cache?.set(parent, facts);
    return facts;
  }
  function isClippedAway(element, rect, position, cache) {
    if (position === "fixed") {
      return false;
    }
    let parent = element.parentElement;
    let depth = 0;
    while (parent !== null && parent !== document.documentElement && depth < MAX_CLIP_DEPTH) {
      const facts = clipFactsOf(parent, cache);
      if (facts.clips && facts.box !== null) {
        const box = facts.box;
        if (box.width < MIN_INTERACTIVE_SIZE || box.height < MIN_INTERACTIVE_SIZE) {
          return true;
        }
        if (rect.right <= box.left || rect.left >= box.right || rect.bottom <= box.top || rect.top >= box.bottom) {
          return true;
        }
      }
      if (facts.fixed) {
        return false;
      }
      parent = parent.parentElement;
      depth += 1;
    }
    return false;
  }
  function isScreenReaderOnly(style) {
    if (style.clipPath === "inset(50%)") {
      return true;
    }
    const clip = style.clip.replace(/px|\s/g, "");
    return clip === "rect(0,0,0,0)" || clip === "rect(1,1,1,1)";
  }
  function isVisible(element, knownRect, cache) {
    const rect = knownRect ?? element.getBoundingClientRect();
    if (rect.width < MIN_INTERACTIVE_SIZE || rect.height < MIN_INTERACTIVE_SIZE) {
      return false;
    }
    if (element.closest("[aria-hidden='true']") !== null) {
      return false;
    }
    const style = window.getComputedStyle(element);
    if (isScreenReaderOnly(style) || isClippedAway(element, rect, style.position, cache)) {
      return false;
    }
    const withCheck = element;
    if (typeof withCheck.checkVisibility === "function") {
      return withCheck.checkVisibility({
        checkOpacity: true,
        checkVisibilityCSS: true,
        contentVisibilityAuto: true,
        opacityProperty: true,
        visibilityProperty: true
      });
    }
    return style.display !== "none" && style.visibility !== "hidden" && style.visibility !== "collapse" && Number.parseFloat(style.opacity || "1") > 0;
  }
  function isRendered(element) {
    const withCheck = element;
    if (typeof withCheck.checkVisibility === "function") {
      return withCheck.checkVisibility({
        checkOpacity: true,
        checkVisibilityCSS: true,
        contentVisibilityAuto: true,
        opacityProperty: true,
        visibilityProperty: true
      });
    }
    const style = window.getComputedStyle(element);
    return style.display !== "none" && style.visibility !== "hidden" && style.visibility !== "collapse" && Number.parseFloat(style.opacity || "1") > 0;
  }
  function textFromLabelledBy(element) {
    const ids = element.getAttribute("aria-labelledby");
    if (ids === null) {
      return "";
    }
    return ids.split(/\s+/).map((id) => document.getElementById(id)?.innerText ?? "").join(" ");
  }
  function textFromAssociatedLabel(element) {
    const labels = element.labels;
    if (labels && labels.length > 0) {
      return Array.from(labels).map((label) => label.innerText).join(" ");
    }
    const id = element.getAttribute("id");
    if (id !== null && id !== "") {
      const escaped = id.replace(/["\\]/g, "\\$&");
      const label = document.querySelector(`label[for="${escaped}"]`);
      if (label !== null) {
        return label.innerText;
      }
    }
    return element.closest("label")?.innerText ?? "";
  }
  function accessibleName(element, maxLength) {
    const namedByContent = !(element instanceof HTMLSelectElement);
    const candidates = [
      element.getAttribute("aria-label") ?? "",
      textFromLabelledBy(element),
      textFromAssociatedLabel(element),
      element.getAttribute("placeholder") ?? "",
      namedByContent ? element.innerText ?? "" : "",
      element.getAttribute("title") ?? "",
      element.getAttribute("alt") ?? "",
      namedByContent ? element.value ?? "" : ""
    ];
    for (const candidate of candidates) {
      const name = collapseWhitespace(candidate);
      if (name !== "") {
        return truncate(name, maxLength);
      }
    }
    return "";
  }
  function roleOf(element) {
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
        const type2 = element.type.toLowerCase();
        if (type2 === "checkbox") return "checkbox";
        if (type2 === "radio") return "radio";
        if (type2 === "submit" || type2 === "button" || type2 === "reset" || type2 === "image")
          return "button";
        if (type2 === "range") return "slider";
        if (type2 === "file") return "button";
        return "textbox";
      }
      default:
        if (element.isContentEditable) {
          return "textbox";
        }
        return "generic";
    }
  }
  function mainContentRoot() {
    return document.querySelector("main") ?? document.querySelector("article") ?? document.querySelector("[role='main']") ?? document.body;
  }
  function extractVisibleText(root, maxLength) {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const visibilityCache = /* @__PURE__ */ new Map();
    const parts = [];
    let length = 0;
    let lastBlock = null;
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
      if (visible === void 0) {
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
    const text = parts.join("").replace(/[ \t]+/g, " ").replace(/ *\n */g, "\n").replace(/\n{3,}/g, "\n\n").trim();
    return truncate(text, maxLength);
  }
  function waitForDomIdle(quietMs = 300, maxMs = 1500) {
    return new Promise((resolve) => {
      let quietTimer = 0;
      const observer = new MutationObserver(() => {
        window.clearTimeout(quietTimer);
        quietTimer = window.setTimeout(finish, quietMs);
      });
      const hardTimer = window.setTimeout(finish, maxMs);
      quietTimer = window.setTimeout(finish, quietMs);
      function finish() {
        window.clearTimeout(quietTimer);
        window.clearTimeout(hardTimer);
        observer.disconnect();
        resolve();
      }
      observer.observe(document.documentElement, {
        childList: true,
        subtree: true,
        attributes: true,
        characterData: true
      });
    });
  }
  async function scrollIntoView(element) {
    element.scrollIntoView({ block: "center", inline: "nearest" });
    await delay(120);
  }

  // src/content/highlight.ts
  var HOST_ID = "ai-assistant-highlight-host";
  var BLINK_MS = 1500;
  var shadow = null;
  var STYLE = `
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
  function ensureShadow() {
    if (shadow !== null && shadow.host.isConnected) {
      return shadow;
    }
    const existing = document.getElementById(HOST_ID);
    existing?.remove();
    const host = document.createElement("div");
    host.id = HOST_ID;
    host.setAttribute("aria-hidden", "true");
    host.style.cssText = "position:fixed;top:0;left:0;width:0;height:0;margin:0;padding:0;border:0;pointer-events:none;z-index:2147483647;";
    document.documentElement.appendChild(host);
    const root = host.attachShadow({ mode: "closed" });
    const style = document.createElement("style");
    style.textContent = STYLE;
    root.appendChild(style);
    shadow = root;
    return root;
  }
  function placeBox(box, element) {
    const rect = element.getBoundingClientRect();
    if (rect.width === 0 && rect.height === 0) {
      box.style.display = "none";
      return;
    }
    box.style.display = "block";
    box.style.left = `${Math.round(rect.left) - 2}px`;
    box.style.top = `${Math.round(rect.top) - 2}px`;
    box.style.width = `${Math.round(rect.width) + 4}px`;
    box.style.height = `${Math.round(rect.height) + 4}px`;
  }
  function followElement(box, element) {
    if (typeof requestAnimationFrame !== "function") {
      return () => {
      };
    }
    let frame = requestAnimationFrame(function tick() {
      if (!box.isConnected) {
        return;
      }
      placeBox(box, element);
      frame = requestAnimationFrame(tick);
    });
    return () => cancelAnimationFrame(frame);
  }
  async function highlightElement(element) {
    await scrollIntoView(element);
    const root = ensureShadow();
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

  // src/content/actions.ts
  var REF_NOT_FOUND = "ref bulunamad\u0131, yeni snapshot al";
  var MASKED_VALUE = "\u2022\u2022\u2022\u2022";
  function readRef(args) {
    const ref = args.ref;
    if (typeof ref === "number" && Number.isInteger(ref)) {
      return ref;
    }
    if (typeof ref === "string" && /^\d+$/.test(ref)) {
      return Number.parseInt(ref, 10);
    }
    return null;
  }
  function resolveTarget(args) {
    const ref = readRef(args);
    if (ref === null) {
      return { error: "ref eksik ya da say\u0131 de\u011Fil" };
    }
    const element = elementByRef(ref);
    if (element === null) {
      return { error: REF_NOT_FOUND };
    }
    return { element };
  }
  var PROTECTED_PATTERNS = [
    /pass(word|wd|phrase)/,
    /\bpwd\b/,
    /şifre|sifre|parola/,
    /\bcvv\b|\bcvc\b|\bcsc\b|\bcvn\b/,
    /\bpin\b/,
    /card[-_ ]?(number|num|no|code|pan)/,
    /credit[-_ ]?card|debit[-_ ]?card/,
    /kart[-_ ]?(numara|no|bilgi)/,
    /kredi[-_ ]?kart|banka[-_ ]?kart/,
    /security[-_ ]?code|g[uü]venlik[-_ ]?kod/,
    /\biban\b/
  ];
  function isProtectedField(element) {
    if (element instanceof HTMLInputElement && element.type.toLowerCase() === "password") {
      return true;
    }
    const autocomplete = (element.getAttribute("autocomplete") ?? "").toLowerCase();
    if (autocomplete.includes("password") || autocomplete.includes("cc-")) {
      return true;
    }
    const haystack = [
      element.getAttribute("name") ?? "",
      element.getAttribute("id") ?? "",
      element.getAttribute("placeholder") ?? "",
      element.getAttribute("data-testid") ?? "",
      autocomplete,
      accessibleName(element, 120)
    ].join(" ").toLowerCase();
    return PROTECTED_PATTERNS.some((pattern) => pattern.test(haystack));
  }
  var NON_TYPABLE_INPUT_TYPES = /* @__PURE__ */ new Set([
    "checkbox",
    "radio",
    "button",
    "submit",
    "reset",
    "image",
    "file"
  ]);
  function nativeValueSetter(element) {
    let prototype;
    if (element instanceof HTMLTextAreaElement) {
      prototype = HTMLTextAreaElement.prototype;
    } else if (element instanceof HTMLSelectElement) {
      prototype = HTMLSelectElement.prototype;
    } else {
      prototype = HTMLInputElement.prototype;
    }
    const setter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
    return setter === void 0 ? null : (value) => setter.call(element, value);
  }
  function dispatchInputAndChange(element, value) {
    const input = typeof InputEvent === "function" ? new InputEvent("input", { bubbles: true, data: value, inputType: "insertText" }) : new Event("input", { bubbles: true });
    element.dispatchEvent(input);
    element.dispatchEvent(new Event("change", { bubbles: true }));
  }
  function setNativeValue(element, value) {
    const setter = nativeValueSetter(element);
    if (setter !== null) {
      setter(value);
    } else {
      element.value = value;
    }
    dispatchInputAndChange(element, value);
  }
  function dispatchEnter(element) {
    for (const type2 of ["keydown", "keypress", "keyup"]) {
      const event = new KeyboardEvent(type2, {
        key: "Enter",
        code: "Enter",
        bubbles: true,
        cancelable: true
      });
      Object.defineProperty(event, "keyCode", { get: () => 13 });
      Object.defineProperty(event, "which", { get: () => 13 });
      element.dispatchEvent(event);
    }
  }
  function typeIntoContentEditable(element, text) {
    element.focus();
    const range = document.createRange();
    range.selectNodeContents(element);
    const selection = window.getSelection();
    selection?.removeAllRanges();
    selection?.addRange(range);
    const exec = document.execCommand;
    if (typeof exec === "function") {
      let inserted = false;
      try {
        inserted = exec.call(document, "insertText", false, text);
      } catch {
        inserted = false;
      }
      if (inserted) {
        return true;
      }
    }
    element.textContent = text;
    dispatchInputAndChange(element, text);
    return element.textContent === text;
  }
  function selectOption(element, text) {
    const wanted = text.trim().toLowerCase();
    for (const option of Array.from(element.options)) {
      if (option.value.toLowerCase() === wanted || option.text.trim().toLowerCase() === wanted) {
        const setter = nativeValueSetter(element);
        if (setter !== null) {
          setter(option.value);
        } else {
          element.value = option.value;
        }
        dispatchInputAndChange(element, option.value);
        return true;
      }
    }
    return false;
  }
  function formOf(element) {
    const owned = element.form;
    if (owned instanceof HTMLFormElement) {
      return owned;
    }
    return element.closest("form");
  }
  function firstInvalidField(form) {
    if (form.noValidate) {
      return null;
    }
    for (const member of Array.from(form.elements)) {
      const control = member;
      if (typeof control.willValidate !== "boolean" || !control.willValidate) {
        continue;
      }
      if (control.validity !== void 0 && !control.validity.valid) {
        return control;
      }
    }
    return null;
  }
  function describeField(element) {
    const name = accessibleName(element, 40);
    if (name !== "") {
      return name;
    }
    return element.getAttribute("name") ?? element.getAttribute("id") ?? roleOf(element);
  }
  function submitForm(form, element) {
    if (typeof form.requestSubmit === "function") {
      try {
        form.requestSubmit();
        return true;
      } catch {
      }
    }
    const button = form.querySelector(
      'button:not([type]), button[type="submit"], input[type="submit"]'
    );
    if (button !== null) {
      button.click();
      return true;
    }
    dispatchEnter(element);
    return true;
  }
  async function settleAfterAction(quietMs, maxMs) {
    let release = null;
    const unloading = new Promise((resolve) => {
      release = resolve;
    });
    const onLeave = () => release?.();
    window.addEventListener("pagehide", onLeave, { once: true });
    window.addEventListener("beforeunload", onLeave, { once: true });
    try {
      await Promise.race([waitForDomIdle(quietMs, maxMs), unloading]);
    } finally {
      window.removeEventListener("pagehide", onLeave);
      window.removeEventListener("beforeunload", onLeave);
    }
  }
  async function click(args) {
    const target = resolveTarget(args);
    if ("error" in target) {
      return fail(target.error);
    }
    const { element } = target;
    if (isDisabled(element)) {
      return fail("\xF6\u011Fe pasif (disabled), \xF6nce etkinle\u015Ftiren ad\u0131m\u0131 yap ya da yeni snapshot al");
    }
    await scrollIntoView(element);
    try {
      element.focus({ preventScroll: true });
    } catch {
    }
    element.click();
    await settleAfterAction(300, 1500);
    return ok({ ok: true });
  }
  async function type(args) {
    const target = resolveTarget(args);
    if ("error" in target) {
      return fail(target.error);
    }
    const text = args.text;
    if (typeof text !== "string") {
      return fail("text eksik ya da metin de\u011Fil");
    }
    const { element } = target;
    if (isProtectedField(element)) {
      return fail("password_field_blocked: \u015Fifre/kart alan\u0131na yaz\u0131lmaz, kullan\u0131c\u0131dan iste");
    }
    if (isDisabled(element)) {
      return fail("alan pasif (disabled), yeni snapshot al");
    }
    await scrollIntoView(element);
    try {
      element.focus({ preventScroll: true });
    } catch {
    }
    if (element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement) {
      if (element.readOnly || element.disabled) {
        return fail("alan salt okunur ya da pasif, ba\u015Fka bir alan se\xE7 ya da yeni snapshot al");
      }
      if (element instanceof HTMLInputElement) {
        const inputType = element.type.toLowerCase();
        if (NON_TYPABLE_INPUT_TYPES.has(inputType)) {
          return fail(`bu alan metin kabul etmiyor (input type=${inputType}), click kullan`);
        }
      }
      const limit = element.maxLength;
      const wanted = limit > 0 && text.length > limit ? text.slice(0, limit) : text;
      setNativeValue(element, wanted);
      if (wanted !== "" && element.value === "") {
        return fail(
          `alan de\u011Feri kabul etmedi (type=${element.type.toLowerCase()}), bi\xE7imi kontrol et`
        );
      }
    } else if (element instanceof HTMLSelectElement) {
      if (element.disabled) {
        return fail("alan salt okunur ya da pasif, ba\u015Fka bir alan se\xE7 ya da yeni snapshot al");
      }
      if (!selectOption(element, text)) {
        const options = Array.from(element.options).slice(0, 10).map((option) => option.text.trim()).join(" | ");
        return fail(`se\xE7enek bulunamad\u0131. Mevcut se\xE7enekler: ${options}`);
      }
    } else if (element.isContentEditable) {
      if (!typeIntoContentEditable(element, text)) {
        return fail("metin alan\u0131na yaz\u0131lamad\u0131, \xF6\u011Fe d\xFCzenlenebilir de\u011Fil");
      }
    } else {
      return fail(`bu \xF6\u011Feye yaz\u0131lamaz (role=${roleOf(element)}), metin kutusu se\xE7`);
    }
    let submitted = false;
    if (args.submit === true) {
      const form = formOf(element);
      if (form === null) {
        dispatchEnter(element);
        submitted = true;
      } else {
        const invalid = firstInvalidField(form);
        if (invalid !== null) {
          const reason = invalid.validationMessage;
          await settleAfterAction(200, 800);
          return fail(
            `metin yaz\u0131ld\u0131 ama form g\xF6nderilmedi: "${describeField(invalid)}" alan\u0131 do\u011Frulamay\u0131 ge\xE7miyor${reason === "" ? "" : ` (${reason})`}. \xD6nce o alan\u0131 doldur, sonra tekrar dene`
          );
        }
        submitted = submitForm(form, element);
      }
      await settleAfterAction(300, 1500);
    } else {
      await settleAfterAction(200, 800);
    }
    return ok({ ok: true, submitted });
  }
  var MIN_SCROLL_RANGE = 8;
  function canScrollBox(element, down) {
    const range = element.scrollHeight - element.clientHeight;
    if (range <= MIN_SCROLL_RANGE) {
      return false;
    }
    return down ? element.scrollTop < range - 1 : element.scrollTop > 1;
  }
  function innerScroller(down) {
    if (typeof document.elementFromPoint !== "function") {
      return null;
    }
    const start = document.elementFromPoint(
      Math.round(window.innerWidth / 2),
      Math.round(window.innerHeight / 2)
    );
    for (let node = start; node !== null; node = node.parentElement) {
      if (!(node instanceof HTMLElement) || node === document.body) {
        continue;
      }
      const style = window.getComputedStyle(node);
      const overflow = `${style.overflowY} ${style.overflow}`;
      if (/auto|scroll|overlay/.test(overflow) && canScrollBox(node, down)) {
        return node;
      }
    }
    return null;
  }
  async function scroll(args) {
    const direction = args.direction;
    if (direction !== "up" && direction !== "down") {
      return fail('direction "up" ya da "down" olmal\u0131');
    }
    const down = direction === "down";
    const page = document.scrollingElement ?? document.documentElement;
    const sign = down ? 1 : -1;
    if (canScrollBox(page, down)) {
      window.scrollBy({ top: sign * Math.round(window.innerHeight * 0.8), behavior: "auto" });
      await delay(250);
      return ok({ ok: true, scroll_y: Math.round(window.scrollY) });
    }
    const container = innerScroller(down);
    if (container !== null) {
      container.scrollTop += sign * Math.round(container.clientHeight * 0.8);
      await delay(250);
      return ok({ ok: true, scroll_y: Math.round(container.scrollTop) });
    }
    window.scrollBy({ top: sign * Math.round(window.innerHeight * 0.8), behavior: "auto" });
    await delay(250);
    return ok({ ok: true, scroll_y: Math.round(window.scrollY) });
  }
  function controlValue(element) {
    if (element instanceof HTMLSelectElement) {
      return Array.from(element.selectedOptions).map((option) => option.text.trim()).join(", ");
    }
    if (element instanceof HTMLTextAreaElement) {
      return element.value;
    }
    if (element instanceof HTMLInputElement) {
      const inputType = element.type.toLowerCase();
      if (inputType === "checkbox" || inputType === "radio") {
        return element.checked ? "true" : "false";
      }
      return element.value;
    }
    return null;
  }
  async function read(args) {
    if (args.ref !== void 0 && args.ref !== null) {
      const target = resolveTarget(args);
      if ("error" in target) {
        return fail(target.error);
      }
      const { element } = target;
      const value = controlValue(element);
      if (value !== null) {
        if (isProtectedField(element)) {
          return ok({ text: value === "" ? "" : MASKED_VALUE });
        }
        return ok({ text: truncate(value, MAX_READ_LENGTH) });
      }
      const text2 = extractVisibleText(element, MAX_READ_LENGTH);
      return ok({ text: truncate(text2, MAX_READ_LENGTH) });
    }
    const text = extractVisibleText(mainContentRoot(), MAX_READ_LENGTH);
    return ok({ text: truncate(text, MAX_READ_LENGTH) });
  }
  async function highlight(args) {
    const target = resolveTarget(args);
    if ("error" in target) {
      return fail(target.error);
    }
    await highlightElement(target.element);
    return ok({ ok: true });
  }

  // src/content/snapshot.ts
  var CANDIDATE_SELECTOR = [
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
    '[tabindex]:not([tabindex="-1"])'
  ].join(", ");
  var MAX_CANDIDATES = 1200;
  var NAMELESS_ALLOWED_ROLES = /* @__PURE__ */ new Set(["textbox", "checkbox", "radio", "combobox", "slider"]);
  function isPasswordField(element) {
    if (!(element instanceof HTMLInputElement)) {
      return false;
    }
    const autocomplete = (element.getAttribute("autocomplete") ?? "").toLowerCase();
    return element.type.toLowerCase() === "password" || autocomplete.includes("password") || autocomplete.includes("cc-");
  }
  function valueOf(element) {
    if (element instanceof HTMLInputElement) {
      const type2 = element.type.toLowerCase();
      if (isPasswordField(element)) {
        return element.value === "" ? "" : "\u2022\u2022\u2022\u2022";
      }
      if (type2 === "checkbox" || type2 === "radio") {
        return element.checked ? "true" : "false";
      }
      return truncate(collapseWhitespace(element.value), MAX_NAME_LENGTH);
    }
    if (element instanceof HTMLTextAreaElement) {
      return truncate(collapseWhitespace(element.value), MAX_NAME_LENGTH);
    }
    if (element instanceof HTMLSelectElement) {
      const selected = element.selectedOptions[0];
      return selected === void 0 ? "" : truncate(collapseWhitespace(selected.text), MAX_NAME_LENGTH);
    }
    return null;
  }
  function priorityOf(rect, viewportWidth, viewportHeight) {
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
  function dropRedundantAncestors(candidates) {
    const byElement = /* @__PURE__ */ new Map();
    for (const candidate of candidates) {
      if (candidate.name !== "") {
        byElement.set(candidate.element, candidate);
      }
    }
    const redundant = /* @__PURE__ */ new Set();
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
  function clearPreviousRefs() {
    for (const element of document.querySelectorAll(`[${REF_ATTRIBUTE}]`)) {
      element.removeAttribute(REF_ATTRIBUTE);
    }
  }
  function takeSnapshot() {
    clearPreviousRefs();
    const viewportWidth = window.innerWidth;
    const viewportHeight = window.innerHeight;
    const measured = Array.from(document.querySelectorAll(CANDIDATE_SELECTOR)).map(
      (element) => {
        const rect = element.getBoundingClientRect();
        return { element, rect, priority: priorityOf(rect, viewportWidth, viewportHeight) };
      }
    );
    if (measured.length > MAX_CANDIDATES) {
      measured.sort((a, b) => a.priority - b.priority);
      measured.length = MAX_CANDIDATES;
    }
    const clipCache = /* @__PURE__ */ new Map();
    const candidates = [];
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
    const ordered = dropRedundantAncestors(candidates).sort((a, b) => a.priority - b.priority).slice(0, MAX_ELEMENTS);
    const elements = ordered.map((candidate, index) => {
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
          Math.round(candidate.rect.height)
        ]
      };
    });
    return {
      source: "browser",
      url: window.location.href,
      title: document.title,
      elements,
      text_excerpt: extractVisibleText(mainContentRoot(), MAX_TEXT_EXCERPT),
      shot: null
    };
  }

  // src/dev/probe.entry.ts
  var CANDIDATE_COUNT_SELECTOR = [
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
    '[tabindex]:not([tabindex="-1"])'
  ].join(", ");
  function measure(runs = 5) {
    const times = [];
    let snapshot = takeSnapshot();
    for (let index = 0; index < runs; index += 1) {
      const started = performance.now();
      snapshot = takeSnapshot();
      times.push(performance.now() - started);
    }
    times.sort((a, b) => a - b);
    const round = (value) => Math.round(value * 10) / 10;
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
      first6: snapshot.elements.slice(0, 6).map((element) => `[${element.ref}] ${element.role} "${element.name}"`)
    };
  }
  function print() {
    return takeSnapshot().elements.map((element) => `[${element.ref}] ${element.role} "${element.name}"`).join("\n");
  }
  var probe = {
    snapshot: takeSnapshot,
    measure,
    print,
    click,
    type,
    scroll,
    read,
    highlight
  };
  window.__aiProbe = probe;
})();
