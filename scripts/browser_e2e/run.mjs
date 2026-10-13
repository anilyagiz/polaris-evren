#!/usr/bin/env node
/**
 * Polaris real-browser E2E.
 *
 *   node scripts/browser_e2e/run.mjs            (headless Playwright Chromium)
 *   HEADED=1 node scripts/browser_e2e/run.mjs   (watch it)
 *   SKIP_BUILD=1 ...                            (reuse scripts/browser_e2e/out/ext)
 *
 * What it does:
 *  1. builds the extension into out/ext (own outDir, never touches extension/dist)
 *     and widens the copy's CSP connect-src to the test backend port;
 *  2. starts the scripted fake LLM (fake_llm_browser.py, reuses scripts/e2e)
 *     and the REAL backend (uvicorn backend.main:app) against it, temp DB;
 *  3. serves extension/test/fixture.html over http;
 *  4. launches Chromium with the extension, opens the panel page as a tab
 *     (Brave tab fallback), the fixture in its own window, and drives the UI;
 *  5. asserts real DOM effects on the fixture + panel rendering, screenshots
 *     into out/, writes out/results.json, exit code 0 only if all PASS.
 */

import { spawn, spawnSync } from "node:child_process";
import { createServer } from "node:http";
import { createServer as createNetServer } from "node:net";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync, createWriteStream, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, "..", "..");
const OUT = join(HERE, "out");
const EXT_OUT = join(OUT, "ext");
const EXTENSION_DIR = join(ROOT, "extension");
const FIXTURE = join(EXTENSION_DIR, "test", "fixture.html");
const PYTHON = process.platform === "win32" ? join(ROOT, ".venv", "Scripts", "python.exe") : join(ROOT, ".venv", "bin", "python");
const HEADED = process.env.HEADED === "1";
const LIVE = process.argv.includes("--live");
/** Voice E2E: fake mic fed with a backend-generated Turkish WAV, real backend + real LLM. */
const VOICE = process.argv.includes("--voice");
const REAL_LLM = LIVE || VOICE;
const LIVE_TIMEOUT = 5 * 60_000;
/** Experiment: force tool_choice="required" in the backend (see live_backend_experiment.py). */
const TOOL_CHOICE_REQUIRED = process.argv.includes("--tool-choice-required");
const STEP_TIMEOUT = 60_000;

mkdirSync(OUT, { recursive: true });
const logFile = createWriteStream(join(OUT, "run.log"), { flags: "w" });
function log(...parts) {
  const line = `[${new Date().toISOString().slice(11, 23)}] ${parts.join(" ")}`;
  console.log(line);
  logFile.write(line + "\n");
}

const results = [];
function record(name, ok, detail = "") {
  results.push({ name, ok: Boolean(ok), detail });
  log(ok ? "PASS" : "FAIL", name, detail ? `- ${detail}` : "");
  return Boolean(ok);
}

const children = [];
let context = null;
const extraContexts = [];
let staticServer = null;
let profileDir = null;

async function cleanup() {
  try { await context?.close(); } catch { /* already closed */ }
  for (const ctx of extraContexts) {
    try { await ctx.close(); } catch { /* already closed */ }
  }
  staticServer?.close();
  for (const child of children) {
    if (child.exitCode === null) {
      if (process.platform === "win32") spawnSync("taskkill", ["/pid", String(child.pid), "/t", "/f"], { stdio: "ignore" });
      else child.kill("SIGTERM");
    }
  }
  if (profileDir) {
    try { rmSync(profileDir, { recursive: true, force: true }); } catch { /* locked on Windows, harmless */ }
  }
}

