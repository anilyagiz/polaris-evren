/**
 * Page actions: click / type / scroll / read / highlight.
 *
 * Every handler returns a `ToolResponse` — it never throws — and every error
 * message is written for the LLM to act on ("yeni snapshot al"), not for a
 * stack trace reader.
 */

import {
  MAX_READ_LENGTH,
  type ClickResult,
  type HighlightResult,
  type ReadResult,
  type ScrollResult,
  type ToolResponse,
  type TypeResult,
  fail,
  ok,
} from "./types";
import {
  accessibleName,
  delay,
  elementByRef,
  extractVisibleText,
  isDisabled,
  mainContentRoot,
  roleOf,
  scrollIntoView,
  truncate,
  waitForDomIdle,
} from "./dom";
import { highlightElement } from "./highlight";
import { announce, describeStep, labelFor, pointAt, press, typeProgressively } from "./hand";

const REF_NOT_FOUND = "ref bulunamadı, yeni snapshot al";

/** Same masking token `snapshot()` uses for protected values. */
const MASKED_VALUE = "••••";

function readRef(args: Record<string, unknown>): number | null {
  const ref = args.ref;
  if (typeof ref === "number" && Number.isInteger(ref)) {
    return ref;
  }
  if (typeof ref === "string" && /^\d+$/.test(ref)) {
    return Number.parseInt(ref, 10);
  }
  return null;
}

/** Resolve `args.ref` to a live element, or explain what went wrong. */
function resolveTarget(
  args: Record<string, unknown>,
): { element: HTMLElement } | { error: string } {
  const ref = readRef(args);
  if (ref === null) {
    return { error: "ref eksik ya da sayı değil" };
  }
  const element = elementByRef(ref);
  if (element === null) {
    return { error: REF_NOT_FOUND };
  }
  return { element };
}

/**
 * Secret / payment field names, matched against the field's attributes and its
 * accessible name. `type="password"` catches only the obvious case: a CVV box
 * is usually `type="text"` with `name="cvv"` and no `autocomplete`, and those
 * must never be typed into or read back either (ORTAK BAĞLAM §1).
 *
 * Deliberately biased towards over-blocking: a false positive costs one "ask
 * the user" turn, a false negative leaks a card number into the transcript.
 * `cc` alone is NOT a pattern — it is the "carbon copy" field of every compose
 * form; the `cc-*` autocomplete tokens are checked separately.
 */
const PROTECTED_PATTERNS: readonly RegExp[] = [
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
  /\biban\b/,
];

/** Password and payment fields are never typed into or read (ORTAK BAĞLAM §1). */
function isProtectedField(element: HTMLElement): boolean {
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
    accessibleName(element, 120),
  ]
    .join(" ")
    .toLowerCase();

  return PROTECTED_PATTERNS.some((pattern) => pattern.test(haystack));
}

/**
 * Input types that hold no typable text. `value` on `type="file"` even throws
 * an `InvalidStateError` when set to anything but the empty string, so these
 * have to be rejected before the setter runs.
 */
const NON_TYPABLE_INPUT_TYPES = new Set([
  "checkbox",
  "radio",
  "button",
  "submit",
  "reset",
  "image",
  "file",
]);

/**
 * Write through the prototype's native setter so React/Vue value trackers see
 * a real change; a plain `element.value = x` would be reverted on re-render.
 *
 * Frameworks install their tracker as an *own* property on the node, so the
 * prototype descriptor — not `element.value = x` — is the one that bypasses it.
 */
function nativeValueSetter(
  element: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement,
): ((value: string) => void) | null {
  let prototype: object;
  if (element instanceof HTMLTextAreaElement) {
    prototype = HTMLTextAreaElement.prototype;
  } else if (element instanceof HTMLSelectElement) {
    prototype = HTMLSelectElement.prototype;
  } else {
    prototype = HTMLInputElement.prototype;
  }
  const setter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
  return setter === undefined ? null : (value: string) => setter.call(element, value);
}

function dispatchInputAndChange(element: HTMLElement, value: string): void {
  const input =
    typeof InputEvent === "function"
      ? new InputEvent("input", { bubbles: true, data: value, inputType: "insertText" })
      : new Event("input", { bubbles: true });
  element.dispatchEvent(input);
  element.dispatchEvent(new Event("change", { bubbles: true }));
}

