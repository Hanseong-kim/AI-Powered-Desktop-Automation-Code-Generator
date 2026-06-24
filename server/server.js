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

// Tell the UI whether the server has a .env key, WITHOUT exposing the key.
// If true, the UI can leave the key field blank and the server uses its own key.
app.get("/api/config", (req, res) => {
  res.json({ hasServerKey: !!process.env.GROQ_API_KEY });
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
- End the @Test method (NOT @AfterClass) with at least one Assert that verifies the final state. The asserted element MUST be one that ALREADY appears earlier in the recorded session — reuse the exact locator of one of the recorded steps (e.g. the last clicked element). NEVER introduce a NEW locator or control just for the assert (do NOT invent By.className("Edit") or a results/display field that was never recorded). Only WebElement.isDisplayed()/isEnabled() or element text comparisons are allowed, e.g. Assert.assertTrue(element.isDisplayed()). NEVER assert on a non-existent driver API such as driver.getSessionDetails().
- @AfterClass tearDown() must be null-safe: if (driver != null) { driver.quit(); } and contain NO assertions.
- Method and field names must be descriptive, derived from the element names.
- The code must compile with Appium java-client 8.6.0 and TestNG 7.x.

CRITICAL - WinAppDriver interaction rules (never break):
- NEVER import or use org.openqa.selenium.interactions.Actions for pointer actions (click, doubleClick, moveToElement, contextClick). These are unsupported by WinAppDriver 1.x and will throw UnsupportedCommandException.
- EXCEPTION 1 (coordinate fallback): When event.element.locatorFallback == "coordinate", the element cannot be resolved by UIA. Generate a coordinate click using Actions wheel/pointer is still banned — use instead:
    import org.openqa.selenium.interactions.Actions;
    new Actions(driver).moveToLocation({x}, {y}).click().build().perform();
  Add this import ONLY when a coordinate-fallback event exists in the session.
- EXCEPTION 2 (scroll): See SCROLL rule below.
- NEVER use contextClick(), doubleClick(), moveToElement(), or any W3C mouse pointer Actions. WinAppDriver 1.x does not support mouse pointer type in W3C Actions API and will throw UnsupportedCommandException at runtime.
- If the recorded event is a right-click or double-click, map it strictly to a simple left-click: element.click();
- All clicks must use element.click() directly on the WebElement (except where EXCEPTION 1 or EXCEPTION 2 above apply).

CRITICAL - exact imports (copy these verbatim, do not alter the package paths):
import java.net.MalformedURLException;
import java.net.URL;
import java.time.Duration;
import org.openqa.selenium.By;
import org.openqa.selenium.Keys;
import org.openqa.selenium.WebElement;
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

  // 1. Launch. Use the EXACT launch statement given in the user prompt.
  //    Win32 -> new ProcessBuilder("C:\\path\\to\\app.exe").start();
  //    UWP   -> new ProcessBuilder("explorer.exe", "shell:AppsFolder\\<AUMID>").start();
  //    (setApp(exePath) alone fails for UWP; a versioned WindowsApps exe path is ACL-blocked)
  <launchStatement from the user prompt>;

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
  Use the launch statement and <windowTitle> exactly as provided in the user prompt.
  NEVER translate or anglicize <windowTitle> — use the exact string as given (it may be non-English, e.g. "계산기").

CRITICAL - locators: each event carries locatorStrategy and locatorValue — use them directly, never guess:
  locatorStrategy == "automationId" → AppiumBy.accessibilityId("{locatorValue}")
  locatorStrategy == "name"         → By.name("{locatorValue}")
  locatorStrategy == "className"    → By.className("{locatorValue}")
  locatorStrategy == "xpath"        → By.xpath("{locatorValue}")
  locatorStrategy == "coordinate"   → new Actions(driver).moveToLocation({x}, {y}).click().build().perform()
    (add import org.openqa.selenium.interactions.Actions only when coordinate events exist)
  Never use MobileBy (deprecated). Never fabricate a locator not derived from locatorStrategy/locatorValue.

POPUP WINDOWS: When an event carries "isPopup": true and "popupTitle": "<title>":
  Switch context to the popup before interacting with its elements:

  WebDriverWait popupWait = new WebDriverWait(driver, Duration.ofSeconds(15));
  WebElement popupWindow = popupWait.until(
      ExpectedConditions.presenceOfElementLocated(
          By.xpath("//Window[contains(@Name,\"<popupTitle>\")]")));

  Then locate the popup's child element within the driver session normally
  (WinAppDriver searches the full tree from the attached window, so a standard
  driver.findElement() call will find elements inside the popup).
  Do NOT close the popup unless a recorded dismiss action (click X / Cancel) exists.

MULTI-WINDOW PAGE OBJECTS: Events carry a "screenId" field (sanitized window title).
  When two or more distinct screenIds appear in the session:
  - Create one Page Object class per screenId. Class name: PascalCase of screenId + "Page"
    (e.g. screenId "add_new_download" → AddNewDownloadPage).
  - The main/first screenId maps to the primary Page class (already named {App}Page).
  - Each secondary Page class receives the driver in its constructor; no new driver session.
  - In the @Test method, call methods in order. When screenId changes between steps,
    add a comment: // --- switch to <screenId> ---
  - NEVER create a new WindowsDriver for a secondary window; use the same driver instance.
  If all events share the same screenId, use a single Page class (no change from default).`;

function buildUserPrompt(strategy, appName, platform, eventList, exePath = "") {
  const p = PLATFORM_MAP[platform] || PLATFORM_MAP.Windows;
  const raw = appName.replace(/[^A-Za-z0-9]/g, "") || "MyApp";
  const className = `${raw.charAt(0).toUpperCase() + raw.slice(1)}Test${strategy === "id" ? "ById" : "ByClass"}`;
  const windowTitle = eventList[0]?.element?.windowTitle || appName;
  // UWP apps are passed as an AUMID ("PackageFamilyName!AppId") and must be
  // launched through the shell AppsFolder; Win32 apps use the plain exe path.
  // Emit the EXACT ProcessBuilder statement so the model copies it verbatim.
  const isUwp = exePath.includes("!");
  const launchLine = isUwp
    ? `new ProcessBuilder("explorer.exe", "shell:AppsFolder\\\\${exePath}").start();`
    : `new ProcessBuilder("${exePath.replace(/\\/g, "\\\\")}").start();`;
  const locatorRule = strategy === "id"
    ? `Locator strategy: use the element's AutomationId as the primary locator (${p.idHint}). If an event has an empty automationId, fall back to By.name(element name), then By.className.`
    : `Locator strategy: locate EVERY element by XPath using ClassName and Name as ATTRIBUTES on a wildcard node — By.xpath("//*[@ClassName='<className>' and @Name='<name>']"), e.g. By.xpath("//*[@ClassName='Button' and @Name='9']"). CRITICAL: in WinAppDriver XPath the node tag is the CONTROL TYPE (Edit, Button, Document, Text, Window...), NOT the ClassName — so NEVER write //<className>[...] such as //RichEditD2DPT[@Name='...'] (RichEditD2DPT is a ClassName, not a control type → it matches nothing and times out). Always put ClassName in an [@ClassName='...'] predicate on //*. Why not a bare By.className: many controls share a ClassName (every calculator key is 'Button'), so By.className returns the FIRST match (often the wrong/menu element). RULES: (1) ClassName + Name both present → By.xpath("//*[@ClassName='cls' and @Name='name']"). (2) Name empty → By.className(cls). (3) ClassName empty → By.name(name); if Name also empty → AppiumBy.accessibilityId(automationId). NEVER fabricate a control such as By.className("Edit"). NEVER chain By.className(...).and(...) — By has no and() method; use the XPath form above.`;

  return `Target application: "${appName}" on platform ${platform} (${p.note}).
Driver class: ${p.driver} (import ${p.driverImport}).
Public test class name: ${className}. Page class name: ${className.replace("Test", "Page")}.
Launch step — use EXACTLY this statement for the ProcessBuilder launch in setUp() (copy verbatim, do not alter or substitute a path):
  ${launchLine}
Window title for root-session XPath lookup in setUp: "${windowTitle}" (use as-is — may be non-English; do NOT translate or replace with the English app name)

${locatorRule}

Recorded user session (in order). Convert EVERY event into a page-object action + a test step:
- click / doubleClick / rightClick -> ALL map to element.click(). WinAppDriver does not support mouse Actions; NEVER emit Actions.doubleClick() or Actions.contextClick(). A double-click or right-click in the recording becomes a single element.click().
  If element.locatorFallback == "coordinate": use new Actions(driver).moveToLocation(x, y).click().build().perform() instead.
- type -> locate the SAME element recorded for THIS event (using the locator strategy and its fallbacks defined above) and send the keys to it. NEVER invent or assume a separate input field such as By.className("Edit"). CRITICAL — newlines: WinAppDriver does NOT convert a literal "\\n" in a string into the Enter key (it gets swallowed → all text lands on one line). So do NOT call sendKeys("text\\n"). Instead split the value on "\\n" and send Keys.ENTER (org.openqa.selenium.Keys) between segments. Use a helper method like:
      private void typeWithEnter(WebElement el, String value) {
          String[] lines = value.split("\\n", -1);
          for (int i = 0; i < lines.length; i++) {
              if (!lines[i].isEmpty()) el.sendKeys(lines[i]);
              if (i < lines.length - 1) el.sendKeys(Keys.ENTER);
          }
      }
  Do NOT call clear() before typing — recorded type events are sequential/appended text, and clear() is unreliable on rich-text controls.
- scroll -> generate a W3C wheel action using Actions (this is the only other permitted use of Actions besides coordinate fallback):
    import org.openqa.selenium.interactions.Actions;
    new Actions(driver).moveToLocation({x}, {y}).scrollByAmount(0, {delta}).build().perform();
  Use the event's x, y, and delta fields. Add the Actions import only when scroll events exist.
  Do NOT use driver.executeScript for scroll.

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

const WDIO_SYSTEM_PROMPT = `You are an expert QA automation engineer. Generate COMPLETE, RUNNABLE WebdriverIO v8 JavaScript test code for Windows Desktop apps via WinAppDriver. Output ONLY raw JavaScript — no markdown fences, no explanations, no TODO comments.

Hard rules:
- Use ESM-style async/await: import { remote } from 'webdriverio';
- One file exports: a default describe() block with beforeAll, afterAll, and it() test cases.
- beforeAll: connect to WinAppDriver at http://127.0.0.1:4723 using remote({ capabilities: { platformName: 'Windows', 'appium:app': 'Root' } }). After connecting, find the app window and re-attach with appTopLevelWindow capability.
- afterAll: await driver.deleteSession()
- Every action awaited: await $(selector).click(), await $(selector).setValue(value)
- Locators:
    AutomationId: await $('~automationId')   // accessibility id prefix ~
    ClassName:    await $('ClassName')
    XPath:        await $('//*[@AutomationId="id"]')
- NEVER use browser global — always use the local driver variable from remote().
- Scroll: await driver.action('wheel').move({ x, y }).scroll({ deltaX: 0, deltaY: delta }).perform()
- For coordinate fallback (locatorFallback == "coordinate"): await driver.action('pointer').move({ x, y }).down().up().perform()
- Print step logs: console.log('[STEP n] ...')
- End with an expect assertion using expect(await $(selector).isDisplayed()).toBe(true)`;

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

function stripFences(code) {
  return code.replace(/^```[a-z]*\s*/i, "").replace(/```\s*$/, "").trim();
}

// Single Groq chat call with automatic retry on 429 (rate limit). This lets us
// fire the two generations in parallel safely: if a momentary TPM cap is hit,
// we honour the Retry-After header (or back off) instead of failing the request.
async function groqChat(apiKey, { system, user, maxTokens }, attempt = 0) {
  const res = await fetch(GROQ_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${apiKey}` },
    body: JSON.stringify({
      model: GROQ_MODEL,
      temperature: 0.2,
      max_tokens: maxTokens,
      messages: [
        { role: "system", content: system },
        { role: "user", content: user },
      ],
    }),
  });
  if (res.status === 429 && attempt < 3) {
    const retryAfter = parseFloat(res.headers.get("retry-after")) || (1.5 * (attempt + 1));
    await new Promise((r) => setTimeout(r, Math.min(retryAfter, 10) * 1000));
    return groqChat(apiKey, { system, user, maxTokens }, attempt + 1);
  }
  if (!res.ok) {
    const text = await res.text();
    const hints = {
      401: "Invalid Groq API key — check your key at console.groq.com",
      429: "Groq rate limit exceeded — wait a moment and retry",
    };
    throw new Error(hints[res.status] ?? `Groq API ${res.status}: ${text.slice(0, 300)}`);
  }
  const data = await res.json();
  return stripFences(data.choices?.[0]?.message?.content || "");
}