function freePort() {
  return new Promise((res, rej) => {
    const srv = createNetServer();
    srv.unref();
    srv.on("error", rej);
    srv.listen(0, "127.0.0.1", () => {
      const { port } = srv.address();
      srv.close(() => res(port));
    });
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitFor(fn, timeout, label) {
  const deadline = Date.now() + timeout;
  let last;
  while (Date.now() < deadline) {
    try {
      const value = await fn();
      if (value) return value;
    } catch (error) {
      last = error;
    }
    await sleep(150);
  }
  throw new Error(`timeout waiting for ${label}${last ? `: ${last.message}` : ""}`);
}

// ---------------------------------------------------------------------------
// 1. build
// ---------------------------------------------------------------------------
function buildExtension() {
  if (process.env.SKIP_BUILD === "1" && existsSync(join(EXT_OUT, "manifest.json"))) {
    log("build: skipped (SKIP_BUILD=1)");
    return;
  }
  log("build: npx vite build ->", EXT_OUT);
  const r = spawnSync("npx", ["vite", "build", "--outDir", EXT_OUT, "--emptyOutDir"], {
    cwd: EXTENSION_DIR,
    shell: process.platform === "win32",
    encoding: "utf8",
  });
  writeFileSync(join(OUT, "build.log"), `${r.stdout}\n${r.stderr}`);
  if (r.status !== 0) throw new Error(`vite build failed (see out/build.log):\n${r.stderr.slice(-2000)}`);
}

/**
 * The shipped CSP only allows ws/http to port 8000. Port 8000 is not free on
 * this machine, so the TEST copy gets the test port added. The finding itself
 * is recorded as a result (a user who changes the backend port in Settings is
 * blocked by CSP).
 */
function patchCsp(port) {
  const path = join(EXT_OUT, "manifest.json");
  const manifest = JSON.parse(readFileSync(path, "utf8"));
  const csp = manifest.content_security_policy?.extension_pages ?? "";
  const connect = /connect-src([^;]*)/.exec(csp)?.[1] ?? "";
  const allowsPort = connect.includes(`:${port}`) || /(localhost|127\.0\.0\.1):\*/.test(connect) || /(^|\s)(ws|http):(\s|$)/.test(connect);
  record("manifest CSP allows a non-8000 backend port", allowsPort,
    allowsPort ? "" : `connect-src only lists :8000 -> Settings backend URL on any other port is blocked (patched in test copy)`);
  if (!allowsPort) {
    const extra = [`http://localhost:${port}`, `http://127.0.0.1:${port}`, `ws://localhost:${port}`, `ws://127.0.0.1:${port}`].join(" ");
    manifest.content_security_policy.extension_pages = csp.replace(/connect-src ([^;]*)/, (_m, list) => `connect-src ${list} ${extra}`);
    writeFileSync(path, JSON.stringify(manifest, null, 2));
  }
}

// ---------------------------------------------------------------------------
// 2. processes
// ---------------------------------------------------------------------------
function startFakeLlm() {
  return new Promise((res, rej) => {
    const child = spawn(PYTHON, [join(HERE, "fake_llm_browser.py")], {
      cwd: ROOT,
      env: { ...process.env, PYTHONUTF8: "1", PYTHONIOENCODING: "utf-8" },
    });
    children.push(child);
    const errLog = createWriteStream(join(OUT, "fake_llm.log"));
    child.stderr.pipe(errLog);
    let buf = "";
    const timer = setTimeout(() => rej(new Error("fake LLM did not start in 30s (see out/fake_llm.log)")), 30_000);
    child.stdout.on("data", (chunk) => {
      buf += chunk.toString();
      const line = buf.split("\n").find((l) => l.trim().startsWith("{"));
      if (line) {
        clearTimeout(timer);
        res(JSON.parse(line).base_url);
      }
    });
    child.on("exit", (code) => rej(new Error(`fake LLM exited with ${code} (see out/fake_llm.log)`)));
  });
}

async function startBackend(port, llmUrl, workdir) {
  // LIVE (llmUrl === null): the backend loads backend/.env itself (real LLM key,
  // base URL, model); we only isolate the DB. load_dotenv never overrides
  // variables already in the environment, so strip any inherited LLM vars.
  if (llmUrl === null) {
    const env = { ...process.env, PYTHONUTF8: "1", PYTHONIOENCODING: "utf-8", ASSISTANT_DB: join(workdir, "assistant.db"), DB_PATH: join(workdir, "assistant.db"), CUA_ENABLED: "false" };
    for (const k of ["DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "LLM_MODEL"]) delete env[k];
    return spawnBackend(port, env);
  }
  const env = {
    ...process.env,
    PYTHONUTF8: "1",
    PYTHONIOENCODING: "utf-8",
    DEEPSEEK_API_KEY: "fake-key",
    DEEPSEEK_BASE_URL: llmUrl,
    LLM_MODEL: "fake-llm",
    LLM_TIMEOUT_S: "20",
    BRAVE_API_KEY: "fake-brave",
    ASSISTANT_DB: join(workdir, "assistant.db"),
    DB_PATH: join(workdir, "assistant.db"),
    TIMEZONE: "Europe/Istanbul",
    CUA_ENABLED: "false",
    TOOL_TIMEOUT_S: "20",
    CONFIRM_TIMEOUT_S: "60",
    REMINDER_POLL_S: "30",
  };
  return spawnBackend(port, env);
}

async function spawnBackend(port, env) {
  const argv = TOOL_CHOICE_REQUIRED
    ? [join(HERE, "live_backend_experiment.py"), String(port)]
    : ["-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", String(port)];
  const child = spawn(PYTHON, argv, { cwd: ROOT, env });
  children.push(child);
  const out = createWriteStream(join(OUT, "backend.log"));
  child.stdout.pipe(out);
  child.stderr.pipe(out);
  await waitFor(async () => {
    if (child.exitCode !== null) throw new Error(`backend exited ${child.exitCode} (see out/backend.log)`);
    const r = await fetch(`http://127.0.0.1:${port}/health`);
    return r.ok;
  }, 90_000, "backend /health");
}

function startStatic(port) {
  const html = readFileSync(FIXTURE);
  staticServer = createServer((req, res) => {
    if (req.url === "/" || req.url.startsWith("/fixture.html")) {
      res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
      res.end(html);
    } else {
      res.writeHead(404);
      res.end();
    }
  });
  return new Promise((r) => staticServer.listen(port, "127.0.0.1", r));
}

// ---------------------------------------------------------------------------
// 4. browser helpers
// ---------------------------------------------------------------------------
async function armFixture(page) {
  await page.waitForLoadState("load");
  await page.evaluate(() => {
    window.__hl = 0;
    window.__clicks = [];
    new MutationObserver((records) => {
      for (const r of records) for (const n of r.addedNodes) if (n.id === "ai-assistant-highlight-host") window.__hl += 1;
    }).observe(document.documentElement, { childList: true });
    document.addEventListener("click", (e) => {
      const t = e.target;
      window.__clicks.push(`${t.tagName}#${t.id || ""}:${(t.textContent || "").trim().slice(0, 20)}`);
    }, true);
  });
}

async function sendChat(panel, text) {
  const box = panel.getByLabel("Mesajın");
  await box.fill(text);
  await box.press("Enter");
}

/** Wait until the n-th exchange is no longer running; returns its state. */
async function waitExchange(panel, index, timeout = STEP_TIMEOUT) {
  const ex = panel.locator("article.exchange").nth(index);
  await ex.waitFor({ timeout });
  await waitFor(async () => (await ex.locator(".spin").count()) === 0, timeout, `exchange #${index} to finish`);
  const answer = (await ex.locator(".markdown").count()) ? await ex.locator(".markdown").first().innerText() : "";
  const err = (await ex.locator("p.error").count()) ? await ex.locator("p.error").first().innerText() : "";
  // The "Neden?" timeline folds once the answer arrives; open it to read the steps.
  const toggle = ex.locator(".timeline-toggle");
  if ((await toggle.count()) && (await toggle.first().getAttribute("aria-expanded")) === "false") {
    await toggle.first().click();
  }
  // Labels are localized ("Yönlendirici"…); the agent id is in the class name.
  const badges = await ex.locator(".agent-badge").evaluateAll((els) =>
    els.map((el) => [...el.classList].find((c) => c.startsWith("agent-") && c !== "agent-badge")?.slice(6) ?? el.textContent));
  return { ex, answer, err, badges };
}

async function shot(page, name) {
  try {
    await page.screenshot({ path: join(OUT, `${name}.png`), fullPage: true });
  } catch (error) {
    log("screenshot failed", name, error.message);
  }
}

// ---------------------------------------------------------------------------
// LIVE scenarios (real LLM, real public pages). Read-only tasks: any
// need_confirm is DENIED automatically, so nothing is ever submitted/bought.
// ---------------------------------------------------------------------------
const LIVE_SCENARIOS = [
  {
    name: "wiki-summary",
    url: "https://tr.wikipedia.org/wiki/Polaris",
    prompt: "Bu sayfayı özetle",
    check: ({ answer, tools }) => {
      const problems = [];
      if (!/polaris|kutup y[ıi]ld[ıi]z/i.test(answer)) problems.push("answer does not mention Polaris/Kutup Yıldızı");
      if (!tools.some((t) => t === "browser.read" || t === "browser.snapshot")) problems.push("page was never read");
      return problems;
    },
  },
  {
    name: "books-cheapest",
    url: "https://books.toscrape.com/",
    prompt: "Bu sitede en ucuz 3 kitabı bul ve fiyatlarını söyle",
    check: ({ answer, tools, pageFacts }) => {
      const problems = [];
      const prices = answer.match(/£\s?\d+\.\d{2}/g) ?? [];
      if (prices.length < 3) problems.push(`answer has ${prices.length} £ prices (<3)`);
      if (!tools.some((t) => t.startsWith("browser."))) problems.push("no browser tool used");
      const lowest = pageFacts.lowest ?? [];
      const hit = lowest.filter((p) => answer.replace(/\s/g, "").includes(p.replace(/\s/g, ""))).length;
      if (lowest.length && hit === 0) problems.push(`none of the first page's 3 lowest (${lowest.join(", ")}) in answer (model may have looked at other pages)`);
      return problems;
    },
    facts: async (page) => ({
      lowest: await page.evaluate(() =>
        [...document.querySelectorAll(".product_price .price_color")]
          .map((e) => e.textContent.trim())
          .sort((a, b) => parseFloat(a.replace(/[^\d.]/g, "")) - parseFloat(b.replace(/[^\d.]/g, "")))
          .slice(0, 3)),
    }),
  },
  {
    name: "wikipedia-search-ataturk",
    url: "https://www.wikipedia.org/",
    prompt: "Vikipedi'de Mustafa Kemal Atatürk'ü ara ve doğum tarihini söyle",
    check: ({ answer, tools, finalUrl }) => {
      const problems = [];
      if (!/1881/.test(answer)) problems.push("answer lacks birth year 1881");
      if (!tools.includes("browser.type")) problems.push("search box was never typed into");
      if (!/Atat%C3%BCrk|Atatürk|Mustafa_Kemal/i.test(decodeURIComponent(finalUrl))) problems.push(`page did not end on an Atatürk article (${finalUrl})`);
      return problems;
    },
  },
  {
    name: "web-search-keyless",
    url: "https://example.com/",
    prompt: "Web'de ara: Brave tarayıcısının kurucusu kim?",
    check: ({ answer, tools }) => {
      const problems = [];
      if (!/Eich/i.test(answer)) problems.push("answer lacks 'Brendan Eich'");
      if (!tools.includes("web.search") && !tools.includes("browser.navigate"))
        problems.push("neither web.search nor browser.navigate was used");
      return problems;
    },
  },
];

async function runLive(panel, page) {
  const report = [];
  const only = process.env.LIVE_ONLY; // e.g. LIVE_ONLY=books-cheapest
  for (const [i, sc] of LIVE_SCENARIOS.filter((s) => !only || s.name === only).entries()) {
    log(`live ${i + 1}: ${sc.name} -> ${sc.url}`);
    const entry = { name: sc.name, url: sc.url, prompt: sc.prompt };
    try {
      await page.goto(sc.url, { waitUntil: "domcontentloaded", timeout: 60_000 });
      await page.waitForLoadState("load", { timeout: 30_000 }).catch(() => undefined);
      await page.bringToFront();
      const pageFacts = sc.facts ? await sc.facts(page).catch(() => ({})) : {};
      const index = await panel.locator("article.exchange").count();
      const started = Date.now();
      await sendChat(panel, sc.prompt);

      // Safety: deny every confirmation on real sites, keep a record of it.
      const denials = [];
      let watching = true;
      const watcher = (async () => {
        while (watching) {
          const modal = panel.locator("dialog.modal[open]");
          if ((await modal.count()) > 0 && /Onayın gerekiyor/.test(await modal.innerText().catch(() => ""))) {
            denials.push((await modal.innerText()).replace(/\s+/g, " ").slice(0, 160));
            await shot(panel, `live-${sc.name}-confirm`);
            await modal.getByRole("button", { name: "Reddet" }).click().catch(() => undefined);
          }
          await sleep(300);
        }
      })();
      let res;
      try {
        res = await waitExchange(panel, index, LIVE_TIMEOUT);
      } finally {
        watching = false;
        await watcher;
      }
      const seconds = Math.round((Date.now() - started) / 100) / 10;
      const tools = (await res.ex.locator(".trace-step code.tool-code").allInnerTexts()).map((c) => c.split("(")[0].trim());
      const steps = await res.ex.locator(".trace-step").count();
      const finalUrl = page.url();
      await shot(panel, `live-${sc.name}-panel`);
      await page.screenshot({ path: join(OUT, `live-${sc.name}-page.png`) }).catch(() => undefined);
      Object.assign(entry, { seconds, steps, tools, agents: [...new Set(res.badges)], answer: res.answer, error: res.err, denials, finalUrl, pageFacts });
      const problems = res.err ? [`panel error: ${res.err}`] : res.answer ? sc.check({ answer: res.answer, tools, finalUrl, pageFacts }) : ["no final answer"];
      entry.ok = problems.length === 0;
      entry.problems = problems;
      record(`live ${sc.name} (${seconds}s, ${steps} steps)`, entry.ok, problems.join("; ") || res.answer.replace(/\s+/g, " ").slice(0, 140));
    } catch (error) {
      entry.ok = false;
      entry.problems = [error.message];
      record(`live ${sc.name}`, false, error.message);
      await shot(panel, `live-${sc.name}-panel`);
    }
    report.push(entry);
    writeFileSync(join(OUT, TOOL_CHOICE_REQUIRED ? "live-results-required.json" : "live-results.json"), JSON.stringify(report, null, 2));
  }
}

// ---------------------------------------------------------------------------
// VOICE (--voice): the backend speaks the command itself (POST /tts), the mp3
// becomes a 16 kHz WAV that Chromium's fake microphone plays. Real STT, real
// LLM, real TTS.
// ---------------------------------------------------------------------------
const VOICE_COMMAND = "Yarın saat on birde dişçi randevumu hatırlat";
const VOICE_HOLD_MS = 5_000;

async function makeVoiceWav(backendUrl) {
  const mp3 = join(OUT, "voice-command.mp3");
  const wav = join(OUT, "voice-command.wav");
  const r = await fetch(`${backendUrl}/tts`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ text: VOICE_COMMAND }),
  });
  const type = r.headers.get("content-type") ?? "";
  const bytes = Buffer.from(await r.arrayBuffer());
  record("voice: backend /tts renders the command (audio/mpeg)", r.ok && type.startsWith("audio/mpeg") && bytes.length > 1000,
    `${r.status} ${type} ${bytes.length} B`);
  if (!r.ok) throw new Error(`/tts failed: ${r.status} ${bytes.toString("utf8").slice(0, 200)}`);
  writeFileSync(mp3, bytes);
  const conv = spawnSync(PYTHON, [join(HERE, "make_voice_wav.py"), mp3, wav], { encoding: "utf8" });
  if (conv.status !== 0) throw new Error(`mp3->wav failed: ${conv.stderr.slice(-800)}`);
  log("voice: wav", wav, conv.stdout.trim());
  return wav;
}

/** Track /stt and /tts traffic of the extension pages. */
function watchVoiceTraffic(ctx) {
  const traffic = { stt: [], tts: [] };
  ctx.on("response", async (res) => {
    const url = res.url();
    const kind = /\/stt$/.test(url) ? "stt" : /\/tts$/.test(url) ? "tts" : null;
    if (!kind) return;
    const entry = { status: res.status(), type: res.headers()["content-type"] ?? "", at: Date.now() };
    if (kind === "tts") entry.text = (() => { try { return JSON.parse(res.request().postData() ?? "{}").text ?? ""; } catch { return ""; } })();
    if (kind === "stt") entry.body = await res.json().catch(() => null);
    traffic[kind].push(entry);
  });
  return traffic;
}

/** Deny any confirmation (none expected: reminders.add is risk "safe"). */
function denyConfirmations(panel) {
  const denials = [];
  let on = true;
  const done = (async () => {
    while (on) {
      const modal = panel.locator("dialog.modal[open]");
      if ((await modal.count()) > 0 && /Onayın gerekiyor/.test(await modal.innerText().catch(() => ""))) {
        denials.push((await modal.innerText()).replace(/\s+/g, " ").slice(0, 160));
        await modal.getByRole("button", { name: "Reddet" }).click().catch(() => undefined);
      }
      await sleep(300);
    }
  })();
  return { denials, stop: async () => { on = false; await done; } };
}

function remindersInDb(workdir) {
  // due_at is stored in UTC; report it in the backend's TIMEZONE (backend/.env).
  const code = [
    "import json,sqlite3,sys",
    "from datetime import datetime",
    "from zoneinfo import ZoneInfo",
    "from backend.db import load_env",
    "import os; load_env()",
    "tz=ZoneInfo(os.environ.get('TIMEZONE') or 'Europe/Istanbul')",
    "try:",
    " c=sqlite3.connect(sys.argv[1])",
    " print(json.dumps([[t, datetime.fromisoformat(d.replace('Z','+00:00')).astimezone(tz).isoformat()] for t,d in c.execute('select text, due_at from reminders')], ensure_ascii=False))",
    "except Exception as e: print(json.dumps({'error': str(e)}))",
  ].join("\n");
  const r = spawnSync(PYTHON, ["-c", code, join(workdir, "assistant.db")], { encoding: "utf8", cwd: ROOT, env: { ...process.env, PYTHONUTF8: "1" } });
  try { return JSON.parse(r.stdout.trim()); } catch { return { error: r.stderr.slice(-300) }; }
}

/**
 * One push-to-talk turn. `press`/`release` drive the gesture (mouse or Space).
 * Returns the finished exchange.
 */
async function voiceTurn(panel, traffic, label, press, release) {
  const index = await panel.locator("article.exchange").count();
  const sttBefore = traffic.stt.length;
  const ttsBefore = traffic.tts.length;
  await press();
  const recording = await waitFor(async () => (await panel.locator(".mic-button.is-recording").count()) > 0, 5_000, "recording state")
    .then(() => true, () => false);
  await sleep(VOICE_HOLD_MS - 1_500);
  const recText = await panel.locator(".input-hint").innerText().catch(() => "");
  const recSeconds = await panel.locator(".rec-time").innerText().catch(() => "");
  await shot(panel, `voice-${label}-recording`);
  await sleep(1_500);
  record(`voice/${label}: recording state shown while held`, recording && /Dinliyorum/.test(recText),
    `${recText.replace(/\s+/g, " ")} (${recSeconds})`);
  await release();
  const transcribing = await waitFor(async () => /yazıya çevriliyor/.test(await panel.locator(".input-hint").innerText()), 3_000, "transcribing")
    .then(() => true, () => false);
  const stt = await waitFor(async () => traffic.stt[sttBefore], 60_000, "/stt response").catch(() => null);
  record(`voice/${label}: /stt called and answered`, stt?.status === 200 && typeof stt?.body?.text === "string",
    stt ? `${stt.status} "${stt.body?.text ?? ""}" (${stt.body?.duration_s ?? "?"}s audio)${transcribing ? "" : " [transcribing label not seen]"}` : "no /stt request");
  const banner = async () => ((await panel.locator(".error-banner").count()) ? await panel.locator(".error-banner").innerText() : "");
  const ex = panel.locator("article.exchange").nth(index);
  const appeared = await ex.waitFor({ timeout: 15_000 }).then(() => true, () => false);
  const userText = appeared ? await ex.locator(".user-message p").innerText() : "";
  const voiceIcon = appeared ? (await ex.locator(".user-message .message-label svg").count()) > 0 : false;
  record(`voice/${label}: transcript sent as user message`, appeared && /randevu|dişçi|dis[cç]i/i.test(userText),
    appeared ? `"${userText}"${voiceIcon ? " [mic icon]" : " [no mic icon]"}` : `no exchange; banner: ${await banner()}`);
  if (!appeared) return null;

  const guard = denyConfirmations(panel);
  let res;
  try {
    res = await waitExchange(panel, index, LIVE_TIMEOUT);
  } finally {
    await guard.stop();
  }
  await shot(panel, `voice-${label}-answer`);
  const tools = (await res.ex.locator(".trace-step code.tool-code").allInnerTexts()).map((c) => c.split("(")[0].trim());
  record(`voice/${label}: agents add a reminder, answer mentions 11 / randevu`,
    !res.err && tools.includes("reminders.add") && /11/.test(res.answer) && /randevu|diş/i.test(res.answer),
    `${res.err ? `error: ${res.err}; ` : ""}tools=${tools.join(",")}; denials=${guard.denials.length}; "${res.answer.replace(/\s+/g, " ").slice(0, 140)}"`);

  const tts = await waitFor(async () => traffic.tts.slice(ttsBefore).find((t) => t.status), 45_000, "/tts response").catch(() => null);
  const playing = await waitFor(async () => (await panel.getByText("Sesli okumayı durdur").count()) > 0, 5_000, "speaker playing")
    .then(() => true, () => false);
  const b = await banner();
  record(`voice/${label}: TTS playback requested (/tts 200 audio/mpeg)`,
    tts?.status === 200 && tts.type.startsWith("audio/mpeg") && !/otomatik sesi engelledi/.test(b),
    tts ? `${tts.status} ${tts.type}; spoken="${(tts.text ?? "").slice(0, 100)}"; player ${playing ? "playing" : "not seen playing"}${b ? `; banner: ${b}` : ""}` : "no /tts request");
  // Stop playback before the next turn so audio does not overlap.
  await panel.getByText("Sesli okumayı durdur").click({ timeout: 2_000 }).catch(() => undefined);
  return res;
}

async function runVoice(panel, extId, workdir) {
  const traffic = watchVoiceTraffic(panel.context());
  const fs = await panel.evaluate(async () => {
    const st = await navigator.permissions.query({ name: "microphone" }).catch(() => null);
    return st?.state ?? "unknown";
  });
  log("voice: microphone permission state in panel:", fs, "ext", extId);

  // 1) mouse press-and-hold on the mic button
  const mic = panel.getByLabel("Basılı tut ve konuş");
  await mic.waitFor();
  await waitFor(async () => mic.isEnabled(), 10_000, "mic enabled");
  const box = await mic.boundingBox();
  await voiceTurn(panel, traffic, "mouse",
    async () => { await panel.mouse.move(box.x + box.width / 2, box.y + box.height / 2); await panel.mouse.down(); },
    async () => { await panel.mouse.up(); });

  // 2) Space push-to-talk (focus on the page body, not a control)
  await waitFor(async () => mic.isEnabled(), 30_000, "mic enabled again");
  await panel.evaluate(() => (document.activeElement instanceof HTMLElement ? document.activeElement.blur() : undefined));
  await voiceTurn(panel, traffic, "space",
    async () => { await panel.keyboard.down("Space"); },
    async () => { await panel.keyboard.up("Space"); });

  const rows = remindersInDb(workdir);
  record("voice: reminders persisted in DB", Array.isArray(rows) && rows.length >= 1 && rows.every((r) => /T11:00/.test(r[1] ?? "")),
    JSON.stringify(rows));
  writeFileSync(join(OUT, "voice-traffic.json"), JSON.stringify(traffic, null, 2));
}

/** Microphone denied: no fake-ui flag, no grant -> Chromium denies getUserMedia. */
async function runVoiceDenied(backendUrl, wav) {
  log("voice: permission-denied flow");
  const dir = mkdtempSync(join(tmpdir(), "polaris-chrome-deny-"));
  const ctx = await chromium.launchPersistentContext(dir, {
    channel: "chromium",
    headless: !HEADED,
    viewport: { width: 1280, height: 900 },
    args: [`--disable-extensions-except=${EXT_OUT}`, `--load-extension=${EXT_OUT}`,
      "--use-fake-device-for-media-stream", `--use-file-for-fake-audio-capture=${wav}`],
  });
  extraContexts.push(ctx);
  let [worker] = ctx.serviceWorkers();
  worker ??= await ctx.waitForEvent("serviceworker", { timeout: 20_000 });
  const extId = new URL(worker.url()).host;
  await worker.evaluate(async (backend) => {
    await chrome.storage.local.set({ "polaris-panel-settings": { backend, mock: false, speak: false } });
  }, backendUrl);
  const panel = ctx.pages()[0] ?? (await ctx.newPage());
  await panel.goto(`chrome-extension://${extId}/src/panel/index.html`);
  await panel.setViewportSize({ width: 420, height: 900 });
  // Explicit deny: a "block" content setting for the extension origin is what a
  // user who clicked "Block" ends up with. Playwright has no deny API, so an
  // un-granted prompt in automation is dismissed (= NotAllowedError).
  await ctx.clearPermissions();
  const mic = panel.getByLabel("Basılı tut ve konuş");
  await waitFor(async () => mic.isEnabled(), 20_000, "mic enabled (deny run)");
  const pagePromise = ctx.waitForEvent("page", { timeout: 15_000 }).catch(() => null);
  const box = await mic.boundingBox();
  await panel.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await panel.mouse.down();
  await sleep(1_500);
  await panel.mouse.up();
  const msg = await waitFor(async () => ((await panel.locator(".error-banner").count()) ? panel.locator(".error-banner").innerText() : ""), 10_000, "error banner")
    .catch(() => "");
  const state = await panel.locator(".input-hint").innerText().catch(() => "");
  record("voice/denied: Turkish permission message shown", /Mikrofon izni verilmedi|izin henüz verilmedi/.test(msg), `${msg || "(no banner)"} | hint="${state}"`);
  const grantBtn = (await panel.getByRole("button", { name: "Mikrofon izni ver" }).count()) > 0;
  const permPage = await pagePromise;
  if (permPage) await permPage.waitForLoadState("domcontentloaded").catch(() => undefined);
  const permUrl = permPage?.url() ?? "";
  const heading = permPage ? await permPage.locator("h1").innerText().catch(() => "") : "";
  record("voice/denied: permission.html opened + 'Mikrofon izni ver' button in panel",
    /\/src\/panel\/permission\.html$/.test(permUrl) && /Mikrofonunu/.test(heading) && grantBtn,
    `url=${permUrl || "(none)"}; h1="${heading}"; panel button=${grantBtn}`);
  await shot(panel, "voice-denied-panel");
  if (permPage) {
    // Clicking "Mikrofon izni ver" on the page while still denied explains why.
    await permPage.locator("#grant").click();
    const status = await waitFor(async () => {
      const t = await permPage.locator("#status").innerText();
      return /reddedildi|bulunamadı|alınamadı|verildi/.test(t) ? t : "";
    }, 10_000, "permission page status").catch(() => "");
    await shot(permPage, "voice-denied-permission-page");
    record("voice/denied: permission page explains the denial", /reddedildi|bulunamadı|alınamadı/.test(status), status || "(no status)");
  }
  await ctx.close().catch(() => undefined);
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------
async function main() {
  const backendPort = Number(process.env.BACKEND_PORT) || (await freePort());
  const staticPort = await freePort();
  const workdir = mkdtempSync(join(tmpdir(), "polaris-browser-e2e-"));

  buildExtension();
  patchCsp(backendPort);

  let llmUrl = null;
  if (!REAL_LLM) {
    log("fake LLM: starting");
    llmUrl = await startFakeLlm();
    log("fake LLM:", llmUrl);
  } else {
    log("LIVE mode: real LLM from backend/.env, real public pages");
  }
  log("backend: starting on", backendPort);
  await startBackend(backendPort, llmUrl, workdir);
  log("backend: healthy");
  await startStatic(staticPort);
  const fixtureUrl = REAL_LLM ? "about:blank" : `http://127.0.0.1:${staticPort}/fixture.html`;
  const backendUrl = `http://127.0.0.1:${backendPort}`;

  let voiceWav = null;
  if (VOICE) voiceWav = await makeVoiceWav(backendUrl);

  profileDir = mkdtempSync(join(tmpdir(), "polaris-chrome-"));
  context = await chromium.launchPersistentContext(profileDir, {
    channel: "chromium",
    headless: !HEADED,
    viewport: { width: 1280, height: 900 },
    args: [
      `--disable-extensions-except=${EXT_OUT}`, `--load-extension=${EXT_OUT}`,
      ...(VOICE ? ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", `--use-file-for-fake-audio-capture=${voiceWav}`] : []),
    ],
  });
  const consoleLog = createWriteStream(join(OUT, "console.log"));
  const watchConsole = (page, tag) => page.on("console", (m) => consoleLog.write(`[${tag}] ${m.type()}: ${m.text()}\n`));

  let [worker] = context.serviceWorkers();
  worker ??= await context.waitForEvent("serviceworker", { timeout: 20_000 });
  const extId = new URL(worker.url()).host;
  worker.on("console", (m) => consoleLog.write(`[sw] ${m.type()}: ${m.text()}\n`));
  record("extension loaded (service worker up)", Boolean(extId), extId);

  // Settings: mock OFF, backend = our port, speech off (no audio in CI).
  // (--voice: speech ON, the TTS path is part of the test.)
  await worker.evaluate(async ({ backend, speak }) => {
    await chrome.storage.local.set({ "polaris-panel-settings": { backend, mock: false, speak } });
  }, { backend: backendUrl, speak: VOICE });

  const panel = context.pages()[0] ?? (await context.newPage());
  watchConsole(panel, "panel");
  await panel.goto(`chrome-extension://${extId}/src/panel/index.html`);
  await panel.setViewportSize({ width: 420, height: 900 });

  // Fixture in its OWN window so it is the active tab there, like a real page
  // next to the side panel.
  const fixturePromise = context.waitForEvent("page", { timeout: 15_000 });
  await panel.evaluate((url) => chrome.windows.create({ url, focused: true }), fixtureUrl);
  const fixture = await fixturePromise;
  watchConsole(fixture, "fixture");
  if (!REAL_LLM) await armFixture(fixture);

  const connected = await waitFor(async () => (await panel.locator(".connection.connected").count()) > 0, 15_000, "panel connected")
    .then(() => true, () => false);
  record("panel connects to real backend (mock off)", connected, connected ? "" : await panel.locator(".connection").innerText().catch(() => "?"));

  // Settings UI reflects stored settings.
  await panel.getByLabel("Ayarlar").click();
  const backendField = await panel.locator("#backend").inputValue();
  const mockChecked = await panel.locator(".setting-row input[type=checkbox]").first().isChecked();
  record("settings UI shows backend URL and mock off", backendField === backendUrl && !mockChecked, `backend=${backendField} mock=${mockChecked}`);
  await shot(panel, "00-settings");
  await panel.locator("dialog.modal").getByLabel("Kapat").click();
  await shot(panel, "01-panel-connected");
  if (!connected) throw new Error("panel not connected; aborting scenarios");
  if (LIVE) {
    await runLive(panel, fixture);
    return;
  }
  if (VOICE) {
    await runVoice(panel, extId, workdir);
    await context.close().catch(() => undefined);
    context = null;
    await runVoiceDenied(backendUrl, voiceWav);
    return;
  }

  // ---------------- scenario 1: shop search ----------------
  log("scenario 1: shop search");
  await sendChat(panel, "Bu sitede mekanik klavye ara, ilk 3 fiyatı söyle");
  const s1 = await waitExchange(panel, 0);
  await shot(panel, "10-shop-panel");
  await shot(fixture, "11-shop-fixture");
  record("shop: final answer rendered", s1.answer.length > 0 && !s1.err, s1.err || s1.answer.slice(0, 120));
  record("shop: answer has first 3 fixture prices", /1\.299 TL.*1\.750 TL.*2\.100 TL/s.test(s1.answer), s1.answer.slice(0, 160));
  const qValue = await fixture.locator("#q").inputValue();
  record("shop: real DOM type into search box", qValue === "mekanik klavye", `#q="${qValue}"`);
  const searchLog = await fixture.locator("#search-log").innerText();
  record("shop: real DOM click on 'Ara' (form submit handler ran)", searchLog === "Arandı: mekanik klavye", searchLog);
  const need = ["router", "planner", "executor", "judge"];
  const missing = need.filter((a) => !s1.badges.includes(a));
  record("shop: timeline has router/planner/executor/judge", missing.length === 0, `badges=${[...new Set(s1.badges)].join(",")}`);
  const toolCodes = await s1.ex.locator(".trace-step code.tool-code").allInnerTexts();
  record("shop: timeline shows tool calls snapshot/type/click/read/highlight",
    ["browser.snapshot", "browser.type", "browser.click", "browser.read", "browser.highlight"].every((t) => toolCodes.some((c) => c.startsWith(t))),
    toolCodes.map((c) => c.split("(")[0]).join(","));
  const judgeOk = (await s1.ex.locator(".verdict.pass").count()) > 0;
  record("shop: judge verdict 'Geçti' rendered", judgeOk);
  const hlByTool = await fixture.evaluate(() => window.__hl);
  record("shop: browser.highlight drew overlay on page", hlByTool >= 1, `overlays=${hlByTool}`);

  const chip = s1.ex.locator(".trace-step button.chip").first();
  if ((await chip.count()) === 0) {
    record("shop: ref chip click highlights element", false, "no 'Öğe #' chip in timeline");
  } else {
    const before = await fixture.evaluate(() => window.__hl);
    const chipText = await chip.innerText();
    await chip.click();
    const after = await waitFor(async () => {
      const n = await fixture.evaluate(() => window.__hl);
      return n > before ? n : 0;
    }, 5_000, "highlight after chip").catch(() => before);
    const banner = (await panel.locator(".error-banner").count()) ? await panel.locator(".error-banner").innerText() : "";
    record("shop: ref chip click highlights element", after > before, `${chipText}; overlays ${before}->${after}${banner ? `; panel error: ${banner}` : ""}`);
    await shot(fixture, "12-chip-highlight");
  }
  const srcChip = s1.ex.locator(".sources button");
  if ((await srcChip.count()) > 0) {
    // The overlay host is reused while a previous blink (1.5 s) is running, so
    // wait for it to go away before counting a new one.
    await fixture.waitForFunction(() => !document.getElementById("ai-assistant-highlight-host"), null, { timeout: 5_000 }).catch(() => undefined);
    const before = await fixture.evaluate(() => window.__hl);
    await srcChip.first().click();
    const after = await waitFor(async () => {
      const n = await fixture.evaluate(() => window.__hl);
      return n > before ? n : 0;
    }, 5_000, "highlight after source chip").catch(() => before);
    record("shop: citation #ref chip highlights element", after > before, `overlays ${before}->${after}`);
  } else {
    record("shop: citation #ref chip highlights element", false, "no citation chip with ref rendered");
  }
  const tabSent = await fetch(`${backendUrl}/health`).then((r) => r.ok);
  void tabSent;

  // ---------------- scenario 2: risky submit, DENY ----------------
  log("scenario 2: form submit -> deny");
  await fixture.reload();
  await armFixture(fixture);
  await sendChat(panel, "Bu formu doldur ve gönder");
  const modal = panel.locator("dialog.modal");
  const modalShown = await modal.waitFor({ timeout: STEP_TIMEOUT }).then(() => true, () => false);
  record("deny: need_confirm modal shown", modalShown);
  if (modalShown) {
    const summary = await modal.innerText();
    record("deny: modal names browser.click on 'Gönder'", /browser\.click/.test(summary) && /Gönder/i.test(summary), summary.replace(/\s+/g, " ").slice(0, 160));
    const typedName = await fixture.locator("#fullname").inputValue();
    const typedMail = await fixture.locator("#email").inputValue();
    record("deny: fields typed before confirmation", typedName === "Ayşe Yılmaz" && typedMail === "ayse@example.com", `${typedName} / ${typedMail}`);
    await shot(panel, "20-confirm-modal");
    await modal.getByRole("button", { name: "Reddet" }).click();
  }
  const s2 = await waitExchange(panel, 1);
  await shot(panel, "21-deny-panel");
  const formLogDeny = await fixture.locator("#form-log").innerText();
  const clicksDeny = await fixture.evaluate(() => window.__clicks);
  record("deny: NO click on page (form not submitted)", formLogDeny === "Form gönderilmedi." && !clicksDeny.some((c) => c.includes("#send")), `${formLogDeny}; clicks=${JSON.stringify(clicksDeny)}`);
  record("deny: final answer says rejected", /reddetti|reddedildi|gönderilmedi/i.test(s2.answer), s2.err || s2.answer.slice(0, 120));

  // ---------------- scenario 3: risky submit, APPROVE ----------------
  log("scenario 3: form submit -> approve");
  await fixture.reload();
  await armFixture(fixture);
  await sendChat(panel, "Bu formu doldur ve gönder");
  const modal3 = await modal.waitFor({ timeout: STEP_TIMEOUT }).then(() => true, () => false);
  record("approve: need_confirm modal shown", modal3);
  if (modal3) await modal.getByRole("button", { name: "Onayla" }).click();
  const s3 = await waitExchange(panel, 2);
  await shot(panel, "30-approve-panel");
  await shot(fixture, "31-approve-fixture");
  const formLog = await fixture.locator("#form-log").innerText();
  record("approve: real click submitted the form", formLog === "Form gönderildi.", formLog);
  record("approve: final answer rendered", /gönderildi/i.test(s3.answer), s3.err || s3.answer.slice(0, 120));
  const passwordUntouched = (await fixture.locator("#password").inputValue()) === "";
  record("approve: password field untouched", passwordUntouched);
}

let fatal = null;
try {
  await main();
} catch (error) {
  fatal = error;
  log("FATAL", error.stack ?? String(error));
} finally {
  await cleanup();
}

const failed = results.filter((r) => !r.ok);
writeFileSync(join(OUT, VOICE ? "results-voice.json" : LIVE ? "results-live.json" : "results.json"), JSON.stringify({ fatal: fatal ? String(fatal.message ?? fatal) : null, results }, null, 2));
log("");
log("==================== SUMMARY ====================");
for (const r of results) log(r.ok ? "PASS" : "FAIL", r.name);
log(`${results.length - failed.length}/${results.length} passed${fatal ? " (aborted: " + fatal.message + ")" : ""}`);
log(`rerun: node scripts/browser_e2e/run.mjs${LIVE ? " --live" : ""}${VOICE ? " --voice" : ""}   (artifacts: scripts/browser_e2e/out/)`);
logFile.end();
process.exit(failed.length || fatal ? 1 : 0);
