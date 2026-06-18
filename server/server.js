/**
 * AI-Powered Desktop Automation Code Generator - Express Bridge Server
 * =====================================================================
 *  - Proxies start/stop commands from the React UI to the Python agent (4444)
 *  - Receives captured events from the agent and broadcasts them over SSE
 *  - Calls the Groq API (llama-3.3-70b-versatile) to generate two TestNG
 *    Page-Object Java files sequentially (ById then ByClass, 4s apart)
 *    to stay within Groq free-tier rate limits
 *
 *    npm install
 *    node server.js          -> http://localhost:3002
 */

const path = require("path");
const fs = require("fs");

// Load .env for local validation (UI requests still use user-supplied key)
require("dotenv").config({ path: path.join(__dirname, "..", ".env") });

const express = require("express");
const cors = require("cors");

const PORT = 3002;
const AGENT_URL = "http://localhost:4444";
const GROQ_URL = "https://api.groq.com/openai/v1/chat/completions";
const GROQ_MODEL = "llama-3.3-70b-versatile";

const TESTRUNNER_JAVA_DIR = path.join(__dirname, "..", "test-runner", "src", "test", "java", "com", "qaforge", "tests");
const PLAYWRIGHT_OUT_DIR = path.join(__dirname, "..", "generated-playwright");

const app = express();
app.use(cors());
app.use(express.json({ limit: "5mb" }));

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
let events = [];          // recorded session
let sseClients = [];      // open SSE responses
let recording = false;
let sessionInfo = { appName: "", exePath: "", platform: "Windows" };

function broadcast(type, payload) {
  const msg = `event: ${type}\ndata: ${JSON.stringify(payload)}\n\n`;
  sseClients = sseClients.filter((res) => {
    try { res.write(msg); return true; } catch { return false; }
  });
}

async function callAgent(path, body) {
  const res = await fetch(`${AGENT_URL}${path}`, {
    method: body ? "POST" : "GET",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(8000),
  });
  return res.json();
}

// ---------------------------------------------------------------------------
// Recording control (proxied to the Python agent)
// ---------------------------------------------------------------------------
app.post("/api/start", async (req, res) => {
  try {
    const { appName, exePath, platform } = req.body;
    if (!exePath) return res.status(400).json({ ok: false, message: "exePath is required" });
    const out = await callAgent("/start", { appName, exePath, platform });
    if (out.ok) {
      events = [];
      recording = true;
      sessionInfo = { appName, exePath, platform };
      broadcast("status", { recording: true, eventCount: 0 });
    }
    res.status(out.ok ? 200 : 400).json(out);
  } catch (e) {
    res.status(502).json({ ok: false, message: `Agent unreachable: ${e.message}` });
  }
});

app.post("/api/stop", async (req, res) => {
  try {
    const out = await callAgent("/stop", {});
    recording = false;
    broadcast("status", { recording: false, eventCount: events.length });
    res.status(out.ok ? 200 : 400).json(out);
  } catch (e) {
    recording = false;
    res.status(502).json({ ok: false, message: `Agent unreachable: ${e.message}` });
  }
});

app.get("/api/status", async (req, res) => {
  let agent = { online: false };
  try { agent = await callAgent("/status"); agent.online = true; } catch { /* offline */ }
  res.json({
    agentOnline: agent.online,
    isAdmin: agent.isAdmin ?? null,
    recording,
    eventCount: events.length,
  });
});

// ---------------------------------------------------------------------------
// Event ingestion (from the Python agent) + live feed (SSE to the browser)
// ---------------------------------------------------------------------------
app.post("/api/events", (req, res) => {
  const event = req.body;
  events.push(event);
  broadcast("capture", event);
  res.json({ ok: true });
});

app.get("/api/events", (req, res) => res.json(events));

app.delete("/api/events", (req, res) => {
  events = [];
  broadcast("status", { recording, eventCount: 0 });
  res.json({ ok: true });
});

app.delete("/api/events/:index", (req, res) => {
  const idx = parseInt(req.params.index, 10);
  if (isNaN(idx) || idx < 0 || idx >= events.length) {
    return res.status(400).json({ ok: false, message: "Invalid event index" });
  }
  events.splice(idx, 1);
  broadcast("snapshot", { events, recording });
  res.json({ ok: true, eventCount: events.length });
});

app.get("/api/stream", (req, res) => {
  res.setHeader("Content-Type", "text/event-stream");
  res.setHeader("Cache-Control", "no-cache, no-transform");
  res.setHeader("Connection", "keep-alive");
  res.flushHeaders();
  // initial snapshot so a refreshed dashboard catches up
  res.write(`event: snapshot\ndata: ${JSON.stringify({ events, recording })}\n\n`);
  sseClients.push(res);
  const hb = setInterval(() => { try { res.write("event: heartbeat\ndata: {}\n\n"); } catch {} }, 10000);
  req.on("close", () => {
    clearInterval(hb);
    sseClients = sseClients.filter((c) => c !== res);
  });
});

