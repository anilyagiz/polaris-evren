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
  delay,
  elementByRef,
  extractVisibleText,
  mainContentRoot,
  roleOf,
  scrollIntoView,
  truncate,
  waitForDomIdle,
} from "./dom";
import { highlightElement } from "./highlight";

const REF_NOT_FOUND = "ref bulunamadı, yeni snapshot al";

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

/** Password and payment fields are never typed into (ORTAK BAĞLAM §1). */
function isProtectedField(element: HTMLElement): boolean {
  const autocomplete = (element.getAttribute("autocomplete") ?? "").toLowerCase();
  if (autocomplete.includes("password") || autocomplete.includes("cc-")) {
    return true;
  }
  return element instanceof HTMLInputElement && element.type.toLowerCase() === "password";
}

/**
 * Write through the prototype's native setter so React/Vue value trackers see
 * a real change; a plain `element.value = x` would be reverted on re-render.
 */
function setNativeValue(element: HTMLInputElement | HTMLTextAreaElement, value: string): void {
  const prototype =
    element instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
  if (setter !== undefined) {
    setter.call(element, value);
  } else {
    element.value = value;
  }

  const input =
    typeof InputEvent === "function"
      ? new InputEvent("input", { bubbles: true, data: value, inputType: "insertText" })
      : new Event("input", { bubbles: true });
  element.dispatchEvent(input);
  element.dispatchEvent(new Event("change", { bubbles: true }));
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

function typeIntoContentEditable(element: HTMLElement, text: string): void {
  element.focus();
  const range = document.createRange();
  range.selectNodeContents(element);
  const selection = window.getSelection();
  selection?.removeAllRanges();
  selection?.addRange(range);
  document.execCommand("insertText", false, text);
}

function selectOption(element: HTMLSelectElement, text: string): boolean {
  const wanted = text.trim().toLowerCase();
  for (const option of Array.from(element.options)) {
    if (option.value.toLowerCase() === wanted || option.text.trim().toLowerCase() === wanted) {
      element.value = option.value;
      element.dispatchEvent(new Event("input", { bubbles: true }));
      element.dispatchEvent(new Event("change", { bubbles: true }));
      return true;
    }
  }
  return false;
}

export async function click(args: Record<string, unknown>): Promise<ToolResponse<ClickResult>> {
  const target = resolveTarget(args);
  if ("error" in target) {
    return fail(target.error);
  }

  const { element } = target;
  await scrollIntoView(element);
  try {
    element.focus({ preventScroll: true });
  } catch {
    // Not focusable; clicking still works.
  }
  element.click();
  await waitForDomIdle(300, 1500);
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

  await scrollIntoView(element);
  try {
    element.focus({ preventScroll: true });
  } catch {
    // Keep going: some frameworks accept input without focus.
  }

  if (element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement) {
    if (element.readOnly || element.disabled) {
      return fail("alan salt okunur ya da pasif");
    }
    setNativeValue(element, text);
  } else if (element instanceof HTMLSelectElement) {
    if (!selectOption(element, text)) {
      const options = Array.from(element.options)
        .slice(0, 10)
        .map((option) => option.text.trim())
        .join(" | ");
      return fail(`seçenek bulunamadı. Mevcut seçenekler: ${options}`);
    }
  } else if (element.isContentEditable) {
    typeIntoContentEditable(element, text);
  } else {
    return fail(`bu öğeye yazılamaz (role=${roleOf(element)}), metin kutusu seç`);
  }

  let submitted = false;
  if (args.submit === true) {
    const form = element.closest("form");
    if (form !== null) {
      form.requestSubmit();
    } else {
      dispatchEnter(element);
    }
    submitted = true;
    await waitForDomIdle(300, 1500);
  } else {
    await waitForDomIdle(200, 800);
  }

  return ok({ ok: true, submitted });
}

export async function scroll(args: Record<string, unknown>): Promise<ToolResponse<ScrollResult>> {
  const direction = args.direction;
  if (direction !== "up" && direction !== "down") {
    return fail('direction "up" ya da "down" olmalı');
  }

  const amount = Math.round(window.innerHeight * 0.8);
  window.scrollBy({ top: direction === "down" ? amount : -amount, behavior: "auto" });
  // Give lazy-loaded content a chance to appear before the next snapshot.
  await delay(250);
  return ok({ ok: true, scroll_y: Math.round(window.scrollY) });
}

export async function read(args: Record<string, unknown>): Promise<ToolResponse<ReadResult>> {
  if (args.ref !== undefined && args.ref !== null) {
    const target = resolveTarget(args);
    if ("error" in target) {
      return fail(target.error);
    }
    let text = extractVisibleText(target.element, MAX_READ_LENGTH);
    if (text === "" && "value" in target.element) {
      text = String((target.element as HTMLInputElement).value ?? "");
    }
    return ok({ text: truncate(text, MAX_READ_LENGTH) });
  }

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
  await highlightElement(target.element);
  return ok({ ok: true });
}