function groqGeneratePlaywright(apiKey, appName, platform, eventList) {
  return groqChat(apiKey, {
    system: PLAYWRIGHT_SYSTEM_PROMPT,
    user: buildPlaywrightPrompt(appName, platform, eventList),
    maxTokens: 4000,
  });
}

function groqGenerate(apiKey, strategy, appName, platform, eventList, exePath) {
  return groqChat(apiKey, {
    system: SYSTEM_PROMPT,
    user: buildUserPrompt(strategy, appName, platform, eventList, exePath),
    maxTokens: 4000,
  });
}

function buildWdioPrompt(strategy, appName, platform, eventList, exePath) {
  const raw = appName.replace(/[^A-Za-z0-9]/g, '') || 'MyApp';
  const base = raw.charAt(0).toUpperCase() + raw.slice(1);
  const className = `${base}Test${strategy === 'id' ? 'ById' : 'ByClass'}`;
  const windowTitle = eventList[0]?.element?.windowTitle || appName;
  const isUwp = (exePath || '').includes('!');
  const launchLine = isUwp
    ? `await exec('explorer.exe "shell:AppsFolder\\\\${exePath}"');`
    : `await exec(${JSON.stringify(exePath)});`;
  const locatorRule = strategy === 'id'
    ? `Locator strategy: prefer $('~automationId') (accessibility id). Fall back to $('[name="${'name'}"]') then $('ClassName').`
    : `Locator strategy: locate by XPath $('//*[@ClassName="cls" and @Name="name"]'). If Name empty: $('ClassName'). If ClassName empty: $('//*[@Name="name"]').`;

  return `Application: "${appName}", platform: ${platform}.
Describe block name: "${className}".
Launch: ${launchLine}
Window title for root attach: "${windowTitle}"
${locatorRule}

For each event, emit: console.log('[STEP n]...') then the wdio action.
Recorded session:
${JSON.stringify(eventList, null, 2)}

Output ONLY the JavaScript source. Start with: import { remote } from 'webdriverio';`;
}

