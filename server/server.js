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
const EVENTS_BACKUP       = path.join(__dirname, '..', 'recorded-events');

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
let sessionBackupFile = null;

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
      fs.mkdirSync(EVENTS_BACKUP, { recursive: true });
      sessionBackupFile = path.join(
        EVENTS_BACKUP,
        `${appName}_${new Date().toISOString().replace(/[:.]/g, '-')}.json`
      );
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
  if (!sessionBackupFile) {
    fs.mkdirSync(EVENTS_BACKUP, { recursive: true });
    sessionBackupFile = path.join(
      EVENTS_BACKUP,
      `recovered_${new Date().toISOString().replace(/[:.]/g, '-')}.json`
    );
  }
  try {
    fs.writeFileSync(sessionBackupFile, JSON.stringify(events, null, 2));
  } catch (e) {
    console.warn('[backup] write failed:', e.message);
  }
  res.json({ ok: true });
});

app.get('/api/events', (req, res) => res.json(events));

app.get('/api/events/backups', (req, res) => {
  try {
    fs.mkdirSync(EVENTS_BACKUP, { recursive: true });
    const files = fs.readdirSync(EVENTS_BACKUP).filter(f => f.endsWith('.json'));
    res.json({ ok: true, files });
  } catch (e) {
    res.status(500).json({ ok: false, message: e.message });
  }
});

app.post('/api/events/restore', (req, res) => {
  const { file } = req.body;
  if (!file) return res.status(400).json({ ok: false, message: 'file is required' });
  const filePath = path.join(EVENTS_BACKUP, file);
  if (!filePath.startsWith(EVENTS_BACKUP) || !fs.existsSync(filePath)) {
    return res.status(404).json({ ok: false, message: 'backup file not found' });
  }
  try {
    events = JSON.parse(fs.readFileSync(filePath, 'utf8'));
    sessionBackupFile = filePath;
    broadcast('snapshot', { events });
    res.json({ ok: true, eventCount: events.length });
  } catch (e) {
    res.status(500).json({ ok: false, message: e.message });
  }
});

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
const OS_CLICK_PS1 = `param([int]$x, [int]$y, [string]$button = 'left', [int]$clicks = 1)
$sig = '[DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y); [DllImport("user32.dll")] public static extern void mouse_event(int f, int dx, int dy, int d, int e);'
Add-Type -MemberDefinition $sig -Name WinMouse -Namespace U -ErrorAction SilentlyContinue
[U.WinMouse]::SetCursorPos($x, $y) | Out-Null
Start-Sleep -Milliseconds 150
$down = if ($button -eq 'right') { 8 } else { 2 }
$up   = if ($button -eq 'right') { 16 } else { 4 }
for ($i = 0; $i -lt $clicks; $i++) {
  [U.WinMouse]::mouse_event($down, 0, 0, 0, 0)
  Start-Sleep -Milliseconds 50
  [U.WinMouse]::mouse_event($up, 0, 0, 0, 0)
  if ($i -lt ($clicks - 1)) { Start-Sleep -Milliseconds 80 }
}
`;

// PowerShell helper script — mouse wheel via user32.dll MOUSEEVENTF_WHEEL (0x0800).
// WinAppDriver/Appium's W3C Actions API has no wheel-scroll primitive, so scroll
// events replay the same OS-level injection approach as OS_CLICK_PS1.
const OS_SCROLL_PS1 = `param([int]$x, [int]$y, [int]$delta)
$sig = '[DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y); [DllImport("user32.dll")] public static extern void mouse_event(int f, int dx, int dy, int d, int e);'
Add-Type -MemberDefinition $sig -Name WinMouse -Namespace U -ErrorAction SilentlyContinue
[U.WinMouse]::SetCursorPos($x, $y) | Out-Null
Start-Sleep -Milliseconds 100
[U.WinMouse]::mouse_event(0x0800, 0, 0, $delta, 0)
`;

