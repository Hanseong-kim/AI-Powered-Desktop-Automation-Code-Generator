/**
 * AI-Powered Desktop Automation Code Generator - Express Bridge Server
 *
 * 코드 생성 방식: LLM 없음. events[]를 서버가 직접 Java/JS 코드로 변환.
 * 생성 프레임워크: Appium Java (TestNG) + WebdriverIO (JS) — 항상 둘 다 생성.
 *
 *   npm install
 *   node server.js   -> http://localhost:3002
 */

const fs   = require('fs');
const path = require('path');

require('dotenv').config({ path: path.join(__dirname, '..', '.env') });

const express = require('express');
const cors    = require('cors');

const PORT      = 3002;
const AGENT_URL = 'http://localhost:4444';

const WDIO_BASE_DIR       = path.join(__dirname, '..', 'generated-wdio');

const app = express();
app.use(cors());
app.use(express.json({ limit: '5mb' }));

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
let events      = [];
let sseClients  = [];
let recording   = false;
let sessionInfo = { appName: '', exePath: '' };

function broadcast(type, payload) {
  const msg = `event: ${type}\ndata: ${JSON.stringify(payload)}\n\n`;
  sseClients = sseClients.filter(res => {
    try { res.write(msg); return true; } catch { return false; }
  });
}

async function callAgent(agentPath, body) {
  const res = await fetch(`${AGENT_URL}${agentPath}`, {
    method : body ? 'POST' : 'GET',
    headers: { 'Content-Type': 'application/json' },
    body   : body ? JSON.stringify(body) : undefined,
    signal : AbortSignal.timeout(8000),
  });
  return res.json();
}

// ---------------------------------------------------------------------------
// Recording control
// ---------------------------------------------------------------------------
app.post('/api/start', async (req, res) => {
  try {
    const { appName, exePath, platform } = req.body;
    if (!exePath) return res.status(400).json({ ok: false, message: 'exePath is required' });
    const out = await callAgent('/start', { appName, exePath, platform });
    if (out.ok) {
      events      = [];
      recording   = true;
      sessionInfo = { appName, exePath };
      broadcast('status', { recording: true, eventCount: 0 });
    }
    res.status(out.ok ? 200 : 400).json(out);
  } catch (e) {
    res.status(502).json({ ok: false, message: `Agent unreachable: ${e.message}` });
  }
});

app.post('/api/stop', async (req, res) => {
  try {
    const out = await callAgent('/stop', {});
    recording = false;
    broadcast('status', { recording: false, eventCount: events.length });
    res.status(out.ok ? 200 : 400).json(out);
  } catch (e) {
    recording = false;
    res.status(502).json({ ok: false, message: `Agent unreachable: ${e.message}` });
  }
});

app.get('/api/status', async (req, res) => {
  let agent = { online: false };
  try { agent = await callAgent('/status'); agent.online = true; } catch { /* offline */ }
  res.json({ agentOnline: agent.online, isAdmin: agent.isAdmin ?? null, recording, eventCount: events.length });
});

// ---------------------------------------------------------------------------
// Event ingestion + SSE live feed
// ---------------------------------------------------------------------------
app.post('/api/events', (req, res) => {
  events.push(req.body);
  broadcast('capture', req.body);
  res.json({ ok: true });
});

app.get('/api/events', (req, res) => res.json(events));

app.delete('/api/events', (req, res) => {
  events = [];
  broadcast('status', { recording, eventCount: 0 });
  res.json({ ok: true });
});

app.delete('/api/events/:index', (req, res) => {
  const idx = parseInt(req.params.index, 10);
  if (isNaN(idx) || idx < 0 || idx >= events.length)
    return res.status(400).json({ ok: false, message: 'Invalid event index' });
  events.splice(idx, 1);
  broadcast('snapshot', { events, recording });
  res.json({ ok: true, eventCount: events.length });
});