// ---------------------------------------------------------------------------
// AI code generation (Groq)
// ---------------------------------------------------------------------------
const PLATFORM_MAP = {
  Windows: { driver: "WindowsDriver", driverImport: "io.appium.java_client.windows.WindowsDriver", idHint: "AutomationId (use By.id / @WindowsFindBy(accessibility = ...) style: MobileBy.AccessibilityId or By.name fallback)", note: "WinAppDriver at http://127.0.0.1:4723, capability 'app' = exe path" },
  Android: { driver: "AndroidDriver", driverImport: "io.appium.java_client.android.AndroidDriver", idHint: "resource-id via By.id", note: "UiAutomator2" },
  iOS: { driver: "IOSDriver", driverImport: "io.appium.java_client.ios.IOSDriver", idHint: "accessibility id", note: "XCUITest" },
};

const SYSTEM_PROMPT = `You are an expert QA automation engineer. You generate COMPLETE, COMPILABLE, RUNNABLE Appium Java test code for desktop/mobile applications. You output ONLY raw Java source code - no markdown fences, no explanations, no comments like TODO or FIXME, no placeholders.

Hard rules for every file you produce:
- package com.qaforge.tests;
- TestNG framework with @BeforeClass (driver setup), @Test (the recorded flow), @AfterClass (driver.quit()).
- Page Object Model: one Page class containing locators + action methods, one Test class that uses it. Both classes in the SAME file (Page class non-public).
- Every interaction is preceded by an explicit wait: WebDriverWait with Duration.ofSeconds(15) and ExpectedConditions (elementToBeClickable for clicks, presenceOfElementLocated/visibilityOfElementLocated for typing). NEVER use Thread.sleep().
- Before each action in the test method, print a step log: System.out.println("[STEP n] ...").
- End the test with at least one Assert that verifies the final state (e.g. an element is displayed or the session is active).
- Method and field names must be descriptive, derived from the element names.
- The code must compile with Appium java-client 8.6.0 and TestNG 7.x.

CRITICAL - exact imports (copy these verbatim, do not alter the package paths):
import java.net.MalformedURLException;
import java.net.URL;
import java.time.Duration;
import org.openqa.selenium.By;
import org.openqa.selenium.WebElement;
import org.openqa.selenium.interactions.Actions;
import org.openqa.selenium.support.ui.ExpectedConditions;
import org.openqa.selenium.support.ui.WebDriverWait;
import org.testng.Assert;
import org.testng.annotations.AfterClass;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.Test;
import io.appium.java_client.AppiumBy;
import io.appium.java_client.windows.WindowsDriver;
import io.appium.java_client.windows.options.WindowsOptions;

CRITICAL - driver initialisation (two-step; supports Win32 AND UWP apps):
  Step 1: launch the process with ProcessBuilder (works for Win32 and UWP alike).
  Step 2: root session — wait until the window appears, grab its handle, then attach.
  Use this exact pattern in @BeforeClass setUp() throws Exception:

  // 1. Launch (works for Win32 and UWP; setApp(exePath) alone fails for UWP)
  new ProcessBuilder("<exePath>").start();

  // 2. Root session — wait for the window, capture its native handle
  WindowsOptions desktopOpts = new WindowsOptions();
  desktopOpts.setApp("Root");
  WindowsDriver desktopDriver = new WindowsDriver(new URL("http://127.0.0.1:4723"), desktopOpts);
  WebDriverWait desktopWait = new WebDriverWait(desktopDriver, Duration.ofSeconds(15));
  WebElement appWindow = desktopWait.until(
      ExpectedConditions.presenceOfElementLocated(
          By.xpath("//Window[contains(@Name,'<windowTitle>')]")));
  String hexHandle = "0x" + Long.toHexString(Long.parseLong(appWindow.getAttribute("NativeWindowHandle")));
  desktopDriver.quit();

  // 3. Attach to the running window via appTopLevelWindow
  WindowsOptions options = new WindowsOptions();
  options.setCapability("appTopLevelWindow", hexHandle);
  driver = new WindowsDriver(new URL("http://127.0.0.1:4723"), options);

  NEVER use setApp(exePath) as the sole init — it cannot launch UWP apps.
  NEVER use DesiredCapabilities — incompatible with java-client 8.x.
  Replace <exePath> and <windowTitle> with the actual values provided in the user prompt.
  NEVER translate or anglicize <windowTitle> — use the exact string as given (it may be non-English, e.g. "계산기").

CRITICAL - locators (use AppiumBy, not MobileBy which is deprecated):
  AppiumBy.accessibilityId("automationId")   // AutomationId-based strategy
  By.className("className")                  // ClassName-based strategy
  By.name("elementName")                     // fallback when id/class empty`;