// PowerShell helper — read the current rect of a window whose title contains
// $titleLike, enumerating ALL top-level windows (not just one MainWindow per
// process) so non-main dialogs (e.g. native "Open Folder") are found too.
// Prints "left top width height". Used to re-base window-relative coordinates
// at replay time (survives window repositioning).
// NOTE: no SetProcessDPIAware call here — agent.py capture and osClick.ps1 are
// both DPI-unaware, and adding awareness here would skew coordinates by the
// DPI scale factor (verified: unaware rect matches agent-captured winLeft).
const OS_WINRECT_PS1 = `param([string]$titleLike, [string]$hwnd, [switch]$listOnly)
Add-Type @"
using System;
using System.Text;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public class WinEnum {
  public delegate bool EnumProc(IntPtr hWnd, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc proc, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern int GetWindowTextLength(IntPtr hWnd);
  [DllImport("user32.dll", CharSet = CharSet.Auto)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder sb, int max);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT r);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
  public static List<IntPtr> Find(string titleLike) {
    var found = new List<IntPtr>();
    EnumWindows((hWnd, lParam) => {
      if (!IsWindowVisible(hWnd) || IsIconic(hWnd)) return true;
      int len = GetWindowTextLength(hWnd);
      if (len == 0) return true;
      var sb = new StringBuilder(len + 1);
      GetWindowText(hWnd, sb, sb.Capacity);
      if (sb.ToString().Contains(titleLike)) found.Add(hWnd);
      return true;
    }, IntPtr.Zero);
    return found;
  }
}
"@ -ErrorAction SilentlyContinue
# -hwnd targets one specific window directly, bypassing title matching entirely.
# Title matching alone is ambiguous whenever more than one window shares a
# substring (e.g. every VS Code window's title ends in "Visual Studio Code") —
# callers that already know their window's handle MUST use -hwnd so replay
# never drifts onto an unrelated window (see launchApp's hwnd tracking).
if ($hwnd) {
  $h = [IntPtr]([int64]$hwnd)
  $r = New-Object WinEnum+RECT
  if ([WinEnum]::GetWindowRect($h, [ref]$r)) {
    Write-Output ("{0} {1} {2} {3}" -f $r.Left, $r.Top, ($r.Right - $r.Left), ($r.Bottom - $r.Top))
  }
  exit
}
$matches = [WinEnum]::Find($titleLike)
if ($listOnly) {
  foreach ($h in $matches) { Write-Output ([int64]$h) }
  exit
}
if ($matches.Count -gt 0) {
  $fg = [WinEnum]::GetForegroundWindow()
  $hWnd = $matches[0]
  foreach ($h in $matches) { if ($h -eq $fg) { $hWnd = $h; break } }
  $r = New-Object WinEnum+RECT
  [WinEnum]::GetWindowRect($hWnd, [ref]$r) | Out-Null
  Write-Output ("{0} {1} {2} {3}" -f $r.Left, $r.Top, ($r.Right - $r.Left), ($r.Bottom - $r.Top))
}
`;

// PowerShell helper — move+resize a window whose title contains $titleLike to
// the recorded geometry. Restores from maximized first (MoveWindow is a no-op
// on a maximized window), then moves. Used to normalize a freshly-launched
// window (which may open maximized, at a different size than recording) back
// to the exact rect the events were captured against — rel-coordinate replay
// is only valid if the window size matches the recording, not just position.
// NOTE: no SetProcessDPIAware — same DPI-unaware coordinate space as
// osClick.ps1/osWindowRect.ps1 (see OS_WINRECT_PS1 comment above). Position
// (left/top) passed to MoveWindow lands unscaled, matching GetWindowRect —
// but WIDTH/HEIGHT can be silently scaled by the target window (verified:
// the FIRST MoveWindow after ShowWindow(RESTORE) scaled 1200x700 up to an
// actual 1500x875 — a 1.25x factor — but a second MoveWindow issued right
// after did NOT rescale, so the effect isn't a constant, query-able DPI
// factor; it seems tied to the restore-then-resize transition). GetDpiForWindow
// and GetDpiForMonitor both reported 96 (no scaling) despite the real
// effect, so it can't be queried — instead we CALIBRATE iteratively: move,
// measure the actual rect, and if it doesn't match, correct the requested
// size by the measured ratio and retry (converges in 1 try when there's no
// scaling, 2 when there is).
const OS_MOVEWINDOW_PS1 = `param([string]$titleLike, [string]$hwnd, [int]$left, [int]$top, [int]$width, [int]$height)
Add-Type @"
using System;
using System.Text;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public class WinMove {
  public delegate bool EnumProc(IntPtr hWnd, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc proc, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern int GetWindowTextLength(IntPtr hWnd);
  [DllImport("user32.dll", CharSet = CharSet.Auto)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder sb, int max);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
  [DllImport("user32.dll")] public static extern bool MoveWindow(IntPtr hWnd, int X, int Y, int nWidth, int nHeight, bool bRepaint);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT r);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
  public static List<IntPtr> Find(string titleLike) {
    var found = new List<IntPtr>();
    EnumWindows((hWnd, lParam) => {
      if (!IsWindowVisible(hWnd) || IsIconic(hWnd)) return true;
      int len = GetWindowTextLength(hWnd);
      if (len == 0) return true;
      var sb = new StringBuilder(len + 1);
      GetWindowText(hWnd, sb, sb.Capacity);
      if (sb.ToString().Contains(titleLike)) found.Add(hWnd);
      return true;
    }, IntPtr.Zero);
    return found;
  }
}
"@ -ErrorAction SilentlyContinue
# -hwnd bypasses title matching — see OS_WINRECT_PS1 for why ambiguous title
# substrings (e.g. any two VS Code windows) are unsafe to move/resize by.
if ($hwnd) {
  $hWnd = [IntPtr]([int64]$hwnd)
} else {
  $matches = [WinMove]::Find($titleLike)
  $hWnd = [IntPtr]::Zero
  if ($matches.Count -gt 0) {
    $fg = [WinMove]::GetForegroundWindow()
    $hWnd = $matches[0]
    foreach ($h in $matches) { if ($h -eq $fg) { $hWnd = $h; break } }
  }
}
if ($hWnd -ne [IntPtr]::Zero) {
  [WinMove]::ShowWindow($hWnd, 9) | Out-Null
  Start-Sleep -Milliseconds 300
  $candW = $width
  $candH = $height
  for ($i = 0; $i -lt 3; $i++) {
    [WinMove]::MoveWindow($hWnd, $left, $top, $candW, $candH, $true) | Out-Null
    Start-Sleep -Milliseconds 300
    $r = New-Object WinMove+RECT
    [WinMove]::GetWindowRect($hWnd, [ref]$r) | Out-Null
    $actualW = $r.Right - $r.Left
    $actualH = $r.Bottom - $r.Top
    if ([math]::Abs($actualW - $width) -le 2 -and [math]::Abs($actualH - $height) -le 2) { break }
    if ($actualW -le 0 -or $actualH -le 0) { break }
    $candW = [int]([math]::Round(($width * $candW) / [double]$actualW))
    $candH = [int]([math]::Round(($height * $candH) / [double]$actualH))
  }
}
`;

