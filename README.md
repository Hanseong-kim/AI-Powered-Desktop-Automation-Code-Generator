# AI-Powered Desktop Automation Code Generator

Records user interactions with any Windows desktop application and generates
runnable test code (Appium Java TestNG or Playwright Python) via Groq AI.

## Architecture

```
React UI (3000) --HTTP--> Express (3002) --HTTP--> Python Agent (4444)
      ^                       |
      +---- SSE live feed ----+--> Groq API (llama-3.3-70b-versatile)
```

Three cooperating processes. The Python agent captures mouse/keyboard input and
Windows UI Automation element data; the Express bridge stores events and calls
the AI; the React dashboard provides live monitoring and triggers code generation.

## Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Python | 3.9+ | Must run agent from **Administrator** terminal |
| Node.js | 18+ | For Express bridge and React UI |
| Java | 11+ | For running generated Appium tests |
| Maven | 3.8+ | `mvn -version` to verify |
| WinAppDriver | 1.2.1 | Required for Appium Java test execution |
| Groq API key | — | Free at console.groq.com. Enter in the UI, **or** put `GROQ_API_KEY=gsk_...` in repo-root `.env` (server-side fallback) |

---

## 1. Install & Run the Code Generator

### Terminal 1 — Express Bridge (normal terminal)

```powershell
cd server
npm install
node server.js
# Listening on http://localhost:3002
```

### Terminal 2 — Python Agent (Administrator PowerShell)

```powershell
cd agent
pip install -r requirements.txt
python agent.py
# Must print: Administrator rights: YES
```

If it prints `NO`, close the terminal and reopen PowerShell using
"Run as Administrator".

### Terminal 3 — React UI (normal terminal)

```powershell
cd ui
npm install
npm run dev
# Open http://localhost:3000
```

---

## 2. Recording a Session

1. Select a **Target App** from the preset dropdown (Calculator / Notepad / Paint (UWP) / Registry Editor / Custom…).
   - Presets auto-fill **App Name** and **Exe Path**.
   - Choose **Custom…** to enter a path manually.
2. Select **Platform** (Windows / Android / iOS).
3. Select **Output Framework**: `Appium Java (TestNG)` or `Playwright Python`.
4. Click **Launch** — the target app opens and recording begins.
5. Interact with the app (clicks, typing, scrolling). **English input only** — IME/CJK keystrokes are silently ignored.
6. Click **Stop** when done.
7. Each captured event row can be **deleted individually** by hovering over it and clicking the `×` button.
8. Enter your **Groq API Key** and click **Generate Code**.
   If the server has `GROQ_API_KEY` set in `.env`, you can **leave the key field blank** — the
   server uses its own key (the key is never sent to the browser). A typed key takes precedence.
   Generated files are **saved to disk automatically** (toast confirms the path).

### Generated output

| Framework | Files |
|---|---|
| Appium Java | `{App}TestById.java`, `{App}TestByClass.java` |
| Playwright Python | `test_{app}_playwright.py` |

> **Note on generation (Appium Java):** The two Java files (ById + ByClass) are generated
> **in parallel** (`Promise.all`). Each Groq call retries automatically on HTTP 429 (honouring
> the `Retry-After` header, with backoff), so concurrent requests stay safe under the rate limit.
> Typical generation time is ~5–12 seconds.
>
> **Groq free-tier limits** are account-wide: ~30 req/min (RPM), 12k tokens/min (TPM),
> and **100k tokens/day (TPD)**. Each generation uses ~6k tokens (two files), so heavy
> re-generation can exhaust the daily 100k. If you hit TPD, generation is blocked for
> ~30 min to several hours — **don't re-generate to test; reuse the saved `.java` files and
> just re-run `mvn test`** (running tests uses zero Groq tokens).

---

## 3. Install & Run WinAppDriver

WinAppDriver is Microsoft's WebDriver server for Windows desktop apps.
Required only when **running** the generated Appium Java tests.

### Installation

1. Download **WinAppDriver 1.2.1** installer from GitHub releases:
   `https://github.com/microsoft/WinAppDriver/releases/tag/v1.2.1`
   File: `WindowsApplicationDriver.msi`