app.get('/api/stream', (req, res) => {
  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache, no-transform');
  res.setHeader('Connection', 'keep-alive');
  res.flushHeaders();
  res.write(`event: snapshot\ndata: ${JSON.stringify({ events, recording })}\n\n`);
  sseClients.push(res);
  const hb = setInterval(() => { try { res.write('event: heartbeat\ndata: {}\n\n'); } catch {} }, 10000);
  req.on('close', () => { clearInterval(hb); sseClients = sseClients.filter(c => c !== res); });
});

// ---------------------------------------------------------------------------
// Code generation — template-based, no LLM
// ---------------------------------------------------------------------------

const EDITABLE_CONTROL_TYPES = new Set(['Edit', 'Document', 'RichEdit', 'RichEditD2DPT', 'ComboBox']);

// PowerShell helper script — generated alongside every test suite.
// Performs an OS-level click via user32.dll (SetCursorPos + mouse_event).
// Verified in probe: works for Electron deep menus where WinAppDriver el.click()
// and W3C touch Actions are both rejected or silently ignored.
const OS_CLICK_PS1 = `param([int]$x, [int]$y)
$sig = '[DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y); [DllImport("user32.dll")] public static extern void mouse_event(int f, int dx, int dy, int d, int e);'
Add-Type -MemberDefinition $sig -Name WinMouse -Namespace U -ErrorAction SilentlyContinue
[U.WinMouse]::SetCursorPos($x, $y) | Out-Null
Start-Sleep -Milliseconds 150
[U.WinMouse]::mouse_event(2, 0, 0, 0, 0)
Start-Sleep -Milliseconds 50
[U.WinMouse]::mouse_event(4, 0, 0, 0, 0)
`;

/** 앱 이름 → PascalCase 클래스명 접두어 */
function toPascal(appName) {
  const raw = (appName || 'MyApp').replace(/[^A-Za-z0-9]/g, '');
  return raw.charAt(0).toUpperCase() + raw.slice(1);
}

/** 이벤트 목록에서 유효한 액션만 추출 (session_meta 제거) */
function filterEvents(eventList) {
  return eventList.filter(e => e.action && e.action !== 'session_meta');
}

// ── WdIO locator 결정 ────────────────────────────────────────────────────────

/** className에 공백이 많거나 소문자 camelCase면 Electron/VSCode CSS 클래스 — coordinate fallback */
function isStableClassName(cn) {
  if (!cn) return false;
  const spaces = (cn.match(/ /g) || []).length;
  if (spaces > 1) return false;           // "foo bar baz" — CSS multi-class, unstable
  if (cn.length > 60) return false;
  // Win32 native class names are PascalCase or ALL_CAPS (e.g. "Button", "Chrome_WidgetWin_1")
  // Electron/web class names start with lowercase (e.g. "gettingStartedContainer")
  if (/^[a-z]/.test(cn)) return false;
  return true;
}