// PowerShell helper — inject keystrokes into the focused control via SendKeys.
// Text is passed base64-encoded to avoid CLI escaping of quotes/parens/newlines.
const OS_TYPE_PS1 = `param([string]$b64)
Add-Type -AssemblyName System.Windows.Forms
$text = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($b64))
$special = '+^%~(){}[]'
Start-Sleep -Milliseconds 200
foreach ($ch in $text.ToCharArray()) {
  if ($ch -eq "\`n") { [System.Windows.Forms.SendKeys]::SendWait("{ENTER}"); Start-Sleep -Milliseconds 15; continue }
  if ($ch -eq "\`r") { continue }
  $s = [string]$ch
  if ($special.IndexOf($ch) -ge 0) { $s = "{$ch}" }
  [System.Windows.Forms.SendKeys]::SendWait($s)
  Start-Sleep -Milliseconds 15
}
`;

/** 앱 이름 → PascalCase 클래스명 접두어 */
function toPascal(appName) {
  const raw = (appName || 'MyApp').replace(/[^A-Za-z0-9]/g, '');
  return raw.charAt(0).toUpperCase() + raw.slice(1);
}

// Electron 창 타이틀들의 최장 공통 부분문자열 (예: "Visual Studio Code").
// 런타임에 살아있는 창 rect를 찾는 앵커로 사용.
function longestCommonSubstringAll(strings) {
  const arr = [...new Set((strings || []).filter(Boolean))];
  if (arr.length === 0) return '';
  if (arr.length === 1) return arr[0];
  const src = arr.reduce((a, b) => (a.length <= b.length ? a : b));
  let best = '';
  for (let i = 0; i < src.length; i++) {
    for (let j = i + best.length + 1; j <= src.length; j++) {
      const sub = src.slice(i, j);
      if (arr.every(s => s.includes(sub))) { if (sub.length > best.length) best = sub; }
      else break;
    }
  }
  return best.trim();
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

// Extra CLI args needed to force a brand-new window rather than focusing/
// reusing an already-running single-instance app.
function newWindowArgsFor(exePath) {
  const base = path.basename(exePath || '').toLowerCase();
  if (base === 'code.exe') return ['-n'];
  return [];
}

// ── 단순 헤더 (Win32/UWP 단일 창 앱, session switching 불필요) ─────────────
const SIMPLE_HEADER = `import { execSync } from 'child_process';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));

// 주입/헬스 실패 수집 — 마지막에 실질 assert로 검증.
const _failures = [];

// OS-level click fallback via PowerShell user32.dll.
function osClick(x, y, button = 'left', clicks = 1) {
    try {
        execSync(
            \`powershell -NoProfile -File "\${join(__dirname, 'osClick.ps1')}" -x \${x} -y \${y} -button \${button} -clicks \${clicks}\`,
            { stdio: 'pipe', timeout: 5000 }
        );
    } catch (e) {
        _failures.push('osClick');
        console.warn('[osClick] failed:', String(e.message || e).substring(0, 100));
    }
}

// OS-level mouse wheel via PowerShell user32.dll (WinAppDriver has no wheel API).
function osScroll(x, y, delta) {
    try {
        execSync(
            \`powershell -NoProfile -File "\${join(__dirname, 'osScroll.ps1')}" -x \${x} -y \${y} -delta \${delta}\`,
            { stdio: 'pipe', timeout: 5000 }
        );
    } catch (e) {
        _failures.push('osScroll');
        console.warn('[osScroll] failed:', String(e.message || e).substring(0, 100));
    }
}

// 실제 마우스 클릭(osClick)을 요소 중심 좌표에 쏘기 위해 wdio의 표준 getRect로
// 현재 화면상 위치를 조회한다 — el.click()(UIA Invoke)은 SetCursorPos를 하지
// 않아 호버 의존 UI(Qt/QML 케밥 버튼 등)가 렌더링되지 않는 문제가 있었다.
async function getCenterSimple(selector) {
    try {
        const el = await browser.$(selector);
        await el.waitForExist({ timeout: 8000 });
        const rect = await el.getRect();
        return { x: Math.round(rect.x + rect.width / 2), y: Math.round(rect.y + rect.height / 2) };
    } catch { return null; }
}

`;

// ── 세션 전환 헤더 (Electron / 다중 창 앱) ────────────────────────────────
const SESSION_HEADER = `import { execSync, spawn } from 'child_process';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));

// 주입/헬스 실패 수집 — 마지막에 실질 assert로 검증.
const _failures = [];

// OS-level click via PowerShell user32.dll — verified for Electron menus, native dialogs.
function osClick(x, y, button = 'left', clicks = 1) {
    try {
        execSync(
            \`powershell -NoProfile -File "\${join(__dirname, 'osClick.ps1')}" -x \${x} -y \${y} -button \${button} -clicks \${clicks}\`,
            { stdio: 'pipe', timeout: 5000 }
        );
    } catch (e) {
        _failures.push('osClick');
        console.warn('[osClick] failed:', String(e.message || e).substring(0, 100));
    }
}

// OS-level mouse wheel via PowerShell user32.dll (WinAppDriver has no wheel API).
function osScroll(x, y, delta) {
    try {
        execSync(
            \`powershell -NoProfile -File "\${join(__dirname, 'osScroll.ps1')}" -x \${x} -y \${y} -delta \${delta}\`,
            { stdio: 'pipe', timeout: 5000 }
        );
    } catch (e) {
        _failures.push('osScroll');
        console.warn('[osScroll] failed:', String(e.message || e).substring(0, 100));
    }
}

// Window session pool: title → Appium sessionId.
// global browser (Root session) used ONCE per new windowTitle for hwnd discovery;
// a fast scoped appTopLevelWindow session is then opened via Appium REST API.
let _APPIUM = 'http://127.0.0.1:4723';
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
    const isHwnd = /^0x[0-9a-f]+$/i.test(app);
    const cap = isHwnd
        ? { platformName: 'Windows', 'appium:automationName': 'Windows', 'appium:appTopLevelWindow': app, 'appium:newCommandTimeout': 60000 }
        : { platformName: 'Windows', 'appium:automationName': 'Windows', 'appium:app': app, 'appium:newCommandTimeout': 60000 };
    const v = await _appiumPost('/session', { capabilities: { alwaysMatch: cap } });
    if (!v?.sessionId) throw new Error(\`Appium session failed for "\${app}": \${JSON.stringify(v)}\`);
    return v.sessionId;
}

// Health-check with a hard timeout — a dead/stale session must never hang the suite.
async function _isSessionAlive(sid) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 1500);
    try {
        const r = await fetch(\`\${_APPIUM}/session/\${sid}\`, { signal: ctrl.signal });
        if (!r.ok) return false;
        const j = await r.json();
        return !!j?.value;
    } catch {
        return false;
    } finally {
        clearTimeout(timer);
    }
}

// Cache entries are { sid, rootElId }. rootElId scopes element lookups to the
// discovered dialog's subtree when sid is a Root-session fallback (see below) —
// without it, every lookup walks the ENTIRE desktop UI tree (VSCode's full
// Electron accessibility tree included), costing 10s+ per call.
async function getWindowSession(title) {
    const cached = _sessionIds[title];
    if (cached && await _isSessionAlive(cached.sid)) return cached;
    delete _sessionIds[title];
    console.log(\`[session] Root scan for: "\${title}"\`);
    const shortTitle = title.slice(0, 30).replace(/"/g, '');
    let hwnd = null;
    let matchedEl = null;
    for (const sel of [\`//*[@Name="\${title}"]\`, \`//*[contains(@Name,"\${shortTitle}")]\`]) {
        try {
            const el = await browser.$(sel);
            const raw = await el.getAttribute('NativeWindowHandle');
            const hwndNum = parseInt(raw, 10);
            if (hwndNum) { hwnd = '0x' + hwndNum.toString(16); matchedEl = el; break; }
        } catch {}
    }
    const app = hwnd || 'Root';
    if (hwnd) console.log(\`[session] hwnd=\${hwnd} → scoped session\`);
    else console.warn(\`[session] Window "\${title}" not found — falling back to Root\`);
    try {
        const sid = await _createSession(app);
        _sessionIds[title] = { sid, rootElId: null };
    } catch (e) {
        console.warn(\`[session] scoped session failed (\${e.message}) — reusing Root session for "\${title}"\`);
        _sessionIds[title] = { sid: browser.sessionId, rootElId: matchedEl ? matchedEl.elementId : null };
    }
    return _sessionIds[title];
}

async function getCenter(sid, rootElId, selector) {
    try {
        const raw = selector.replace(/^['"]|['"]$/g, '');
        const using = raw.startsWith('~') ? 'accessibility id' : 'xpath';
        const value = raw.startsWith('~') ? raw.slice(1) : raw;
        const path = rootElId
            ? \`/session/\${sid}/element/\${rootElId}/element\`
            : \`/session/\${sid}/element\`;
        const el = await _appiumPost(path, { using, value });
        if (!el) return null;
        const elId = el.ELEMENT || el['element-6066-11e4-a52e-4f735466cecf'];
        const r = await (await fetch(\`\${_APPIUM}/session/\${sid}/element/\${elId}/rect\`)).json();
        const rect = r.value;
        if (!rect) return null;
        return { x: Math.round(rect.x + rect.width / 2), y: Math.round(rect.y + rect.height / 2) };
    } catch { return null; }
}

async function _typeScoped(sid, rootElId, selector, text) {
    try {
        const raw = selector.replace(/^['"]|['"]$/g, '');
        const using = raw.startsWith('~') ? 'accessibility id' : 'xpath';
        const value = raw.startsWith('~') ? raw.slice(1) : raw;
        const path = rootElId
            ? \`/session/\${sid}/element/\${rootElId}/element\`
            : \`/session/\${sid}/element\`;
        const el = await _appiumPost(path, { using, value });
        if (!el) throw new Error('element not found');
        const elId = el.ELEMENT || el['element-6066-11e4-a52e-4f735466cecf'];
        await _appiumPost(\`/session/\${sid}/element/\${elId}/clear\`, {});
        await _appiumPost(\`/session/\${sid}/element/\${elId}/value\`, { text });
    } catch (e) { _failures.push('type'); console.warn('[type] failed:', String(e.message || e).substring(0, 100)); }
}

// ── Electron 웹 콘텐츠: 창-상대 OS 재생 ────────────────────────────────────
// Electron 창은 NativeWindowHandle=0 → scoped UIA 세션도, live rect도 불가.
// 대신 앱 창의 현재 rect를 PowerShell로 읽어 녹화된 창-상대 오프셋을 재생.
// 창이 이동해도 좌표가 따라감.
// No caching: recorded flows can include actions (e.g. maximize) that change
// window geometry mid-replay. A cached rect would go stale after such a step
// and every subsequent rel-offset click would land on the old origin.
// Title fragment → hwnd of the window launchApp actually created for this run.
// Populated by launchApp via baseline/diff (see below). Once set, every
// _resolveWinRect/normalizeWindow call for that fragment targets this exact
// hwnd instead of re-searching by title — title substrings are NOT unique
// (e.g. any pre-existing "...- Visual Studio Code" window also matches), and
// replaying clicks against whichever window happens to match/be-foreground
// can land recorded titlebar clicks (including close) on the WRONG window.
const _hwndCache = {};

function _listWindowHwnds(frag) {
    if (!frag) return [];
    try {
        const out = execSync(
            \`powershell -NoProfile -File "\${join(__dirname, 'osWindowRect.ps1')}" -titleLike "\${frag}" -listOnly\`,
            { stdio: 'pipe', timeout: 5000 }
        ).toString().trim();
        if (!out) return [];
        return out.split(/\\r?\\n/).map(s => s.trim()).filter(Boolean).map(Number);
    } catch {
        return [];
    }
}

function _resolveWinRect(frag) {
    if (!frag) return null;
    const hwnd = _hwndCache[frag];
    try {
        const args = hwnd ? \`-hwnd \${hwnd}\` : \`-titleLike "\${frag}"\`;
        const out = execSync(
            \`powershell -NoProfile -File "\${join(__dirname, 'osWindowRect.ps1')}" \${args}\`,
            { stdio: 'pipe', timeout: 5000 }
        ).toString().trim();
        const m = out.match(/(-?\\d+)\\s+(-?\\d+)\\s+(-?\\d+)\\s+(-?\\d+)/);
        if (m) return { left: +m[1], top: +m[2], width: +m[3], height: +m[4] };
        if (hwnd) delete _hwndCache[frag]; // tracked window closed — next call re-searches by title
    } catch (e) {
        _failures.push('winRect');
        console.warn('[winRect] failed:', String(e.message || e).substring(0, 100));
    }
    return null;
}

// Force a newly-launched window to the exact geometry it was recorded at.
// Recorded rel-offsets are only valid if the window is the same SIZE as
// during recording, not just position — a freshly-launched window (often
// maximized) reflows its UI at a different size, pointing rel offsets at
// the wrong elements. Soft-fails: a move/resize failure doesn't abort the
// suite, but it does invalidate the cached rect so callers re-scan live.
function normalizeWindow(frag, left, top, width, height) {
    const hwnd = _hwndCache[frag];
    try {
        const target = hwnd ? \`-hwnd \${hwnd}\` : \`-titleLike "\${frag}"\`;
        execSync(
            \`powershell -NoProfile -File "\${join(__dirname, 'osMoveWindow.ps1')}" \${target} -left \${left} -top \${top} -width \${width} -height \${height}\`,
            { stdio: 'pipe', timeout: 8000 }
        );
    } catch (e) {
        _failures.push('moveWindow');
        console.warn('[moveWindow] failed:', String(e.message || e).substring(0, 100));
    }
}

// Launch a fresh app window before replay starts (session mode only), so the
// suite targets a known-clean window instead of whatever happens to already
// be open. Single-instance apps (e.g. VS Code with -n) don't spawn a new OS
// process at all — they message the already-running instance to open a new
// window — so a NEW hwnd can appear even when no NEW process does. We snapshot
// hwnds matching titleFrag BEFORE spawning and diff against the post-spawn
// set to identify that new window unambiguously, then cache it in _hwndCache
// so every later _resolveWinRect/normalizeWindow call targets that hwnd
// directly instead of re-matching by (possibly ambiguous) title.
async function launchApp(exePath, args, titleFrag, rect) {
    if (!exePath) return;
    // agent.py is_aumid()와 동일 판정, 대칭 유지 — "PackageFamilyName!AppId"는
    // 파일 경로가 아니라 explorer shell:AppsFolder로 활성화해야 한다.
    // spawn(exePath,...)로 직접 넘기면 파일 경로로 오인해 비동기 ENOENT로
    // 실패하는데, 이 실패는 이 catch 밖(다음 tick)에서 터져 try/catch에
    // 잡히지 않고 _failures에도 안 찍힌 채 20초 타임아웃만 나는 문제가 있었다.
    const isAumid = /!/.test(exePath) && !/[\\\/]/.test(exePath);
    const baseline = new Set(_listWindowHwnds(titleFrag));
    try {
        if (isAumid) {
            spawn('explorer.exe', ['shell:AppsFolder\\\\' + exePath], { detached: true, stdio: 'ignore' }).unref();
        } else {
            spawn(exePath, args, { detached: true, stdio: 'ignore' }).unref();
        }
    } catch (e) {
        _failures.push('launch');
        console.warn('[launch] failed:', String(e.message || e).substring(0, 100));
        return;
    }
    const deadline = Date.now() + 20000;
    while (Date.now() < deadline) {
        if (titleFrag && !_hwndCache[titleFrag]) {
            const fresh = _listWindowHwnds(titleFrag).find(h => !baseline.has(h));
            if (fresh) {
                _hwndCache[titleFrag] = fresh;
                console.log(\`[launch] tracking new window hwnd=\${fresh}\`);
            }
        }
        // A matched window with width/height 0 is a not-yet-rendered
        // placeholder (Electron/UWP frame created before content loads,
        // same hwnd, resized later) — treat it as "not found yet" and keep
        // polling instead of normalizing/replaying against a window that
        // isn't really there, which sent every later osClick to whatever
        // was actually on screen underneath (e.g. the desktop).
        const liveRect = _resolveWinRect(titleFrag);
        if (liveRect && liveRect.width > 0 && liveRect.height > 0) {
            if (rect) {
                normalizeWindow(titleFrag, rect.left, rect.top, rect.width, rect.height);
                const normalized = _resolveWinRect(titleFrag);
                console.log('[launch] window normalized to', normalized);
            }
            return;
        }
        await new Promise(r => setTimeout(r, 1000));
    }
    _failures.push('launch');
    console.warn('[launch] window not detected within timeout');
}

function osClickRel(frag, relX, relY, absX, absY, button = 'left', clicks = 1) {
    const r = _resolveWinRect(frag);
    if (r) osClick(r.left + relX, r.top + relY, button, clicks);
    else   osClick(absX, absY, button, clicks);      // 창 못 찾으면 녹화 절대좌표로 폴백
}
function osScrollRel(frag, relX, relY, absX, absY, delta) {
    const r = _resolveWinRect(frag);
    if (r) osScroll(r.left + relX, r.top + relY, delta);
    else   osScroll(absX, absY, delta);
}

// Electron 입력: 직전 osClick이 포커스를 잡아둔 상태 → OS 키 주입.
function osType(text) {
    try {
        const b64 = Buffer.from(text, 'utf8').toString('base64');
        execSync(
            \`powershell -NoProfile -File "\${join(__dirname, 'osType.ps1')}" -b64 "\${b64}"\`,
            { stdio: 'pipe', timeout: 8000 }
        );
    } catch (e) {
        _failures.push('osType');
        console.warn('[osType] failed:', String(e.message || e).substring(0, 100));
    }
}

`;

function generateWdio(strategy, appName, eventList, useSession, exePath) {
  const base     = toPascal(appName);
  const suffix   = strategy === 'id' ? 'ById' : 'ByClass';
  const testName = `${base}Test${suffix}`;
  const pageName = `${base}Page${suffix}`;
  const selFn    = strategy === 'id' ? wdioSelectorById : wdioSelectorByClass;

  const filtered     = filterEvents(eventList);
  const pageMethods  = [];
  const testSteps    = [];

  // Electron 창 rect 앵커: 모든 Electron 이벤트 타이틀의 공통 부분문자열.
  const _elecTitles = filtered.filter(e => e.isElectron === true)
                              .map(e => e.element?.windowTitle || '').filter(Boolean);
  const winFrag   = longestCommonSubstringAll(_elecTitles).replace(/["']/g, '');
  const winFragOk = winFrag.length >= 3;

  // 녹화 시점 창 기하: launchApp이 새로 띄운 창을 이 크기로 정규화하는 데 쓰인다
  // (창이 최대화 상태로 뜨면 UI가 리플로우되어 rel 오프셋이 어긋나므로).
  // 우선순위: Electron 이벤트의 실측 winLeft/Top/Width/Height → session_meta.initialWindow.
  const _rectEvent = filtered.find(e =>
    e.isElectron === true &&
    Number.isInteger(e.winLeft) && Number.isInteger(e.winTop) &&
    Number.isInteger(e.winWidth) && Number.isInteger(e.winHeight)
  );
  const _sessionMeta = eventList.find(e => e.action === 'session_meta');
  const recordedRect = _rectEvent
    ? { left: _rectEvent.winLeft, top: _rectEvent.winTop, width: _rectEvent.winWidth, height: _rectEvent.winHeight }
    : (_sessionMeta?.initialWindow || null);

  // 다이얼로그 내부의 "coordinate" 폴백 이벤트(agent.py가 element.windowTitle을
  // 못 채운 경우)는 relX/relY/winLeft/winTop은 있지만 windowTitle이 빈 문자열이다.
  // 직전에 관측된 실제 windowTitle을 이어받아 rel 재생(osClickRel/osScrollRel)이
  // 가능하도록 한다 — 같은 다이얼로그 안에서 연속 캡처되므로 안전한 가정이다.
  let lastWinTitle = '';

  filtered.forEach((e, i) => {
    const stepNum  = i + 1;
    const sel      = selFn(e.element);
    const isEdit   = EDITABLE_CONTROL_TYPES.has(e.element?.controlType);
    const x        = e.x ?? 0;
    const y        = e.y ?? 0;
    const winTitle = e.element?.windowTitle || '';
    const titleArg = escapeStr(winTitle);
    const hasRel      = Number.isInteger(e.relX) && Number.isInteger(e.relY);
    const relTitle    = winTitle || lastWinTitle;
    if (winTitle) lastWinTitle = winTitle;
    const electronCtx = winFragOk && relTitle.includes(winFrag);

    if (e.action === 'scroll') {
      // WinAppDriver/Appium W3C Actions have no wheel primitive — replay via
      // OS-level mouse_event(MOUSEEVENTF_WHEEL). WHEEL_DELTA=120 per notch;
      // e.delta is the summed notch count captured by the agent.
      const wheelDelta = (e.delta ?? 0) * 120;
      if (useSession && hasRel && relTitle) {
        const relFrag = electronCtx ? winFrag : relTitle;
        pageMethods.push(
`    async scroll${stepNum}() {
        osScrollRel('${escapeStr(relFrag)}', ${e.relX}, ${e.relY}, ${x}, ${y}, ${wheelDelta});
    }`
        );
      } else {
        pageMethods.push(
`    async scroll${stepNum}() {
        osScroll(${x}, ${y}, ${wheelDelta});
    }`
        );
      }
      testSteps.push(
`            console.log('[STEP ${stepNum}] scroll: delta=${wheelDelta}');
            await page.scroll${stepNum}();`
      );
      return;
    }

    if (e.action === 'type' && isEdit) {
      if (useSession && electronCtx) {
        // Electron 입력 → UIA 세션 조회(45초 실패 경로) 제거, OS 키 주입.
        pageMethods.push(
`    async type${stepNum}(value) {
        osType(value);
    }`
        );
      } else if (useSession && winTitle) {
        const elSel = sel || `'//*[@Name="${escapeAttr(e.element?.name)}"]'`;
        pageMethods.push(
`    async type${stepNum}(value) {
        const s = await getWindowSession('${titleArg}');
        await _typeScoped(s.sid, s.rootElId, ${elSel}, value);
    }`
        );
      } else {
        const elSel = sel || `'//*[@Name="${escapeAttr(e.element?.name)}"]'`;
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
      // 우클릭/더블클릭 재생용 osClick 인자. doubleClick 앞에 agent가 동일 좌표의
      // 단일 click 이벤트를 별도로 방출하는 경우가 있는데, 여기선 dedupe하지 않는다(스코프 외).
      const btnArg = e.action === 'rightClick' ? `, 'right'`
                   : e.action === 'doubleClick' ? `, 'left', 2`
                   : '';
      if (useSession) {
        const nativeScoped = !e.isElectron && winTitle && sel;
        if (nativeScoped) {
          // Non-Electron native window: scoped session → getCenter → osClick.
          // Gives current element coordinates even if window has moved.
          pageMethods.push(
`    async click${stepNum}() {
        let s = await getWindowSession('${titleArg}');
        let c = await getCenter(s.sid, s.rootElId, ${sel});
        if (!c) {
            delete _sessionIds['${titleArg}'];
            s = await getWindowSession('${titleArg}');
            c = await getCenter(s.sid, s.rootElId, ${sel});
        }
        c = c ?? { x: ${x}, y: ${y} };
        osClick(c.x, c.y${btnArg});
    }`
          );
        } else if (hasRel && relTitle) {
          // 창-상대 좌표를 런타임 창 원점 기준으로 재생 (창 이동/다이얼로그 위치 변화 대응).
          // Electron은 타이틀이 파일별로 바뀌므로 공통 부분문자열(winFrag)을,
          // 네이티브 창(다이얼로그 포함)은 타이틀이 안정적이므로 relTitle(빈 경우 직전
          // 타이틀 승계) 그대로 사용.
          const relFrag = electronCtx ? winFrag : relTitle;
          pageMethods.push(
`    async click${stepNum}() {
        osClickRel('${escapeStr(relFrag)}', ${e.relX}, ${e.relY}, ${x}, ${y}${btnArg});
    }`
          );
        } else {
          // 최후 폴백: 녹화 절대좌표.
          pageMethods.push(
`    async click${stepNum}() {
        osClick(${x}, ${y}${btnArg});
    }`
          );
        }
      } else if (sel) {
        // Simple mode: resolve element's live center via getCenterSimple, then
        // click through actual mouse injection (osClick) — el.click() (UIA
        // Invoke) never moves the cursor, so hover-dependent UI never renders.
        pageMethods.push(
`    async click${stepNum}() {
        const c = await getCenterSimple(${sel});
        if (c) osClick(c.x, c.y${btnArg});
        else osClick(${x}, ${y}${btnArg});
    }`
        );
      } else {
        pageMethods.push(
`    async click${stepNum}() {
        osClick(${x}, ${y}${btnArg});
    }`
        );
      }
      testSteps.push(
`            console.log('[STEP ${stepNum}] click: ${escapeStr(e.element?.name || '')}');
            await page.click${stepNum}();`
      );
    }
  });

  // OS 주입(osClick/osScroll/type) 실패를 실제 assert로 검증(거짓 통과 방지).
  // SIMPLE_HEADER/SESSION_HEADER 둘 다 _failures를 갖는다.
  const assertLine = `            expect(_failures).toEqual([]);`;

  const header   = useSession ? SESSION_HEADER : SIMPLE_HEADER;
  const launchCall = (useSession && exePath && winFragOk)
    ? `        await launchApp(${JSON.stringify(exePath)}, ${JSON.stringify(newWindowArgsFor(exePath))}, ${JSON.stringify(winFrag)}, ${JSON.stringify(recordedRect)});\n`
    : '';
  const beforeHook = useSession ? `
    beforeAll(async () => {
        const { hostname, port } = browser.options;
        _APPIUM = \`http://\${hostname || '127.0.0.1'}:\${port || 4723}\`;
        console.log(\`[session] Appium endpoint resolved to \${_APPIUM}\`);
${launchCall}    });
` : '';

  const afterHook = useSession ? `
    afterAll(async () => {
        for (const { sid } of Object.values(_sessionIds)) {
            if (sid === browser.sessionId) continue;
            try { await fetch(\`\${_APPIUM}/session/\${sid}\`, { method: 'DELETE' }); } catch {}
        }
    });
` : '';

  return header + `class ${pageName} {
${pageMethods.join('\n\n')}
}

describe('${testName}', () => {${beforeHook}${afterHook}
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
    const wdioById    = generateWdio('id',    name, events, useSession, exe);
    const wdioByClass = generateWdio('class', name, events, useSession, exe);
    const wdioFiles   = [
      { filename: `${base}TestById.js`,    content: wdioById    },
      { filename: `${base}TestByClass.js`, content: wdioByClass },
    ];

    // 앱별 서브폴더에 저장
    const wdioOutDir = path.join(WDIO_BASE_DIR, base);

    const confContent = buildWdioConf(exe || name, wdioFiles.map(f => f.filename), useSession);
    const confFile    = { filename: 'wdio.conf.js', content: confContent };
    const ps1File     = { filename: 'osClick.ps1',  content: OS_CLICK_PS1 };
    const scrollPs1File = { filename: 'osScroll.ps1', content: OS_SCROLL_PS1 };
    const winRectPs1File = { filename: 'osWindowRect.ps1', content: OS_WINRECT_PS1 };
    const moveWinPs1File = { filename: 'osMoveWindow.ps1', content: OS_MOVEWINDOW_PS1 };
    const typePs1File    = { filename: 'osType.ps1',       content: OS_TYPE_PS1 };
    const { savedPaths: wdioPaths, saveError: wdioErr } = saveFiles(
      [...wdioFiles, confFile, ps1File, scrollPs1File, winRectPs1File, moveWinPs1File, typePs1File], wdioOutDir
    );

    const warnings = !exe ? ['exePath missing — launchApp will be skipped'] : [];

    if (useSession && !exe) {
      console.warn('[generate] exePath missing — launchApp skipped');
    }

    broadcast('generation', {
      status: 'success',
      files: wdioFiles.map(f => f.filename),
      folder: base,
      ...(warnings.length ? { warning: warnings[0] } : {}),
    });

    res.json({
      ok: true,
      files: wdioFiles,
      savedPaths: wdioPaths,
      folder: base,
      runCommand: `cd generated-wdio && npx wdio run ${base}/wdio.conf.js`,
      saveErrors: [wdioErr].filter(Boolean),
      warnings,
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