2. Run the installer (default path:
   `C:\Program Files (x86)\Windows Application Driver\`)

3. Enable **Developer Mode** in Windows:
   Settings → Privacy & security → For developers → Developer Mode → On

### Running WinAppDriver

Open a **new Administrator PowerShell** and run:

```powershell
& "C:\Program Files (x86)\Windows Application Driver\WinAppDriver.exe"
```

Expected output:

```
Windows Application Driver listening for requests at: http://127.0.0.1:4723/
```

Leave this terminal open during the test run.

> **Alternative — Appium Server.** Instead of `WinAppDriver.exe` directly, you can run
> Appium Server (with `appium-windows-driver`), which listens on `4723` and proxies to an
> internal WinAppDriver. The generated code connects to `http://127.0.0.1:4723` either way:
> ```powershell
> appium    # Appium v3.x with the 'windows' driver installed
> ```

---

## 4. Run Generated Appium Java Tests

### Setup

```powershell
# Java 11 (Temurin)
winget install EclipseAdoptium.Temurin.11.JDK

# Maven (if winget doesn't find it, download manually from archive.apache.org)
# Place in C:\tools\maven\apache-maven-3.9.6\
```

### Step-by-step

1. Generate test files using the UI — they are **automatically saved** to
   `test-runner\src\test\java\com\qaforge\tests\` (old files from a previous
   app are cleaned up first so Maven doesn't compile stale tests).
2. Start WinAppDriver as Administrator (port 4723).
3. Run:
   ```powershell
   cd test-runner
   mvn test
   ```

Test reports: `test-runner\target\surefire-reports\`

To run only one file:
```powershell
mvn test -Dtest=CalculatorTestById
```

---

## 5. Regression Testing

```powershell
# Requires only server running (no agent, no admin rights needed)
cd server; node server.js  # Terminal 1
python agent/mock_events.py  # Terminal 2
# Expected: 35/35 checks passed

# With Groq key (tests code generation + Playwright syntax):
$env:GROQ_API_KEY="gsk_..."; python agent/mock_events.py
# Expected: 48/48 checks passed
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `Agent unreachable` from Express | Python agent not running | Start `python agent.py` in Admin terminal |
| `Administrator rights: NO` | Agent not started as Administrator | Reopen PowerShell → Run as Administrator |
| `Connection refused` on port 4723 | WinAppDriver not running | Start `WinAppDriver.exe` as Administrator |
| `SessionNotCreatedException` | Wrong `.exe` path in capability | Verify path passed to Launch |
| `NoSuchElementException` | Locator mismatch or timing | Try the ByClass file instead of ById |
| `UnsupportedCommandException: only pen and touch...` | Generated code used `Actions`/`contextClick`/`doubleClick` | WinAppDriver has no W3C mouse Actions — regenerate (prompt now maps these to `element.click()`) |
| Generation fails `429 ... tokens per day (TPD)` | Groq free-tier daily 100k tokens exhausted | Wait ~30 min–hours (rolling window); **reuse saved `.java` + re-run `mvn test`** instead of regenerating |
| Generation fails `429 ... tokens per minute (TPM)` | Two requests momentarily exceeded 12k/min | Auto-retried by the server; if persistent, wait ~30s |
| `Cannot find symbol: WindowsDriver` | Wrong java-client version | Confirm `pom.xml` uses `java-client 8.6.0` |
| UIA properties empty | Not running as Admin | Restart agent terminal as Administrator |
| Toast "Server connection lost" loops | Express server crashed | Restart `node server.js`; toasts fire once per episode |
| `UnicodeEncodeError cp949` | Windows terminal encoding | Use `chcp 65001` before running Python scripts |
| winget can't find Apache.Maven | Package not in winget catalog | Download from `archive.apache.org/dist/maven` |

---

## Generated Code Requirements (Appium Java)

Both `.java` files conform to:

- Package `com.qaforge.tests`
- TestNG `@BeforeClass` / `@Test` / `@AfterClass`
- Page Object Model (Page + Test class in one `.java` file)
- `WindowsOptions` for W3C-compliant driver init (java-client 8.x compatible)
- `AppiumBy.accessibilityId()` / `By.className()` / `By.name()` locators
- `WebDriverWait(driver, Duration.ofSeconds(15))` for every interaction
- `System.out.println("[STEP n] ...")` before each action
- Final `Assert` that **reuses a recorded element's locator** (never an invented control)
- No `Thread.sleep()`, no TODOs

### WinAppDriver constraints baked into the prompt

- **No W3C mouse `Actions`.** WinAppDriver supports only pen/touch pointer types. Recorded
  right-click / double-click are mapped to a plain `element.click()`; `Actions`/`contextClick`/
  `doubleClick` are never emitted.
- **ByClass disambiguation.** Many controls share a ClassName (every calculator key is
  `Button`), so a bare `By.className(...)` matches the wrong (first) element. The ByClass
  strategy combines ClassName + Name as `By.xpath("//Button[@Name='9']")` whenever a Name exists.
- **No invented input field.** `type` events `sendKeys` to the recorded element — the prompt
  forbids fabricating a control like `By.className("Edit")` (absent in UWP apps).

### Known limitation — Paint / canvas drawing

Toolbar/button clicks in Paint work, but **freehand canvas drawing (mouse drag) cannot be
replayed** — it requires W3C mouse Actions, which WinAppDriver rejects. Use **Notepad** or
**Calculator** for end-to-end demos; treat Paint as click-only.