function groqGenerateWdio(apiKey, strategy, appName, platform, eventList, exePath) {
  return groqChat(apiKey, {
    system: WDIO_SYSTEM_PROMPT,
    user: buildWdioPrompt(strategy, appName, platform, eventList, exePath),
    maxTokens: 4000,
  });
}

app.post("/api/generate", async (req, res) => {
  const { appName, platform, framework = "appium" } = req.body;
  const name = appName || sessionInfo.appName || "MyApp";
  const plat = platform || sessionInfo.platform || "Windows";
  const exe = req.body.exePath || sessionInfo.exePath || "";

  // Prefer a key the user typed in the UI; otherwise fall back to the server's
  // .env key. The key never has to leave the server when .env is configured.
  const apiKey = (req.body.apiKey && req.body.apiKey.trim()) || process.env.GROQ_API_KEY;

  if (!apiKey) return res.status(400).json({ ok: false, message: "Groq API key is required (enter one in the UI or set GROQ_API_KEY in .env)" });
  if (events.length === 0) return res.status(400).json({ ok: false, message: "No recorded events to generate from" });

  const slim = events.map((e, i) => ({
    step: i + 1,
    action: e.action,
    value: e.value,
    x: e.x,
    y: e.y,
    delta: e.delta,
    screenId: e.screenId,        // NEW
    isPopup: e.isPopup,
    popupTitle: e.popupTitle,
    element: {
      name: e.element?.name,
      automationId: e.element?.automationId,
      className: e.element?.className,
      controlType: e.element?.controlType,
      windowTitle: e.element?.windowTitle,
      locatorFallback: e.element?.locatorFallback,
      locatorStrategy: e.element?.locatorStrategy,   // from Task 9
      locatorValue: e.element?.locatorValue,         // from Task 9
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
    } else if (framework === 'wdio') {
      const rawBase = name.replace(/[^A-Za-z0-9]/g, '') || 'MyApp';
      const base = rawBase.charAt(0).toUpperCase() + rawBase.slice(1);
      const [byId, byClass] = await Promise.all([
        groqGenerateWdio(apiKey, 'id', name, plat, slim, exe),
        groqGenerateWdio(apiKey, 'class', name, plat, slim, exe),
      ]);
      payload = {
        ok: true,
        framework: 'wdio',
        files: [
          { filename: `${base}TestById.js`, content: byId },
          { filename: `${base}TestByClass.js`, content: byClass },
        ],
      };
      const wdioOutDir = path.join(__dirname, '..', 'generated-wdio');
      const { savedPaths, saveError } = saveFiles(payload.files, wdioOutDir);
      payload.savedPaths = savedPaths;
      if (saveError) payload.saveError = saveError;
    } else {
      const rawBase = name.replace(/[^A-Za-z0-9]/g, "") || "MyApp";
      const base = rawBase.charAt(0).toUpperCase() + rawBase.slice(1);
      // Generate both files in parallel (spec: "produce the two files in parallel").
      // groqChat retries on 429, so concurrent calls are safe under the rate limit.
      const [byId, byClass] = await Promise.all([
        groqGenerate(apiKey, "id", name, plat, slim, exe),
        groqGenerate(apiKey, "class", name, plat, slim, exe),
      ]);
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
    } else if (framework !== 'wdio') {
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