function buildUserPrompt(strategy, appName, platform, eventList) {
  const p = PLATFORM_MAP[platform] || PLATFORM_MAP.Windows;
  const raw = appName.replace(/[^A-Za-z0-9]/g, "") || "MyApp";
  const className = `${raw.charAt(0).toUpperCase() + raw.slice(1)}Test${strategy === "id" ? "ById" : "ByClass"}`;
  const windowTitle = eventList[0]?.element?.windowTitle || appName;
  const locatorRule = strategy === "id"
    ? `Locator strategy: use the element's AutomationId as the primary locator (${p.idHint}). If an event has an empty automationId, fall back to By.name(element name), then By.className.`
    : `Locator strategy: use the element's ClassName as the primary locator (By.className). When several elements share a class, use XPath to disambiguate — e.g. By.xpath("//Button[@Name='계산기']"). NEVER chain By.className(...).and(...) — By has no and() method; use XPath instead.`;

  return `Target application: "${appName}" on platform ${platform} (${p.note}).
Driver class: ${p.driver} (import ${p.driverImport}).
Public test class name: ${className}. Page class name: ${className.replace("Test", "Page")}.
Exe path for ProcessBuilder: use the exePath from the note section above.
Window title for root-session XPath lookup in setUp: "${windowTitle}" (use as-is — may be non-English; do NOT translate or replace with the English app name)

${locatorRule}

Recorded user session (in order). Convert EVERY event into a page-object action + a test step:
- click / doubleClick / rightClick -> click(), Actions.doubleClick(), Actions.contextClick()
- type -> sendKeys(value) into the recorded input field (clear() first); if value contains "\\n", keep it verbatim — sendKeys("text\\n") triggers Enter in WinAppDriver
- scroll -> a scroll action on the window (e.g. Actions or executeScript), keep it simple

${JSON.stringify(eventList, null, 2)}

Output ONLY the Java source code of the single .java file. Start directly with "package com.qaforge.tests;".`;
}


// ---------------------------------------------------------------------------
// Playwright Python generation
// ---------------------------------------------------------------------------
const PLAYWRIGHT_SYSTEM_PROMPT = `You are an expert test automation engineer. Generate COMPLETE, SYNTACTICALLY VALID Python Playwright test code. Output ONLY raw Python source code — no markdown fences, no explanations, no placeholders, no TODO comments.

Hard rules:
- Use playwright.sync_api: from playwright.sync_api import sync_playwright, expect, Page
- pytest-style: one test function named test_<appname>_flow(page: Page)
- Use a conftest-style fixture OR inline sync_playwright context if no page fixture available.
- Every action must use a locator with an explicit timeout: page.locator(...).wait_for(timeout=15000)
- Locator strategy: prefer get_by_role, get_by_label, get_by_text, or page.locator('[name="..."]')
- NEVER use time.sleep() — use wait_for or expect(...).to_be_visible(timeout=15000)
- Print step logs: print("[STEP n] ...")
- End with an expect() assertion verifying visible state
- The file must pass: python -c "compile(open('file.py').read(), 'file.py', 'exec')"`;

function buildPlaywrightPrompt(appName, platform, eventList) {
  const safeName = (appName || "myapp").replace(/[^a-z0-9]/gi, "_").toLowerCase();
  return `Application: "${appName}" on ${platform}.
Test function name: test_${safeName}_flow
File name: test_${safeName}_playwright.py

Convert EVERY recorded event into a Playwright action:
- click -> page.locator(...).click()
- doubleClick -> page.locator(...).dblclick()
- rightClick -> page.locator(...).click(button="right")
- type -> page.locator(...).fill(value)  (or .type() for char-by-char)
- scroll -> page.mouse.wheel(0, delta_y)

Recorded session:
${JSON.stringify(eventList, null, 2)}

Output ONLY the Python source. Start directly with the imports.`;
}