/** Full replacement: the whole previous value is overwritten, as `type` promises. */
function setNativeValue(element: HTMLInputElement | HTMLTextAreaElement, value: string): void {
  const setter = nativeValueSetter(element);
  if (setter !== null) {
    setter(value);
  } else {
    element.value = value;
  }
  dispatchInputAndChange(element, value);
}

/** One intermediate keystroke of the visible typing: `input` only, no `change`. */
function setPartialValue(element: HTMLInputElement | HTMLTextAreaElement, value: string): void {
  const setter = nativeValueSetter(element);
  if (setter !== null) {
    setter(value);
  } else {
    element.value = value;
  }
  const input =
    typeof InputEvent === "function"
      ? new InputEvent("input", { bubbles: true, data: value.slice(-1), inputType: "insertText" })
      : new Event("input", { bubbles: true });
  element.dispatchEvent(input);
}

function dispatchEnter(element: HTMLElement): void {
  for (const type of ["keydown", "keypress", "keyup"] as const) {
    const event = new KeyboardEvent(type, {
      key: "Enter",
      code: "Enter",
      bubbles: true,
      cancelable: true,
    });
    Object.defineProperty(event, "keyCode", { get: () => 13 });
    Object.defineProperty(event, "which", { get: () => 13 });
    element.dispatchEvent(event);
  }
}

/**
 * `execCommand` is deprecated: it is missing in some embedders and returns
 * `false` whenever the host refuses the edit. Both cases used to be reported as
 * a successful type, so fall back to a plain text replacement.
 */
