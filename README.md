# AI-Powered Desktop Automation Code Generator

Records user interactions with any Windows desktop application and generates
runnable Appium Java (TestNG) test code via Groq AI.

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
| Java | 11+ | For running generated tests |
| Maven | 3.8+ | `mvn -version` to verify |
| WinAppDriver | 1.2.1 | See section below |
| Groq API key | — | Free at console.groq.com |

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

### Smoke test (no UI)

```powershell
# Start a recording session
curl -X POST http://localhost:3002/api/start `
  -H "Content-Type: application/json" `
  -d '{"appName":"Calculator","exePath":"C:\\Windows\\System32\\calc.exe","platform":"Windows"}'

# Stop
curl -X POST http://localhost:3002/api/stop

# Generate code (replace YOUR_KEY)
curl -X POST http://localhost:3002/api/generate `
  -H "Content-Type: application/json" `
  -d '{"apiKey":"YOUR_KEY","appName":"Calculator","platform":"Windows"}'
```

---

## 2. Install WinAppDriver

WinAppDriver is Microsoft's WebDriver server for Windows desktop apps.
It must be running whenever you execute generated tests.

### Installation

1. Download **WinAppDriver 1.2.1** installer:
   `https://github.com/microsoft/WinAppDriver/releases/tag/v1.2.1`
   → `WindowsApplicationDriver.msi`

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

Leave this terminal open for the duration of your test run.

---

## 3. Run Generated Tests

### Step 1 — Generate test files

Use the React UI or call `/api/generate` directly. The server returns two files:

- `{AppName}TestById.java` — locates elements by `AutomationId`
- `{AppName}TestByClass.java` — locates elements by `ClassName`

### Step 2 — Drop files into the test-runner

```
test-runner/
└── src/test/java/com/qaforge/tests/
    ├── CalculatorTestById.java      <- paste here
    └── CalculatorTestByClass.java   <- paste here
```

### Step 3 — Start WinAppDriver (Administrator terminal)

See section 2 above.

### Step 4 — Run tests

```powershell
cd test-runner
mvn test
```

Test reports are written to `test-runner/target/surefire-reports/`.

To run only one file:

```powershell
mvn test -Dtest=CalculatorTestById
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `Agent unreachable` from Express | Python agent not running | Start `python agent.py` in an Admin terminal |
| `Administrator rights: NO` | Agent not started as Administrator | Reopen PowerShell → Run as Administrator |
| `Connection refused` on port 4723 | WinAppDriver not running | Start `WinAppDriver.exe` as Administrator |
| `SessionNotCreatedException: app not found` | Wrong `.exe` path in capability | Verify the path passed to `/api/start` |
| `NoSuchElementException` | Locator mismatch or timing | Try the `ByClass` file; check WinAppDriver console log |
| `Cannot find symbol: WindowsDriver` | Wrong `java-client` version | Confirm `pom.xml` uses `java-client 8.6.0` |
| UIA properties (`AutomationId`, `Name`) are empty | Not running as Admin | Restart agent terminal as Administrator |
| comtypes initialisation delay on first run | Normal — generates COM wrapper | Wait ~5 seconds for first startup |

---

## Generated Code Requirements

Both `.java` files produced by `/api/generate` conform to:

- Package `com.qaforge.tests`
- TestNG `@BeforeClass` / `@Test` / `@AfterClass`
- Page Object Model (Page class + Test class in one `.java` file)
- `WebDriverWait(driver, Duration.ofSeconds(15))` for every interaction
- `System.out.println("[STEP n] ...")` before each action
- Final `Assert` verifying session state
- No `Thread.sleep()`, no TODOs