async function groqGeneratePlaywright(apiKey, appName, platform, eventList) {
  const res = await fetch(GROQ_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${apiKey}` },
    body: JSON.stringify({
      model: GROQ_MODEL,
      temperature: 0.2,
      max_tokens: 4000,
      messages: [
        { role: "system", content: PLAYWRIGHT_SYSTEM_PROMPT },
        { role: "user", content: buildPlaywrightPrompt(appName, platform, eventList) },
      ],
    }),
  });
  if (!res.ok) {
    const text = await res.text();
    const hints = { 401: "Invalid Groq API key", 429: "Groq rate limit exceeded" };
    throw new Error(hints[res.status] ?? `Groq API ${res.status}: ${text.slice(0, 300)}`);
  }
  const data = await res.json();
  return stripFences(data.choices?.[0]?.message?.content || "");
}

function stripFences(code) {
  return code.replace(/^```[a-z]*\s*/i, "").replace(/```\s*$/, "").trim();
}

async function groqGenerate(apiKey, strategy, appName, platform, eventList) {
  const res = await fetch(GROQ_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${apiKey}` },
    body: JSON.stringify({
      model: GROQ_MODEL,
      temperature: 0.2,
      max_tokens: 6000,
      messages: [
        { role: "system", content: SYSTEM_PROMPT },
        { role: "user", content: buildUserPrompt(strategy, appName, platform, eventList) },
      ],
    }),
  });
  if (!res.ok) {
    const text = await res.text();
    const hints = {
      401: 'Invalid Groq API key — check your key at console.groq.com',
      429: 'Groq rate limit exceeded — wait a moment and retry',
    };
    throw new Error(hints[res.status] ?? `Groq API ${res.status}: ${text.slice(0, 300)}`);
  }
  const data = await res.json();
  return stripFences(data.choices?.[0]?.message?.content || "");
}

app.post("/api/generate", async (req, res) => {
  const { apiKey, appName, platform, framework = "appium" } = req.body;
  const name = appName || sessionInfo.appName || "MyApp";
  const plat = platform || sessionInfo.platform || "Windows";

  if (!apiKey) return res.status(400).json({ ok: false, message: "Groq API key is required" });
  if (events.length === 0) return res.status(400).json({ ok: false, message: "No recorded events to generate from" });

  const slim = events.map((e, i) => ({
    step: i + 1,
    action: e.action,
    value: e.value,
    element: {
      name: e.element?.name,
      automationId: e.element?.automationId,
      className: e.element?.className,
      controlType: e.element?.controlType,
      windowTitle: e.element?.windowTitle,
    },
  }));

  broadcast("generation", { status: "started", framework });
  try {
    let payload;
    if (framework === "playwright") {
      const base = name.replace(/[^a-z0-9]/gi, "_").toLowerCase() || "myapp";
      const code = await groqGeneratePlaywright(apiKey, name, plat, slim);
      payload = {
        ok: true,
        framework: "playwright",
        files: [{ filename: `test_${base}_playwright.py`, content: code }],
      };
    } else {
      const rawBase = name.replace(/[^A-Za-z0-9]/g, "") || "MyApp";
      const base = rawBase.charAt(0).toUpperCase() + rawBase.slice(1);
      const byId = await groqGenerate(apiKey, "id", name, plat, slim);
      await new Promise((r) => setTimeout(r, 4000));
      const byClass = await groqGenerate(apiKey, "class", name, plat, slim);
      payload = {
        ok: true,
        framework: "appium",
        files: [
          { filename: `${base}TestById.java`, content: byId },
          { filename: `${base}TestByClass.java`, content: byClass },
        ],
      };
    }
    // Persist to disk (non-fatal)
    if (framework === "playwright") {
      const { savedPaths, saveError } = saveFiles(payload.files, PLAYWRIGHT_OUT_DIR);
      payload.savedPaths = savedPaths;
      if (saveError) payload.saveError = saveError;
    } else {
      cleanJavaTestFiles(TESTRUNNER_JAVA_DIR);
      const { savedPaths, saveError } = saveFiles(payload.files, TESTRUNNER_JAVA_DIR);
      payload.savedPaths = savedPaths;
      if (saveError) payload.saveError = saveError;
    }
    broadcast("generation", { status: "success", files: payload.files.map(f => f.filename) });
    res.json(payload);
  } catch (e) {
    broadcast("generation", { status: "error", message: e.message });
    res.status(500).json({ ok: false, message: e.message });
  }
});

// ---------------------------------------------------------------------------
// File persistence helpers (non-fatal — generation result is returned regardless)
// ---------------------------------------------------------------------------
function cleanJavaTestFiles(dir) {
  try {
    for (const f of fs.readdirSync(dir)) {
      if (f.endsWith("TestById.java") || f.endsWith("TestByClass.java")) {
        fs.unlinkSync(path.join(dir, f));
      }
    }
  } catch { /* dir may not exist yet */ }
}

function saveFiles(files, dir) {
  const savedPaths = [];
  let saveError;
  try {
    fs.mkdirSync(dir, { recursive: true });
    for (const f of files) {
      const fp = path.join(dir, f.filename);
      fs.writeFileSync(fp, f.content, "utf8");
      savedPaths.push(fp);
    }
  } catch (e) {
    saveError = e.message;
  }
  return { savedPaths, saveError };
}

app.listen(PORT, () =>
  console.log(`[bridge] Express server listening on http://localhost:${PORT}`)
);


