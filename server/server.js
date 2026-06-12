/**
 * AI-Powered Desktop Automation Code Generator - Express Bridge Server
 * =====================================================================
 *  - Proxies start/stop commands from the React UI to the Python agent (4444)
 *  - Receives captured events from the agent and broadcasts them over SSE
 *  - Calls the Groq API (llama-3.3-70b-versatile) to generate two TestNG
 *    Page-Object Java files in parallel (ById + ByClass)
 *
 *    npm install
 *    node server.js          -> http://localhost:3002
 */

const express = require("express");
const cors = require("cors");

const PORT = 3002;
const AGENT_URL = "http://localhost:4444";
const GROQ_URL = "https://api.groq.com/openai/v1/chat/completions";
const GROQ_MODEL = "llama-3.3-70b-versatile";

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
  const hb = setInterval(() => { try { res.write(":hb\n\n"); } catch {} }, 15000);
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
- Every interaction is preceded by an explicit wait: WebDriverWait with Duration.ofSeconds(15) and ExpectedConditions (elementToBeClickable for clicks, visibilityOf/presence for typing). NEVER use Thread.sleep().
- Full imports at the top (java.time.Duration, java.net.URL, org.openqa.selenium.*, org.openqa.selenium.support.ui.*, org.testng.annotations.*, org.testng.Assert, io.appium.java_client.* as needed).
- Before each action in the test method, print a step log: System.out.println("[STEP n] ...").
- End the test with at least one Assert that verifies the final state (e.g. an element is displayed or the session is active).
- Method and field names must be descriptive, derived from the element names.
- Full imports at the top (java.time.Duration, java.net.MalformedURLException, java.net.URL, org.openqa.selenium.*, org.openqa.selenium.support.ui.*, org.testng.annotations.*, org.testng.Assert, io.appium.java_client.AppiumBy, io.appium.java_client.windows.WindowsDriver, io.appium.java_client.windows.options.WindowsOptions).
- Before each action in the test method, print a step log: System.out.println("[STEP n] ...").
- End the test with at least one Assert that verifies the final state (e.g. an element is displayed or the session is active).
- Method and field names must be descriptive, derived from the element names.
- The code must compile with Appium java-client 8.6.0 and TestNG 7.x.

CRITICAL - driver initialisation (W3C protocol, required for java-client 8.x):
  WindowsOptions options = new WindowsOptions();
  options.setApp("<exePath>");
  driver = new WindowsDriver(new URL("http://127.0.0.1:4723"), options);
  NEVER use DesiredCapabilities - it is incompatible with java-client 8.x.

CRITICAL - locators (use AppiumBy, not MobileBy which is deprecated):
  AppiumBy.accessibilityId("automationId")   // AutomationId-based strategy
  By.className("className")                  // ClassName-based strategy
  By.name("elementName")                     // fallback when id/class empty`;

function buildUserPrompt(strategy, appName, platform, eventList) {
  const p = PLATFORM_MAP[platform] || PLATFORM_MAP.Windows;
  const className = `${appName.replace(/[^A-Za-z0-9]/g, "") || "MyApp"}Test${strategy === "id" ? "ById" : "ByClass"}`;
  const locatorRule = strategy === "id"
    ? `Locator strategy: use the element's AutomationId as the primary locator (${p.idHint}). If an event has an empty automationId, fall back to By.name(element name), then By.className.`
    : `Locator strategy: use the element's ClassName as the primary locator (By.className). When several elements share a class, disambiguate with By.name or an XPath using the recorded Name attribute.`;

  return `Target application: "${appName}" on platform ${platform} (${p.note}).
Driver class: ${p.driver} (import ${p.driverImport}).
Public test class name: ${className}. Page class name: ${className.replace("Test", "Page")}.

${locatorRule}

Recorded user session (in order). Convert EVERY event into a page-object action + a test step:
- click / doubleClick / rightClick -> click(), Actions.doubleClick(), Actions.contextClick()
- type -> sendKeys(value) into the recorded input field (clear() first)
- scroll -> a scroll action on the window (e.g. Actions or executeScript), keep it simple

${JSON.stringify(eventList, null, 2)}

Output ONLY the Java source code of the single .java file. Start directly with "package com.qaforge.tests;".`;
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
      401: 'Invalid Groq API key ??check your key at console.groq.com',
      429: 'Groq rate limit exceeded ??wait a moment and retry',
    };
    throw new Error(hints[res.status] ?? `Groq API ${res.status}: ${text.slice(0, 300)}`);
  }
  const data = await res.json();
  return stripFences(data.choices?.[0]?.message?.content || "");
}

app.post("/api/generate", async (req, res) => {
  const { apiKey, appName, platform } = req.body;
  const name = appName || sessionInfo.appName || "MyApp";
  const plat = platform || sessionInfo.platform || "Windows";

  if (!apiKey) return res.status(400).json({ ok: false, message: "Groq API key is required" });
  if (events.length === 0) return res.status(400).json({ ok: false, message: "No recorded events to generate from" });

  // slim event list for the prompt
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

  broadcast("generation", { status: "started" });
  try {
    const [byId, byClass] = await Promise.all([
      groqGenerate(apiKey, "id", name, plat, slim),
      groqGenerate(apiKey, "class", name, plat, slim),
    ]);
    const base = name.replace(/[^A-Za-z0-9]/g, "") || "MyApp";
    const payload = {
      ok: true,
      files: [
        { filename: `${base}TestById.java`, content: byId },
        { filename: `${base}TestByClass.java`, content: byClass },
      ],
    };
    broadcast("generation", { status: "success", files: payload.files.map(f => f.filename) });
    res.json(payload);
  } catch (e) {
    broadcast("generation", { status: "error", message: e.message });
    res.status(500).json({ ok: false, message: e.message });
  }
});

app.listen(PORT, () =>
  console.log(`[bridge] Express server listening on http://localhost:${PORT}`)
);