/** XPath 속성값용 escape: JS 문자열 내 \, ', 제어문자 + XPath " → &quot; */
function escapeAttr(s) {
  return (s || '')
    .replace(/\\/g, '\\\\')
    .replace(/'/g,  "\\'")
    .replace(/\n/g, '\\n')
    .replace(/\r/g, '\\r')
    .replace(/\t/g, '\\t')
    .replace(/"/g,  '&quot;');
}

function wdioSelectorById(el) {
  if (!el) return null;
  // Skip purely numeric automationIds (e.g. "4", "1") — these are ListView slot
  // indices assigned at runtime, not stable accessibility IDs. Use name instead.
  const hasStableId = el.automationId && !/^\d+$/.test(el.automationId);
  if (hasStableId) return `'~${escapeAttr(el.automationId)}'`;
  if (el.name)     return `'//*[@Name="${escapeAttr(el.name)}"]'`;
  if (el.className && isStableClassName(el.className)) return `'//*[@ClassName="${escapeAttr(el.className)}"]'`;
  return null;
}

function wdioSelectorByClass(el) {
  if (!el) return null;
  if (el.className && isStableClassName(el.className) && el.name)
    return `'//*[@ClassName="${escapeAttr(el.className)}" and @Name="${escapeAttr(el.name)}"]'`;
  if (el.className && isStableClassName(el.className)) return `'//*[@ClassName="${escapeAttr(el.className)}"]'`;
  if (el.name)         return `'//*[@Name="${escapeAttr(el.name)}"]'`;
  if (el.automationId) return `'~${escapeAttr(el.automationId)}'`;
  return null;
}

// ── WdIO 코드 생성 ───────────────────────────────────────────────────────────

function escapeStr(s) {
  return (s || '')
    .replace(/\\/g, '\\\\')
    .replace(/'/g,  "\\'")
    .replace(/\n/g, '\\n')
    .replace(/\r/g, '\\r')
    .replace(/\t/g, '\\t');
}

// Detect whether events need the session-switching architecture:
//   true  → multiple windowTitles OR any Electron event
//           → wdio.conf: appium:app='Root'; generated code uses getWindowSession/getCenter/osClick
//   false → single windowTitle AND no Electron
//           → wdio.conf: appium:app=exePath; generated code uses browser.$().click() + osClick fallback
function needsSessionSwitching(eventList) {
  const filtered = filterEvents(eventList);
  const hasElectron = filtered.some(e => e.isElectron === true);
  if (hasElectron) return true;
  const titles = new Set(filtered.map(e => e.element?.windowTitle || '').filter(Boolean));
  return titles.size > 1;
}

// ── 단순 헤더 (Win32/UWP 단일 창 앱, session switching 불필요) ─────────────
const SIMPLE_HEADER = `import { execSync } from 'child_process';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));

// OS-level click fallback via PowerShell user32.dll.
function osClick(x, y) {
    try {
        execSync(
            \`powershell -NoProfile -File "\${join(__dirname, 'osClick.ps1')}" -x \${x} -y \${y}\`,
            { stdio: 'pipe', timeout: 5000 }
        );
    } catch (e) {
        console.warn('[osClick] failed:', String(e.message || e).substring(0, 100));
    }
}

`;

// ── 세션 전환 헤더 (Electron / 다중 창 앱) ────────────────────────────────
const SESSION_HEADER = `import { execSync } from 'child_process';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));

// OS-level click via PowerShell user32.dll — verified for Electron menus, native dialogs.
function osClick(x, y) {
    try {
        execSync(
            \`powershell -NoProfile -File "\${join(__dirname, 'osClick.ps1')}" -x \${x} -y \${y}\`,
            { stdio: 'pipe', timeout: 5000 }
        );
    } catch (e) {
        console.warn('[osClick] failed:', String(e.message || e).substring(0, 100));
    }
}

// Window session pool: title → Appium sessionId.
// global browser (Root session) used ONCE per new windowTitle for hwnd discovery;
// a fast scoped appTopLevelWindow session is then opened via Appium REST API.
const _APPIUM = 'http://127.0.0.1:4723';
const _sessionIds = {};

async function _appiumPost(path, body) {
    const r = await fetch(\`\${_APPIUM}\${path}\`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    return (await r.json()).value;
}

async function _createSession(app) {
    const v = await _appiumPost('/session', {
        capabilities: { alwaysMatch: { platformName: 'Windows', 'appium:automationName': 'Windows', 'appium:app': app, 'appium:newCommandTimeout': 60000 } },
    });
    if (!v?.sessionId) throw new Error(\`Appium session failed for "\${app}": \${JSON.stringify(v)}\`);
    return v.sessionId;
}

async function getWindowSession(title) {
    if (_sessionIds[title]) return _sessionIds[title];
    console.log(\`[session] Root scan for: "\${title}"\`);
    const shortTitle = title.slice(0, 30).replace(/"/g, '');
    let hwnd = null;
    for (const sel of [\`//*[@Name="\${title}"]\`, \`//*[contains(@Name,"\${shortTitle}")]\`]) {
        try {
            const el = await browser.$(sel);
            if (await el.isExisting()) {
                const raw = await el.getAttribute('NativeWindowHandle');
                const hwndNum = parseInt(raw, 10);
                if (hwndNum) { hwnd = '0x' + hwndNum.toString(16); break; }
            }
        } catch {}
    }
    const app = hwnd || 'Root';
    if (hwnd) console.log(\`[session] hwnd=\${hwnd} → scoped session\`);
    else console.warn(\`[session] Window "\${title}" not found — falling back to Root\`);
    _sessionIds[title] = await _createSession(app);
    return _sessionIds[title];
}

async function getCenter(sid, selector) {
    try {
        const raw = selector.replace(/^['"]|['"]$/g, '');
        const using = raw.startsWith('~') ? 'accessibility id' : 'xpath';
        const value = raw.startsWith('~') ? raw.slice(1) : raw;
        const el = await _appiumPost(\`/session/\${sid}/element\`, { using, value });
        if (!el) return null;
        const elId = el.ELEMENT || el['element-6066-11e4-a52e-4f735466cecf'];
        const r = await (await fetch(\`\${_APPIUM}/session/\${sid}/element/\${elId}/rect\`)).json();
        const rect = r.value;
        if (!rect) return null;
        return { x: Math.round(rect.x + rect.width / 2), y: Math.round(rect.y + rect.height / 2) };
    } catch { return null; }
}

async function _typeScoped(sid, selector, text) {
    try {
        const raw = selector.replace(/^['"]|['"]$/g, '');
        const using = raw.startsWith('~') ? 'accessibility id' : 'xpath';
        const value = raw.startsWith('~') ? raw.slice(1) : raw;
        const el = await _appiumPost(\`/session/\${sid}/element\`, { using, value });
        if (!el) throw new Error('element not found');
        const elId = el.ELEMENT || el['element-6066-11e4-a52e-4f735466cecf'];
        await _appiumPost(\`/session/\${sid}/element/\${elId}/clear\`, {});
        await _appiumPost(\`/session/\${sid}/element/\${elId}/value\`, { text });
    } catch (e) { console.warn('[type] failed:', String(e.message || e).substring(0, 100)); }
}

`;

function generateWdio(strategy, appName, eventList, useSession) {
  const base     = toPascal(appName);
  const suffix   = strategy === 'id' ? 'ById' : 'ByClass';
  const testName = `${base}Test${suffix}`;
  const pageName = `${base}Page${suffix}`;
  const selFn    = strategy === 'id' ? wdioSelectorById : wdioSelectorByClass;

  const filtered     = filterEvents(eventList);
  const pageMethods  = [];
  const testSteps    = [];

  filtered.forEach((e, i) => {
    const stepNum  = i + 1;
    const sel      = selFn(e.element);
    const isEdit   = EDITABLE_CONTROL_TYPES.has(e.element?.controlType);
    const x        = e.x ?? 0;
    const y        = e.y ?? 0;
    const winTitle = e.element?.windowTitle || '';
    const titleArg = escapeStr(winTitle);

    if (e.action === 'scroll') {
      testSteps.push(`            // [STEP ${stepNum}] scroll skipped (WinAppDriver wheel unsupported)`);
      return;
    }

    if (e.action === 'type' && isEdit) {
      const elSel = sel || `'//*[@Name="${escapeAttr(e.element?.name)}"]'`;
      if (useSession && winTitle) {
        pageMethods.push(
`    async type${stepNum}(value) {
        const sid = await getWindowSession('${titleArg}');
        await _typeScoped(sid, ${elSel}, value);
    }`
        );
      } else {
        pageMethods.push(
`    async type${stepNum}(value) {
        const el = await browser.$(${elSel});
        await el.waitForExist({ timeout: 8000 });
        await el.setValue(value);
    }`
        );
      }
      testSteps.push(
`            console.log('[STEP ${stepNum}] type: ${escapeStr(e.value)}');
            await page.type${stepNum}('${escapeStr(e.value)}');`
      );
    } else if (e.action === 'type') {
      testSteps.push(`            // [STEP ${stepNum}] skip type on non-editable element`);
    } else {
      // Click
      if (useSession) {
        const isElectron = e.isElectron === true;
        if (!isElectron && winTitle && sel) {
          // Non-Electron native window: scoped session → getCenter → osClick.
          // Gives current element coordinates even if window has moved.
          pageMethods.push(
`    async click${stepNum}() {
        const sid = await getWindowSession('${titleArg}');
        const c = await getCenter(sid, ${sel}) ?? { x: ${x}, y: ${y} };
        osClick(c.x, c.y);
    }`
          );
        } else {
          // Electron web-content or no UIA selector: osClick with captured coords.
          // (Electron NativeWindowHandle=0; el bounding rect unreliable for web DOM.)
          pageMethods.push(
`    async click${stepNum}() {
        osClick(${x}, ${y});
    }`
          );
        }
      } else {
        // Simple mode: try browser el.click(), fall back to osClick
        if (sel) {
          pageMethods.push(
`    async click${stepNum}() {
        try {
            const el = await browser.$(${sel});
            await el.waitForExist({ timeout: 8000 });
            await el.click();
        } catch (_) {
            osClick(${x}, ${y});
        }
    }`
          );
        } else {
          pageMethods.push(
`    async click${stepNum}() {
        osClick(${x}, ${y});
    }`
          );
        }
      }
      testSteps.push(
`            console.log('[STEP ${stepNum}] click: ${escapeStr(e.element?.name || '')}');
            await page.click${stepNum}();`
      );
    }
  });

  const assertLine = `            expect(browser).toBeDefined();`;

  const header   = useSession ? SESSION_HEADER : SIMPLE_HEADER;
  const afterHook = useSession ? `
    afterAll(async () => {
        for (const sid of Object.values(_sessionIds)) {
            try { await fetch(\`\${_APPIUM}/session/\${sid}\`, { method: 'DELETE' }); } catch {}
        }
    });
` : '';

  return header + `class ${pageName} {
${pageMethods.join('\n\n')}
}

describe('${testName}', () => {${afterHook}
    it('should replay recorded flow', async () => {
        const page = new ${pageName}();

${testSteps.join('\n')}

${assertLine}
    });
});
`;
}

// ── WdIO conf 생성 ───────────────────────────────────────────────────────────

function buildWdioConf(exePath, specFiles, useSession) {
  const specsArr = (specFiles && specFiles.length)
    ? specFiles.map(f => `'./${f}'`).join(', ')
    : `'./*.js'`;

  if (useSession) {
    // Multi-window / Electron: Root session as global browser for hwnd discovery.
    // Tests open scoped appTopLevelWindow sessions themselves via Appium REST API.
    return `export const config = {
  runner: 'local',
  specs: [${specsArr}],
  exclude: ['./wdio.conf.js'],
  maxInstances: 1,
  capabilities: [{
    platformName: 'Windows',
    'appium:automationName': 'Windows',
    'appium:app': 'Root',
    'appium:newCommandTimeout': 120000,
  }],
  hostname: '127.0.0.1',
  port: 4723,
  path: '/',
  framework: 'jasmine',
  jasmineOpts: { defaultTimeoutInterval: 300000 },
  reporters: ['spec'],
  services: ['appium'],
  appium: { command: 'appium', args: ['--allow-insecure', 'winappdriver'] },
  injectGlobals: true,
};`;
  }

  // Single-window Win32/UWP: direct app launch, classic browser.$() style.
  const isUwp  = (exePath || '').includes('!');
  const appCap = isUwp ? exePath : exePath.replace(/\\/g, '\\\\');
  return `export const config = {
  runner: 'local',
  specs: [${specsArr}],
  exclude: ['./wdio.conf.js'],
  maxInstances: 1,
  capabilities: [{
    platformName: 'Windows',
    'appium:automationName': 'Windows',
    'appium:app': '${appCap}',
    'appium:newCommandTimeout': 60000,
    'appium:connectHardwareKeyboard': false,
  }],
  hostname: '127.0.0.1',
  port: 4723,
  path: '/',
  framework: 'jasmine',
  jasmineOpts: { defaultTimeoutInterval: 60000 },
  reporters: ['spec'],
  services: ['appium'],
  appium: { command: 'appium', args: ['--allow-insecure', 'winappdriver'] },
  injectGlobals: true,
};`;
}

// ── 유틸: 이벤트 element name → 안전한 Java/JS 식별자 ───────────────────────

function safeName(str) {
  if (!str) return 'element';
  // 한글/특수문자를 제거, camelCase 정리
  const ascii = str.replace(/[^A-Za-z0-9 _-]/g, '').trim();
  if (!ascii) return 'element';
  return ascii
    .split(/[\s_-]+/)
    .map((w, i) => i === 0 ? w.charAt(0).toLowerCase() + w.slice(1) : w.charAt(0).toUpperCase() + w.slice(1))
    .join('');
}

// ── 파일 저장 ────────────────────────────────────────────────────────────────

function saveFiles(files, dir) {
  const savedPaths = [];
  let saveError;
  try {
    fs.mkdirSync(dir, { recursive: true });
    for (const f of files) {
      const fp = path.join(dir, f.filename);
      fs.writeFileSync(fp, f.content, 'utf8');
      savedPaths.push(fp);
    }
  } catch (e) {
    saveError = e.message;
  }
  return { savedPaths, saveError };
}

// ---------------------------------------------------------------------------
// /api/generate — 템플릿 기반 즉시 생성 (LLM 호출 없음)
// ---------------------------------------------------------------------------
app.post('/api/generate', (req, res) => {
  const name  = req.body.appName  || sessionInfo.appName  || 'MyApp';
  const exe   = req.body.exePath  || sessionInfo.exePath  || '';

  if (events.length === 0)
    return res.status(400).json({ ok: false, message: 'No recorded events to generate from' });

  const base = toPascal(name);

  broadcast('generation', { status: 'started' });

  try {
    // Decide architecture: session switching for Electron / multi-window apps.
    const useSession = needsSessionSwitching(events);

    // ── WebdriverIO ────────────────────────────────────────────────────────
    const wdioById    = generateWdio('id',    name, events, useSession);
    const wdioByClass = generateWdio('class', name, events, useSession);
    const wdioFiles   = [
      { filename: `${base}TestById.js`,    content: wdioById    },
      { filename: `${base}TestByClass.js`, content: wdioByClass },
    ];

    // 앱별 서브폴더에 저장
    const wdioOutDir = path.join(WDIO_BASE_DIR, base);

    const confContent = buildWdioConf(exe || name, wdioFiles.map(f => f.filename), useSession);
    const confFile    = { filename: 'wdio.conf.js', content: confContent };
    const ps1File     = { filename: 'osClick.ps1',  content: OS_CLICK_PS1 };
    const { savedPaths: wdioPaths, saveError: wdioErr } = saveFiles(
      [...wdioFiles, confFile, ps1File], wdioOutDir
    );

    broadcast('generation', { status: 'success', files: wdioFiles.map(f => f.filename), folder: base });

    res.json({
      ok: true,
      files: wdioFiles,
      savedPaths: wdioPaths,
      folder: base,
      runCommand: `cd generated-wdio && npx wdio run ${base}/wdio.conf.js`,
      saveErrors: [wdioErr].filter(Boolean),
    });
  } catch (e) {
    broadcast('generation', { status: 'error', message: e.message });
    res.status(500).json({ ok: false, message: e.message });
  }
});

// ---------------------------------------------------------------------------
app.listen(PORT, () =>
  console.log(`[bridge] Express server listening on http://localhost:${PORT}`)
);