function typeIntoContentEditable(element: HTMLElement, text: string): boolean {
  element.focus();
  const range = document.createRange();
  range.selectNodeContents(element);
  const selection = window.getSelection();
  selection?.removeAllRanges();
  selection?.addRange(range);

  const exec = (document as Document & { execCommand?: (c: string, u: boolean, v: string) => boolean })
    .execCommand;
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

function selectOption(element: HTMLSelectElement, text: string): boolean {
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

/**
 * The form this control submits, honouring `form="other-form-id"`: such a
 * control is NOT a DOM descendant of its form, so `closest("form")` misses it.
 */
function formOf(element: HTMLElement): HTMLFormElement | null {
  const owned = (element as { form?: unknown }).form;
  if (owned instanceof HTMLFormElement) {
    return owned;
  }
  return element.closest("form");
}

/**
 * First control that HTML5 validation would reject. `requestSubmit()` runs
 * interactive validation and then silently does nothing — no `submit` event, no
 * exception — so without this check we report a submit that never happened.
 *
 * Reads `validity.valid` instead of `checkValidity()` on purpose: the latter
 * fires `invalid` events at the page.
 */
function firstInvalidField(form: HTMLFormElement): HTMLElement | null {
  if (form.noValidate) {
    return null;
  }
  for (const member of Array.from(form.elements)) {
    const control = member as HTMLInputElement;
    if (typeof control.willValidate !== "boolean" || !control.willValidate) {
      continue;
    }
    if (control.validity !== undefined && !control.validity.valid) {
      return control;
    }
  }
  return null;
}

function describeField(element: HTMLElement): string {
  const name = accessibleName(element, 40);
  if (name !== "") {
    return name;
  }
  return element.getAttribute("name") ?? element.getAttribute("id") ?? roleOf(element);
}

/** Submit `form`; false means nothing was dispatched. */
function submitForm(form: HTMLFormElement, element: HTMLElement): boolean {
  if (typeof form.requestSubmit === "function") {
    try {
      form.requestSubmit();
      return true;
    } catch {
      // Fall through to the button/Enter path below.
    }
  }

  const button = form.querySelector<HTMLElement>(
    'button:not([type]), button[type="submit"], input[type="submit"]',
  );
  if (button !== null) {
    button.click();
    return true;
  }

  dispatchEnter(element);
  return true;
}

/**
 * A click that starts a navigation tears the content script down. The reply has
 * to leave before that happens, otherwise the bridge sees "message port closed"
 * and reports a failure for a click that actually worked — so stop waiting for
 * the DOM as soon as the page announces it is going away.
 */
async function settleAfterAction(quietMs: number, maxMs: number): Promise<void> {
  let release: (() => void) | null = null;
  const unloading = new Promise<void>((resolve) => {
    release = resolve;
  });
  const onLeave = (): void => release?.();

  window.addEventListener("pagehide", onLeave, { once: true });
  window.addEventListener("beforeunload", onLeave, { once: true });
  try {
    await Promise.race([waitForDomIdle(quietMs, maxMs), unloading]);
  } finally {
    window.removeEventListener("pagehide", onLeave);
    window.removeEventListener("beforeunload", onLeave);
  }
}

export async function click(args: Record<string, unknown>): Promise<ToolResponse<ClickResult>> {
  const target = resolveTarget(args);
  if ("error" in target) {
    return fail(target.error);
  }

  const { element } = target;
  // A `.click()` on a disabled control dispatches nothing at all, and pages
  // ignore clicks on `aria-disabled` ones; both used to report success.
  if (isDisabled(element)) {
    return fail("öğe pasif (disabled), önce etkinleştiren adımı yap ya da yeni snapshot al");
  }

  await scrollIntoView(element);
  // The visible hand: glide onto the target, press, then the real click.
  await pointAt(element, labelFor(args, describeStep("click", element)));
  await press();
  try {
    element.focus({ preventScroll: true });
  } catch {
    // Not focusable; clicking still works.
  }
  element.click();
  await settleAfterAction(300, 1500);
  return ok({ ok: true });
}

export async function type(args: Record<string, unknown>): Promise<ToolResponse<TypeResult>> {
  const target = resolveTarget(args);
  if ("error" in target) {
    return fail(target.error);
  }
  const text = args.text;
  if (typeof text !== "string") {
    return fail("text eksik ya da metin değil");
  }

  const { element } = target;
  if (isProtectedField(element)) {
    return fail("password_field_blocked: şifre/kart alanına yazılmaz, kullanıcıdan iste");
  }
  if (isDisabled(element)) {
    return fail("alan pasif (disabled), yeni snapshot al");
  }

  await scrollIntoView(element);
  await pointAt(element, labelFor(args, describeStep("type", element)));
  try {
    element.focus({ preventScroll: true });
  } catch {
    // Keep going: some frameworks accept input without focus.
  }

  if (element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement) {
    if (element.readOnly || element.disabled) {
      return fail("alan salt okunur ya da pasif, başka bir alan seç ya da yeni snapshot al");
    }
    if (element instanceof HTMLInputElement) {
      const inputType = element.type.toLowerCase();
      if (NON_TYPABLE_INPUT_TYPES.has(inputType)) {
        return fail(`bu alan metin kabul etmiyor (input type=${inputType}), click kullan`);
      }
    }

    // A real keyboard cannot exceed maxlength; writing past it would leave a
    // value the page can never accept (and silently fails HTML5 validation).
    const limit = element.maxLength;
    const wanted = limit > 0 && text.length > limit ? text.slice(0, limit) : text;

    // Typed like a person (visible, progressive), then the full value is set
    // once more the React-safe way so the final state never depends on how the
    // page reacted to the intermediate keystrokes.
    await typeProgressively(wanted, (partial) => setPartialValue(element, partial));
    setNativeValue(element, wanted);

    // Empty after a non-empty write = the value was rejected outright
    // (`type="number"` fed letters, `type="date"` fed a free-form date, …).
    if (wanted !== "" && element.value === "") {
      return fail(
        `alan değeri kabul etmedi (type=${element.type.toLowerCase()}), biçimi kontrol et`,
      );
    }
  } else if (element instanceof HTMLSelectElement) {
    if (element.disabled) {
      return fail("alan salt okunur ya da pasif, başka bir alan seç ya da yeni snapshot al");
    }
    if (!selectOption(element, text)) {
      const options = Array.from(element.options)
        .slice(0, 10)
        .map((option) => option.text.trim())
        .join(" | ");
      return fail(`seçenek bulunamadı. Mevcut seçenekler: ${options}`);
    }
  } else if (element.isContentEditable) {
    if (!typeIntoContentEditable(element, text)) {
      return fail("metin alanına yazılamadı, öğe düzenlenebilir değil");
    }
  } else {
    return fail(`bu öğeye yazılamaz (role=${roleOf(element)}), metin kutusu seç`);
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
        // Honest report: the text is in, the submit was blocked by the browser.
        const reason = (invalid as HTMLInputElement).validationMessage;
        await settleAfterAction(200, 800);
        return fail(
          `metin yazıldı ama form gönderilmedi: "${describeField(invalid)}" alanı doğrulamayı ` +
            `geçmiyor${reason === "" ? "" : ` (${reason})`}. Önce o alanı doldur, sonra tekrar dene`,
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

/** Below this a box is not really scrollable, just rounding noise. */
const MIN_SCROLL_RANGE = 8;

function canScrollBox(element: Element, down: boolean): boolean {
  const range = element.scrollHeight - element.clientHeight;
  if (range <= MIN_SCROLL_RANGE) {
    return false;
  }
  return down ? element.scrollTop < range - 1 : element.scrollTop > 1;
}

/**
 * The scroller the user's wheel would move: pages that put `overflow: hidden`
 * on the body and scroll an inner column (chat threads, infinite feeds, virtual
 * lists) do not move at all on `window.scrollBy`, and the LLM then sees an
 * identical snapshot and keeps scrolling forever.
 */
function innerScroller(down: boolean): HTMLElement | null {
  if (typeof document.elementFromPoint !== "function") {
    return null;
  }
  const start = document.elementFromPoint(
    Math.round(window.innerWidth / 2),
    Math.round(window.innerHeight / 2),
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

export async function scroll(args: Record<string, unknown>): Promise<ToolResponse<ScrollResult>> {
  const direction = args.direction;
  if (direction !== "up" && direction !== "down") {
    return fail('direction "up" ya da "down" olmalı');
  }

  const down = direction === "down";
  await announce(
    labelFor(args, down ? "Sayfayı aşağı kaydırıyorum" : "Sayfayı yukarı kaydırıyorum"),
    down ? "down" : "up",
  );
  const page = document.scrollingElement ?? document.documentElement;
  const sign = down ? 1 : -1;

  // The page wins when it can move: that is what the user sees scrolling.
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

  // Nothing to move: still a success, and the unchanged offset is the signal
  // the Executor uses for "there is no more content this way".
  window.scrollBy({ top: sign * Math.round(window.innerHeight * 0.8), behavior: "auto" });
  await delay(250);
  return ok({ ok: true, scroll_y: Math.round(window.scrollY) });
}

/** Current value of a form control, which carries no descendant text. */
function controlValue(element: HTMLElement): string | null {
  if (element instanceof HTMLSelectElement) {
    return Array.from(element.selectedOptions)
      .map((option) => option.text.trim())
      .join(", ");
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

export async function read(args: Record<string, unknown>): Promise<ToolResponse<ReadResult>> {
  if (args.ref !== undefined && args.ref !== null) {
    const target = resolveTarget(args);
    if ("error" in target) {
      return fail(target.error);
    }
    const { element } = target;
    if (element.isConnected) {
      await pointAt(element, labelFor(args, describeStep("read", element)));
    }

    // An `<input>` has no text nodes (so `extractVisibleText` is empty) and a
    // `<select>` would otherwise return every option instead of the chosen one.
    const value = controlValue(element);
    if (value !== null) {
      // `snapshot()` masks these values; `read` must not be the way around it.
      // Only real controls are masked: a paragraph that merely mentions "şifre"
      // is page text the LLM needs ("Şifre hatalı"), not a secret.
      if (isProtectedField(element)) {
        return ok({ text: value === "" ? "" : MASKED_VALUE });
      }
      return ok({ text: truncate(value, MAX_READ_LENGTH) });
    }

    const text = extractVisibleText(element, MAX_READ_LENGTH);
    return ok({ text: truncate(text, MAX_READ_LENGTH) });
  }

  void announce(labelFor(args, "Sayfayı okuyorum"));
  const text = extractVisibleText(mainContentRoot(), MAX_READ_LENGTH);
  return ok({ text: truncate(text, MAX_READ_LENGTH) });
}

export async function highlight(
  args: Record<string, unknown>,
): Promise<ToolResponse<HighlightResult>> {
  const target = resolveTarget(args);
  if ("error" in target) {
    return fail(target.error);
  }
  await scrollIntoView(target.element);
  await pointAt(target.element, labelFor(args, describeStep("highlight", target.element)));
  await highlightElement(target.element);
  return ok({ ok: true });
}
