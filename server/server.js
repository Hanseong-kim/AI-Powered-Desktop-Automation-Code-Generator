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

// PowerShell helper — programmatic scroll (2026-07-10 스테이크홀더 지시:
// PowerShell로 화면 좌표에 물리 마우스 신호를 주입하는 방식 금지).
// 1) 추적 중인 top-level hwnd 아래에서 녹화된 스크롤 컨테이너를 UIA로 찾고
//    ScrollPattern.Scroll()로 픽셀 없이 스크롤 (PoC ② 실증: Explorer
//    UIItemsView에서 VerticalScrollPercent 0→0.374, SetCursorPos 0회).
// 2) 레거시 컨트롤(MMC ListView, CharGridWClass 등 — PoC에서 ScrollPattern
//    미지원 확인)은 hwnd-scoped WM_MOUSEWHEEL을 PostMessageW로 전달.
//    반드시 PostMessage(비동기) — SendMessageW(동기)는 PoC 중 charmap.exe를
//    비정상 종료시킴. lParam의 좌표는 요소의 "라이브" rect 중심을 지금
//    계산한 것으로, 녹화 좌표 재생이 아니다. 물리 커서/마우스 상태는 일절
//    건드리지 않는다 (SetCursorPos / mouse_event 없음).
const OS_SCROLL_PS1 = `param([string]$hwnd, [string]$selB64, [int]$delta)
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes -ErrorAction SilentlyContinue
$sig = '[DllImport("user32.dll")] public static extern bool PostMessageW(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);'
Add-Type -MemberDefinition $sig -Name WheelMsg -Namespace U -ErrorAction SilentlyContinue
if (-not $hwnd -or [int64]$hwnd -eq 0) { Write-Error 'osScroll: -hwnd is required'; exit 2 }
$root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]([int64]$hwnd))
if (-not $root) { Write-Error 'osScroll: AutomationElement.FromHandle failed'; exit 2 }

# selB64 = base64(UTF-8 JSON { automationId, className, name }) — 캡처 시점에
# agent.py가 ScrollPattern 보유 조상으로 걸어 올라가 기록한 컨테이너 셀렉터.
$sel = $null
if ($selB64) {
  try {
    $json = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($selB64))
    $sel = $json | ConvertFrom-Json
  } catch {}
}

$target = $null
if ($sel) {
  $conds = @()
  if ($sel.automationId) {
    $conds += New-Object System.Windows.Automation.PropertyCondition(
      [System.Windows.Automation.AutomationElement]::AutomationIdProperty, [string]$sel.automationId)
  }
  if ($sel.className) {
    $conds += New-Object System.Windows.Automation.PropertyCondition(
      [System.Windows.Automation.AutomationElement]::ClassNameProperty, [string]$sel.className)
  }
  if ($sel.name) {
    $conds += New-Object System.Windows.Automation.PropertyCondition(
      [System.Windows.Automation.AutomationElement]::NameProperty, [string]$sel.name)
  }
  foreach ($c in $conds) {
    try { $target = $root.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $c) } catch {}
    if ($target) { break }
  }
}
if (-not $target) { $target = $root }

# 1차: 대상(또는 가장 가까운 스크롤 가능 조상)의 ScrollPattern.
$walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
$cur = $target
$scroll = $null
for ($i = 0; $i -lt 10 -and $cur; $i++) {
  $p = $null
  if ($cur.TryGetCurrentPattern([System.Windows.Automation.ScrollPattern]::Pattern, [ref]$p)) {
    $sp = [System.Windows.Automation.ScrollPattern]$p
    if ($sp.Current.VerticallyScrollable) { $scroll = $sp; break }
  }
  try { $cur = $walker.GetParent($cur) } catch { break }
}
if ($scroll) {
  $before = $scroll.Current.VerticalScrollPercent
  # 휠 업(양수 delta) = 콘텐츠 위로 = SmallDecrement. 노치당 약 3줄.
  $dir = if ($delta -gt 0) { [System.Windows.Automation.ScrollAmount]::SmallDecrement }
         else { [System.Windows.Automation.ScrollAmount]::SmallIncrement }
  $n = [math]::Abs($delta) * 3
  for ($i = 0; $i -lt $n; $i++) {
    $scroll.Scroll([System.Windows.Automation.ScrollAmount]::NoAmount, $dir)
  }
  Start-Sleep -Milliseconds 150
  $after = $scroll.Current.VerticalScrollPercent
  Write-Output "[osScroll] ScrollPattern $before -> $after (delta=$delta)"
  exit 0
}

# 2차: hwnd-scoped WM_MOUSEWHEEL (PostMessageW — 비동기, SendMessage 금지).
$postH = [IntPtr]([int64]$hwnd)
$cur = $target
for ($i = 0; $i -lt 10 -and $cur; $i++) {
  try {
    $nh = $cur.Current.NativeWindowHandle
    if ($nh) { $postH = [IntPtr]$nh; break }
    $cur = $walker.GetParent($cur)
  } catch { break }
}
$cx = 0; $cy = 0
try {
  $r = $target.Current.BoundingRectangle
  $cx = [int]($r.X + $r.Width / 2)
  $cy = [int]($r.Y + $r.Height / 2)
} catch {}
$wp = [IntPtr]([int64]($delta * 120) -shl 16)
$lp = [IntPtr]([int64]((($cy -band 0xFFFF) -shl 16) -bor ($cx -band 0xFFFF)))
[U.WheelMsg]::PostMessageW($postH, 0x020A, $wp, $lp) | Out-Null
Write-Output "[osScroll] PostMessageW WM_MOUSEWHEEL hwnd=$postH delta=$delta (ScrollPattern unavailable)"
`;

// PowerShell helper — read the current rect of a window whose title contains
// $titleLike, enumerating ALL top-level windows (not just one MainWindow per
// process) so non-main dialogs (e.g. native "Open Folder") are found too.
// Prints "left top width height". Used to re-base window-relative coordinates
// at replay time (survives window repositioning).
// NOTE: no SetProcessDPIAware call here — agent.py capture and osClick.ps1 are
// both DPI-unaware, and adding awareness here would skew coordinates by the
// DPI scale factor (verified: unaware rect matches agent-captured winLeft).
const OS_WINRECT_PS1 = `param([string]$titleLike, [string]$hwnd, [switch]$listOnly, [switch]$ownerOnly)
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
  [DllImport("user32.dll")] public static extern IntPtr GetWindow(IntPtr hWnd, uint cmd);
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
  if ($ownerOnly) {
    # GW_OWNER=4 — nonzero means an owned (dialog-style) window, which
    # WinAppDriver's appTopLevelWindow rejects outright ("not a top level
    # window handle"), so callers skip the scoped-session attempt entirely.
    Write-Output ([int64][WinEnum]::GetWindow($h, 4))
    exit
  }
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
  [DllImport("user32.dll")] public static extern bool IsZoomed(IntPtr hWnd);
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
  # Idempotency fast-path: if the window is already at the target geometry
  # (and not maximized), skip ShowWindow(RESTORE)+MoveWindow entirely — avoids
  # a visible restore-then-resize flicker when replay finds the window already
  # in the recorded position (e.g. the "already maximized" case reported
  # 2026-07-07: recorded flow assumes a maximize step is needed, but the
  # window is already there).
  $already = New-Object WinMove+RECT
  [WinMove]::GetWindowRect($hWnd, [ref]$already) | Out-Null
  $sameW = [math]::Abs(($already.Right - $already.Left) - $width) -le 2
  $sameH = [math]::Abs(($already.Bottom - $already.Top) - $height) -le 2
  $sameL = [math]::Abs($already.Left - $left) -le 2
  $sameT = [math]::Abs($already.Top - $top) -le 2
  if (-not [WinMove]::IsZoomed($hWnd) -and $sameW -and $sameH -and $sameL -and $sameT) {
    exit
  }
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

// Fail-and-Recover ESC fallback (v1 extension) — only called from _step()
// after a step already failed AND osDismissPopup() found no known dismiss
// button (e.g. an inline rename edit-box or an open context menu, not a
// dialog with named buttons). A bare ESC is the generic "back out" input
// for both cases.
const OS_ESCAPE_PS1 = `Add-Type -AssemblyName System.Windows.Forms
Start-Sleep -Milliseconds 100
[System.Windows.Forms.SendKeys]::SendWait("{ESC}")
`;

// PowerShell helper — bring a window whose title contains $titleLike to the
// foreground. Simple-mode (single-window, non-session) tests never call
// launchApp's normalizeWindow/foreground step, so a freshly launched app can
// stay behind other windows or the OS may place it wherever it wants (not
// necessarily where it was during recording) — every subsequent osClick then
// misses because it's still aimed at the click's LIVE-resolved coordinates,
// which are correct, but the window with focus/foreground may not be the one
// receiving OS-level input. Restore-then-foreground before the first click.
// SetForegroundWindow alone is blocked by Windows' foreground-lock unless the
// calling thread shares input state with the current foreground thread —
// AttachThreadInput before the call (detached after) plus a topmost toggle
// forces it through regardless of which process currently owns focus.
const OS_ACTIVATE_PS1 = `param([string]$titleLike, [string]$hwnd)
Add-Type @"
using System;
using System.Text;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public class WinActivate {
  public delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc p, IntPtr l);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern int GetWindowTextLength(IntPtr h);
  [DllImport("user32.dll", CharSet=CharSet.Auto)] public static extern int GetWindowText(IntPtr h, StringBuilder sb, int m);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, IntPtr pid);
  [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint a, uint b, bool f);
  [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
  [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint flags);
  public static List<IntPtr> Find(string t) {
    var f = new List<IntPtr>();
    EnumWindows((h,l) => { if(!IsWindowVisible(h)) return true; int n=GetWindowTextLength(h); if(n==0) return true;
      var sb=new StringBuilder(n+1); GetWindowText(h,sb,sb.Capacity); if(sb.ToString().Contains(t)) f.Add(h); return true; }, IntPtr.Zero);
    return f;
  }
  public static void Force(IntPtr h) {
    ShowWindow(h, 9); // SW_RESTORE
    uint fg = GetWindowThreadProcessId(GetForegroundWindow(), IntPtr.Zero);
    uint me = GetCurrentThreadId();
    if (fg != me) AttachThreadInput(me, fg, true);
    BringWindowToTop(h); SetForegroundWindow(h);
    SetWindowPos(h, (IntPtr)(-1), 0,0,0,0, 0x0003); // TOPMOST, NOMOVE|NOSIZE
    SetWindowPos(h, (IntPtr)(-2), 0,0,0,0, 0x0003); // NOTOPMOST
    if (fg != me) AttachThreadInput(me, fg, false);
  }
}
"@ -ErrorAction SilentlyContinue
if ($hwnd) {
  [WinActivate]::Force([IntPtr]([int64]$hwnd)); Start-Sleep -Milliseconds 250
} else {
  $m = [WinActivate]::Find($titleLike)
  if ($m.Count -gt 0) { [WinActivate]::Force($m[0]); Start-Sleep -Milliseconds 250 }
}
`;

// PowerShell helper — Fail-and-Recover popup dismissal (v2). Only ever invoked
// AFTER a step has already failed (see _step() in the headers below), so it
// costs nothing on the happy path. Scans for candidate popup windows and
// clicks the first button matching a conservative preference order
// (no-side-effect buttons first). $hwnd (when known) identifies the main app
// window deterministically, same rationale as -hwnd elsewhere (see
// OS_WINRECT_PS1); $titleLike is the fallback. A window qualifies as a popup
// candidate only when it is dialog-SHAPED: dialog class #32770 or an owned
// window, belonging to (or owned by) the target process. v1 qualified EVERY
// same-PID top-level window, which in single-process multi-window apps put
// the user's other main windows on the list — VS Code runs all windows in
// one Code.exe process, and the preferred-button scan then Invoke()d the
// TITLEBAR close button (UIA Name "닫기") of the user's own editor window
// (confirmed 2026-07-09: replay closed the VS Code window running the user's
// other work mid-run). $exclude lists hwnds the replay itself is driving
// (main window + tracked dialogs) — dismissing those would tear down the
// very UI the failed step is about to retry against.
const OS_DISMISS_POPUP_PS1 = `param([string]$titleLike, [string]$hwnd, [string]$exclude)
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes -ErrorAction SilentlyContinue
Add-Type @"
using System;
using System.Text;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public class PopupWin {
  public delegate bool EnumProc(IntPtr hWnd, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc proc, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool IsWindow(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern int GetWindowTextLength(IntPtr hWnd);
  [DllImport("user32.dll", CharSet = CharSet.Auto)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder sb, int max);
  [DllImport("user32.dll", CharSet = CharSet.Auto)] public static extern int GetClassName(IntPtr hWnd, StringBuilder sb, int max);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
  [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);
  [DllImport("user32.dll")] public static extern IntPtr GetWindow(IntPtr hWnd, uint cmd);
  public static IntPtr OwnerOf(IntPtr h) { return GetWindow(h, 4); } // GW_OWNER
  public static List<IntPtr> AllTop() {
    var f = new List<IntPtr>();
    EnumWindows((h, l) => { if (IsWindowVisible(h)) f.Add(h); return true; }, IntPtr.Zero);
    return f;
  }
  public static string ClassOf(IntPtr h) {
    var sb = new StringBuilder(256);
    GetClassName(h, sb, sb.Capacity);
    return sb.ToString();
  }
  public static string TitleOf(IntPtr h) {
    int len = GetWindowTextLength(h);
    if (len == 0) return "";
    var sb = new StringBuilder(len + 1);
    GetWindowText(h, sb, sb.Capacity);
    return sb.ToString();
  }
  public static uint PidOf(IntPtr h) {
    uint pid; GetWindowThreadProcessId(h, out pid); return pid;
  }
}
"@ -ErrorAction SilentlyContinue

$mainHwnd = [IntPtr]::Zero
if ($hwnd) { $mainHwnd = [IntPtr]([int64]$hwnd) }
elseif ($titleLike) {
  foreach ($h in [PopupWin]::AllTop()) {
    if ([PopupWin]::TitleOf($h).Contains($titleLike)) { $mainHwnd = $h; break }
  }
}
$targetPid = 0
if ($mainHwnd -ne [IntPtr]::Zero) { $targetPid = [PopupWin]::PidOf($mainHwnd) }

$excludeSet = New-Object 'System.Collections.Generic.HashSet[long]'
if ($exclude) {
  foreach ($tok in ($exclude -split ',')) {
    $t = $tok.Trim()
    if ($t) { [void]$excludeSet.Add([int64]$t) }
  }
}

# Candidate = dialog-shaped window of the target process only:
#  - same PID AND (#32770 class OR owned window)  → native/Electron/Qt dialogs
#  - #32770 owned by a target-PID window          → dialog hosted out-of-process
# Never: unowned main-class windows (a sibling VS Code window shares the PID
# but is nobody's popup), excluded hwnds (windows the replay itself drives),
# or windows of unrelated processes. If no target PID could be resolved at
# all, dismiss nothing — guessing across the whole desktop is how an
# unrelated app loses a dialog.
$candidates = New-Object System.Collections.Generic.List[IntPtr]
if ($targetPid -ne 0) {
  foreach ($h in [PopupWin]::AllTop()) {
    if ($h -eq $mainHwnd) { continue }
    if ($excludeSet.Contains([int64]$h)) { continue }
    $isDialogClass = ([PopupWin]::ClassOf($h) -eq '#32770')
    $owner = [PopupWin]::OwnerOf($h)
    $qualifies = $false
    if ([PopupWin]::PidOf($h) -eq $targetPid) {
      $qualifies = ($isDialogClass -or ($owner -ne [IntPtr]::Zero))
    } elseif ($isDialogClass -and $owner -ne [IntPtr]::Zero -and [PopupWin]::PidOf($owner) -eq $targetPid) {
      $qualifies = $true
    }
    if ($qualifies) { $candidates.Add($h) }
  }
}

# Conservative order: no-side-effect dismissal first (Cancel/No/Close), only
# reach for an affirmative (OK/Yes) if nothing safer matched — a wrong click
# here should never silently accept a destructive action.
$preferred = @('취소', '아니요', '닫기', 'Cancel', 'No', 'Close', '확인', 'OK', '예', 'Yes')

$dismissed = $false
foreach ($h in $candidates) {
  if ($dismissed) { break }
  try {
    $el = [System.Windows.Automation.AutomationElement]::FromHandle($h)
    if (-not $el) { continue }
    $cond = New-Object System.Windows.Automation.PropertyCondition(
      [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
      [System.Windows.Automation.ControlType]::Button)
    $buttons = $el.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cond)
    foreach ($btnName in $preferred) {
      if ($dismissed) { break }
      foreach ($b in $buttons) {
        if ($b.Current.Name -ne $btnName) { continue }
        $clicked = $false
        try {
          $inv = $b.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
          $inv.Invoke()
          $clicked = $true
        } catch {
          try {
            $legacy = $b.GetCurrentPattern([System.Windows.Automation.LegacyIAccessiblePattern]::Pattern)
            $legacy.DoDefaultAction()
            $clicked = $true
          } catch {
            # Owner-drawn / non-standard control — last resort: BM_CLICK via SendMessage.
            try {
              $bh = [IntPtr]$b.Current.NativeWindowHandle
              if ($bh -ne [IntPtr]::Zero) { [PopupWin]::SendMessage($bh, 0x00F5, [IntPtr]::Zero, [IntPtr]::Zero) | Out-Null; $clicked = $true }
            } catch {}
          }
        }
        if ($clicked) {
          $deadline = (Get-Date).AddSeconds(3)
          while ((Get-Date) -lt $deadline -and [PopupWin]::IsWindow($h)) { Start-Sleep -Milliseconds 100 }
          Write-Output "DISMISSED|$btnName|$([PopupWin]::TitleOf($h))"
          $dismissed = $true
        }
        break
      }
    }
  } catch {}
}
if (-not $dismissed) { Write-Output "NONE" }
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

// agent.py의 _emit_click_from_press()는 물리적 더블클릭 1회를 click + click +
// doubleClick 3개 이벤트로 방출한다(반복 단일클릭 "9999" 보존을 위한 의도된 설계 —
// 병합/드롭 없음). 그러나 그대로 재생하면 각 이벤트가 별도 _step()이 되어 스텝 사이에
// 수백ms~수초의 간격이 생기고, 탐색기 같은 앱에서는 "클릭 → 대기 → 재클릭"이 곧
// rename 제스처가 되어 폴더 진입 대신 이름 바꾸기 모드가 켜진다(2026-07-08 VSCode
// "폴더 열기" 다이얼로그에서 확인). 코드 생성 시점에 doubleClick 구성 클릭(직전 최대
// 2개의 click, 같은 좌표+근접 타임스탬프)을 걷어내 doubleClick 스텝 하나만 남긴다.
const DEDUPE_RADIUS = 6;       // px — agent.py DOUBLE_CLICK_RADIUS와 동일
const DEDUPE_MAX_GAP_MS = 1500; // ms — 방출 시각 비교라 캡처 창(500ms)보다 여유 있게

function dedupeDoubleClicks(events) {
  const out = [];
  for (const raw of events) {
    let e = raw;
    if (e.action === 'doubleClick') {
      let merged = 0;
      let donorElement = null;
      while (merged < 2 && out.length > 0) {
        const prev = out[out.length - 1];
        if (prev.action !== 'click') break;
        const dx = Math.abs((prev.x ?? 0) - (e.x ?? 0));
        const dy = Math.abs((prev.y ?? 0) - (e.y ?? 0));
        const dt = Math.abs((prev.timestamp ?? 0) - (e.timestamp ?? 0)) * 1000;
        if (dx > DEDUPE_RADIUS || dy > DEDUPE_RADIUS || dt > DEDUPE_MAX_GAP_MS) break;
        out.pop();
        merged++;
        // 두 번째 press 시점의 hit-test는 (더블클릭 도중 UI가 전환되는 타이밍이라)
        // 자주 빈 요소를 반환한다(confirmed 2026-07-08: VSCode 폴더 다이얼로그의
        // doubleClick 이벤트 3개 전부 automationId/name/className 없음) — 걷어낸
        // 구성 click 중 셀렉터를 가진 것이 있으면 doubleClick에 승계해 좌표만
        // 재생하는 대신 이름 기반 셀렉터를 쓸 수 있게 한다.
        if (!donorElement && (prev.element?.automationId || prev.element?.name || prev.element?.className)) {
          donorElement = prev.element;
        }
      }
      if (merged > 0) {
        console.log(`[dedupe] merged ${merged} click(s) into doubleClick @(${e.x},${e.y})`);
      }
      const own = e.element || {};
      if (!own.automationId && !own.name && !own.className && donorElement) {
        console.log(`[dedupe] doubleClick @(${e.x},${e.y}) had no element data — adopted selector from constituent click`);
        e = { ...e, element: donorElement };
      }
    }
    out.push(e);
  }
  return out;
}

// ── WdIO locator 결정 ────────────────────────────────────────────────────────

/** className에 공백이 많거나 소문자 camelCase면 Electron/VSCode CSS 클래스 — 신뢰 불가 */
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

// XAML controls often carry their bare type name as automationId (e.g. a
// Notepad menu item captured as automationId="TextBlock") — these repeat
// throughout a window/popup, so trusting them as a unique accessibility id
// matches the wrong instance (confirmed 2026-07-08: File-menu item "다른
// 이름으로 저장" carried automationId="TextBlock"). Same treatment as the
// numeric-id skip below: prefer Name when one was captured.
const GENERIC_AUTOMATION_IDS = new Set(['TextBlock', 'Image', 'ContentPresenter', 'Border', 'Grid', 'StackPanel']);

// 자기 자신에게 유니크 AutomationId/Name이 없는 요소용 — 캡처 시점에 agent.py가
// 찾아둔 "안정적 ID를 가진 조상 anchor" 기준 relative XPath (2026-07-10 지시:
// 유니크 ID가 없는 요소는 이웃 anchor 요소 기준 relative XPath로 해결).
// anchorPath는 "/Pane[2]/Button[1]" 형태만 신뢰 — 그 외 형식은 무시해 XPath
// 인젝션/오생성을 차단한다.
function anchorSelector(el) {
  if (!el?.anchorId || !el?.anchorPath) return null;
  if (!/^(\/[A-Za-z]+\[\d+\])+$/.test(el.anchorPath)) return null;
  return `'//*[@AutomationId="${escapeAttr(el.anchorId)}"]${el.anchorPath}'`;
}

function wdioSelectorById(el) {
  if (!el) return null;
  // Skip purely numeric automationIds (e.g. "4", "1") — these are ListView slot
  // indices assigned at runtime, not stable accessibility IDs. Use name instead.
  const hasStableId = el.automationId && !/^\d+$/.test(el.automationId)
    && !(GENERIC_AUTOMATION_IDS.has(el.automationId) && el.name);
  if (hasStableId) return `'~${escapeAttr(el.automationId)}'`;
  if (el.name)     return `'//*[@Name="${escapeAttr(el.name)}"]'`;
  // anchor를 className보다 우선 — anchor는 인덱스까지 고정된 유일 경로지만
  // bare className XPath는 첫 매치가 엉뚱한 인스턴스일 수 있다.
  const anchored = anchorSelector(el);
  if (anchored) return anchored;
  if (el.className && isStableClassName(el.className)) return `'//*[@ClassName="${escapeAttr(el.className)}"]'`;
  return null;
}

function wdioSelectorByClass(el) {
  if (!el) return null;
  // controlType이 알려져 있으면 XPath 태그를 그걸로 제한 — WinAppDriver의 XML은
  // 노드 태그명이 곧 ControlType이므로(예: Button, Edit, Pane), 와일드카드(*)보다
  // 탐색 폭이 좁아지고 잘못된 노드(예: 컬럼 헤더)에 매칭될 여지가 줄어든다
  // (confirmed 2026-07-07 — VSCode ByClass가 UIProperty/@Name="이름" 와일드카드로
  // 엉뚱한 노드를 골라 클릭 타임아웃을 유발).
  const tag = (el.controlType && /^[A-Za-z]+$/.test(el.controlType)) ? el.controlType : '*';
  if (el.className && isStableClassName(el.className) && el.name)
    return `'//${tag}[@ClassName="${escapeAttr(el.className)}" and @Name="${escapeAttr(el.name)}"]'`;
  if (el.className && isStableClassName(el.className)) return `'//${tag}[@ClassName="${escapeAttr(el.className)}"]'`;
  if (el.name)         return `'//${tag}[@Name="${escapeAttr(el.name)}"]'`;
  if (el.automationId) return `'~${escapeAttr(el.automationId)}'`;
  return anchorSelector(el);
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
  // 콘텐츠에 따라 타이틀이 바뀌는 단일 창(예: Win11 메모장의 "*a - 메모장" →
  // "*asdfasdfasdf - 메모장")을 여러 창으로 오인해 세션 전환 모드로 빠지는 것을 방지.
  // rootHwndHex는 agent.py가 이벤트마다 emit하는 실제 top-level 창 핸들 — 전부
  // 동일하면 창은 하나뿐이므로 titles.size는 무시하고 simple 모드로 강제한다.
  const roots = new Set(filtered.map(e => e.rootHwndHex).filter(Boolean));
  if (roots.size === 1) return false;
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
// 조용히 넘어갈 수 있는 성능/폴백 신호 — 실패는 아니지만 재생 품질 저하 가능성을 기록.
const _warnings = [];

// One-time PowerShell/.NET cold-start warm-up. execSync's per-call timeout
// budget was getting eaten by PowerShell's own process-spawn + Add-Type JIT
// cost on the FIRST call of a run (confirmed 2026-07-07 — VSCode multi-window
// osClick timeouts under concurrent PowerShell spawns). Absorbing that cost
// once in beforeAll keeps every real step's timeout budget for the actual work.
function _warmupPowerShell() {
    try {
        execSync('powershell -NoProfile -Command "Add-Type -AssemblyName System.Windows.Forms"', { stdio: 'pipe', timeout: 30000 });
    } catch (e) {
        console.warn('[warmup] powershell warm-up failed (non-fatal):', String(e.message || e).substring(0, 100));
    }
}

// 프로그래매틱 스크롤 — osScroll.ps1이 추적된 top-level hwnd 아래에서 녹화된
// 컨테이너를 UIA로 찾아 ScrollPattern.Scroll()을 호출하고, ScrollPattern
// 미지원 레거시 컨트롤에만 hwnd-scoped WM_MOUSEWHEEL을 PostMessageW로
// 전달한다. 픽셀 좌표/물리 커서 주입 없음 (2026-07-10 좌표 실행 금지 지시).
function osScrollEl(hwnd, target, delta) {
    if (!hwnd) {
        _failures.push('osScroll:no-hwnd');
        console.warn('[osScroll] no window hwnd — cannot scroll without a window handle');
        return;
    }
    try {
        const selB64 = Buffer.from(JSON.stringify(target || {}), 'utf8').toString('base64');
        const out = execSync(
            \`powershell -NoProfile -File "\${join(__dirname, 'osScroll.ps1')}" -hwnd \${hwnd} -selB64 "\${selB64}" -delta \${delta}\`,
            { stdio: 'pipe', timeout: 20000 }
        ).toString().trim();
        if (out) console.log(out);
    } catch (e) {
        _failures.push('osScroll');
        console.warn('[osScroll] failed:', String((e.stderr && e.stderr.toString()) || e.message || e).substring(0, 200));
    }
}

// hwnd of the window this WinAppDriver session actually owns. Title-substring
// matching is non-deterministic whenever a same-titled window already exists
// (e.g. a FreeDM instance the user had open, alongside the fresh instance the
// session just launched, on a different monitor) — confirmed 2026-07-06: the
// session window landed on monitor 1 while the pre-existing user window sat
// on monitor 2, and title match grabbed whichever one it happened to find
// first, so clicks that were otherwise correctly computed missed entirely.
// getWindowHandle() returns the session's own NativeWindowHandle — unique,
// no title ambiguity — so every OS-level lookup below prefers it once known.
let _appHwnd = 0;

async function initAppHwnd() {
    try {
        const h = await browser.getWindowHandle();   // e.g. "0x00061D2C"
        _appHwnd = parseInt(h, 16);
        console.log(\`[hwnd] session window hwnd=\${_appHwnd} (0x\${_appHwnd.toString(16)})\`);
    } catch (e) {
        console.warn('[hwnd] getWindowHandle failed — falling back to title match:', String(e.message || e).substring(0, 100));
    }
}

// OS-level window activation via PowerShell user32.dll. Simple-mode tests
// never run launchApp's foreground/normalize step, so a freshly launched app
// can be spawned behind other windows or off-position — bring it forward
// before the first click so OS-level input actually reaches it.
function osActivate(titleLike) {
    try {
        const args = _appHwnd ? \`-hwnd \${_appHwnd}\` : \`-titleLike "\${titleLike}"\`;
        execSync(
            \`powershell -NoProfile -File "\${join(__dirname, 'osActivate.ps1')}" \${args}\`,
            { stdio: 'pipe', timeout: 15000 }
        );
    } catch (e) {
        console.warn('[osActivate] failed:', String(e.message || e).substring(0, 100));
    }
}

// Reads the window's CURRENT rect — by hwnd when the session's own window is
// known (deterministic), by title match otherwise. Geometry read only —
// used by normalizeWindowSimple to restore the recorded window position/size.
function _resolveWinRect(titleLike) {
    try {
        const args = _appHwnd ? \`-hwnd \${_appHwnd}\` : \`-titleLike "\${titleLike}"\`;
        const out = execSync(
            \`powershell -NoProfile -File "\${join(__dirname, 'osWindowRect.ps1')}" \${args}\`,
            { stdio: 'pipe', timeout: 15000 }
        ).toString().trim();
        const m = out.match(/(-?\\d+)\\s+(-?\\d+)\\s+(-?\\d+)\\s+(-?\\d+)/);
        if (m) return { left: +m[1], top: +m[2], width: +m[3], height: +m[4] };
    } catch (e) {
        console.warn('[winRect] failed:', String(e.message || e).substring(0, 100));
    }
    return null;
}

// Moves+resizes the session's window back to its recorded geometry. The
// freshly launched window can appear on a different monitor or at a
// different size than recording, which both skews DPI-scaling behavior and
// makes the recorded relX/relY (window-relative UI offsets) point at the
// wrong spot after the resulting UI reflow.
function normalizeWindowSimple(rect) {
    if (!_appHwnd || !rect) return;
    try {
        execSync(
            \`powershell -NoProfile -File "\${join(__dirname, 'osMoveWindow.ps1')}" -hwnd \${_appHwnd} -left \${rect.left} -top \${rect.top} -width \${rect.width} -height \${rect.height}\`,
            { stdio: 'pipe', timeout: 15000 }
        );
        const after = _resolveWinRect('');
        console.log('[normalize] window moved to', after);
    } catch (e) {
        _failures.push('normalize');
        console.warn('[normalize] failed:', String(e.message || e).substring(0, 100));
    }
}

// OS-level keystrokes into the focused control (SendKeys — keyboard
// injection, not coordinate execution). Fallback for edit controls whose
// element/value endpoint WinAppDriver rejects (confirmed 2026-07-08:
// Win11 Notepad's RichEditD2DPT Document control).
function osType(text) {
    try {
        const b64 = Buffer.from(text, 'utf8').toString('base64');
        execSync(
            \`powershell -NoProfile -File "\${join(__dirname, 'osType.ps1')}" -b64 "\${b64}"\`,
            { stdio: 'pipe', timeout: 15000 }
        );
    } catch (e) {
        _failures.push('osType');
        console.warn('[osType] failed:', String(e.message || e).substring(0, 100));
    }
}

// Fail-and-Recover popup dismissal (v2) — only called from _step() below,
// after a step has already failed, so the happy path pays zero cost.
// _appHwnd (resolved by initAppHwnd() in beforeAll) identifies the main
// app window deterministically for owner-PID scoping. Simple mode drives a
// single window (_appHwnd, already excluded as the ps1's $mainHwnd), so no
// -exclude list is needed here — see the session header's osDismissPopup.
function osDismissPopup() {
    try {
        const args = _appHwnd ? \`-hwnd \${_appHwnd}\` : '';
        const out = execSync(
            \`powershell -NoProfile -File "\${join(__dirname, 'osDismissPopup.ps1')}" \${args}\`,
            { stdio: 'pipe', timeout: 15000 }
        ).toString().trim();
        if (out.startsWith('DISMISSED')) { console.log('[popup]', out); return true; }
        return false;
    } catch (e) {
        console.warn('[osDismissPopup] failed:', String(e.message || e).substring(0, 100));
        return false;
    }
}

// ESC fallback — see OS_ESCAPE_PS1. Called only when osDismissPopup() found
// no known dismiss button (rename edit-box, open menu, etc).
function osEscape() {
    try {
        execSync(
            \`powershell -NoProfile -File "\${join(__dirname, 'osEscape.ps1')}"\`,
            { stdio: 'pipe', timeout: 15000 }
        );
        return true;
    } catch (e) {
        console.warn('[osEscape] failed:', String(e.message || e).substring(0, 100));
        return false;
    }
}

// Wraps a single replay step: on the happy path (no exception, no new
// _failures entry) this costs nothing extra. On failure, scans for and
// dismisses a known-shape popup that didn't exist at recording time (e.g.
// FDM's "file already exists"), then retries the step ONCE. If no dismiss
// button was found (e.g. an inline rename edit-box left open by a mistimed
// double-click), falls back to osActivate + ESC to back out of whatever
// modal input state grabbed focus, then retries once. If that still fails,
// the original failure/exception stands untouched (no false PASSED).
async function _step(label, fn) {
    console.log('[STEP] ' + label);
    const before = _failures.length;
    let err = null;
    try { await fn(); } catch (e) { err = e; }
    if (!err && _failures.length === before) return;
    const dismissed = osDismissPopup();
    if (dismissed) {
        _warnings.push('popup-dismissed:' + label);
    } else {
        osActivate('');
        osEscape();
        _warnings.push('esc-recovery:' + label);
    }
    _failures.length = before;
    await fn();
}

`;

// ── 세션 전환 헤더 (Electron / 다중 창 앱) ────────────────────────────────
const SESSION_HEADER = `import { execSync, spawn } from 'child_process';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));

// 주입/헬스 실패 수집 — 마지막에 실질 assert로 검증.
const _failures = [];
// 조용히 넘어갈 수 있는 성능/폴백 신호 — 실패는 아니지만 재생 품질 저하 가능성을 기록.
const _warnings = [];

// One-time PowerShell/.NET cold-start warm-up. execSync's per-call timeout
// budget was getting eaten by PowerShell's own process-spawn + Add-Type JIT
// cost on the FIRST call of a run (confirmed 2026-07-07 — VSCode multi-window
// osClick timeouts under concurrent PowerShell spawns). Absorbing that cost
// once in beforeAll keeps every real step's timeout budget for the actual work.
function _warmupPowerShell() {
    try {
        execSync('powershell -NoProfile -Command "Add-Type -AssemblyName System.Windows.Forms"', { stdio: 'pipe', timeout: 30000 });
    } catch (e) {
        console.warn('[warmup] powershell warm-up failed (non-fatal):', String(e.message || e).substring(0, 100));
    }
}

// 프로그래매틱 스크롤 — osScroll.ps1이 대상 창 hwnd 아래에서 녹화된 컨테이너를
// UIA로 찾아 ScrollPattern.Scroll()을 호출하고, ScrollPattern 미지원 레거시
// 컨트롤에만 hwnd-scoped WM_MOUSEWHEEL을 PostMessageW로 전달한다. 픽셀
// 좌표/물리 커서 주입 없음 (2026-07-10 좌표 실행 금지 지시).
function osScrollEl(hwnd, target, delta) {
    if (!hwnd) {
        _failures.push('osScroll:no-hwnd');
        console.warn('[osScroll] no window hwnd — cannot scroll without a window handle');
        return;
    }
    try {
        const selB64 = Buffer.from(JSON.stringify(target || {}), 'utf8').toString('base64');
        const out = execSync(
            \`powershell -NoProfile -File "\${join(__dirname, 'osScroll.ps1')}" -hwnd \${hwnd} -selB64 "\${selB64}" -delta \${delta}\`,
            { stdio: 'pipe', timeout: 20000 }
        ).toString().trim();
        if (out) console.log(out);
    } catch (e) {
        _failures.push('osScroll');
        console.warn('[osScroll] failed:', String((e.stderr && e.stderr.toString()) || e.message || e).substring(0, 200));
    }
}

// 스크롤 대상 창의 top-level hwnd 해석 — launchApp/_ensureDialog가 채운
// _hwndCache 우선, 없으면 EnumWindows 타이틀 매치로 1회 해석 후 캐시.
function _scrollHwnd(title) {
    _ensureDialog(title);
    if (_hwndCache[title]) return _hwndCache[title];
    const hs = _listWindowHwnds(title);
    if (hs.length) { _hwndCache[title] = hs[0]; return hs[0]; }
    return 0;
}

// Window session pool: title → Appium sessionId.
// global browser (Root session) used ONCE per new windowTitle for hwnd discovery;
// a fast scoped appTopLevelWindow session is then opened via Appium REST API.
let _APPIUM = 'http://127.0.0.1:4723';
const _sessionIds = {};
// hwnds whose scoped-session creation already failed once this run.
// appium-windows-driver spawns a NEW WinAppDriver.exe per session and WAD's
// POST /session can block indefinitely attaching to some dialog hwnds
// (confirmed 2026-07-09: "폴더 열기" attach timed out, then the Root-scan
// fallback re-derived the SAME hwnd and paid the full timeout again).
// Never retry a handle that failed — go straight to Root-session reuse.
// Keyed by hwnd, not title: a reopened dialog gets a fresh hwnd and is
// allowed a new attempt.
const _scopedFailHwnds = new Set();

// Hard timeout on every Appium HTTP call — WinAppDriver can block internally
// on a POST /session for a hwnd whose window is mid-close (confirmed
// 2026-07-09: STEP replay hung forever inside _createSession with no
// "failed" log ever printed, because the fetch neither resolved nor
// rejected). Without this, getWindowSession's existing catch-and-fall-back-
// to-Root-scan path never runs, since a promise that never settles never
// reaches a catch block.
async function _appiumFetch(path, opts = {}, timeoutMs = 20000) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
        return await fetch(\`\${_APPIUM}\${path}\`, { ...opts, signal: ctrl.signal });
    } catch (e) {
        if (e.name === 'AbortError') throw new Error(\`Appium request timed out after \${timeoutMs}ms: \${opts.method || 'GET'} \${path}\`);
        throw e;
    } finally {
        clearTimeout(timer);
    }
}

async function _appiumPost(path, body, timeoutMs = 20000) {
    const r = await _appiumFetch(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    }, timeoutMs);
    return (await r.json()).value;
}

async function _createSession(app) {
    const isHwnd = /^0x[0-9a-f]+$/i.test(app);
    // createSessionTimeout 15s caps the driver's internal WAD POST /session
    // retry loop; with WAD spawn (~3s) + /status poll (≤10s) that totals
    // ~28s worst case, so the 30s client abort below only fires when WAD is
    // truly wedged. Keeping server budget < client budget means Appium
    // settles definitively first — no more "client aborted at 20s while the
    // server went on to create an orphaned session/WAD process" race
    // (observed 2026-07-09).
    const cap = isHwnd
        ? { platformName: 'Windows', 'appium:automationName': 'Windows', 'appium:appTopLevelWindow': app, 'appium:newCommandTimeout': 60000, 'appium:createSessionTimeout': 15000 }
        : { platformName: 'Windows', 'appium:automationName': 'Windows', 'appium:app': app, 'appium:newCommandTimeout': 60000, 'appium:createSessionTimeout': 15000 };
    const v = await _appiumPost('/session', { capabilities: { alwaysMatch: cap } }, 30000);
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
    _ensureDialog(title);

    // Preferred path: Win32 EnumWindows (_listWindowHwnds) finds the TRUE
    // top-level window by title — no ambiguity with a child element's own
    // NativeWindowHandle (confirmed 2026-07-07: the desktop-UIA XPath scan
    // below matched a child control inside the "폴더 열기" dialog, whose
    // NativeWindowHandle Appium rejected with "not a top level window
    // handle", which silently degraded every subsequent getCenter() call to
    // garbage coordinates). _ensureDialog() above already resolved and
    // cached this hwnd (and normalized the window to its recorded rect), so
    // this is normally just a cache read.
    let hwndNum = _hwndCache[title];
    if (!hwndNum) {
        const hs = _listWindowHwnds(title);
        if (hs.length) { hwndNum = hs[0]; _hwndCache[title] = hwndNum; }
    }
    // Owned windows (native dialogs owned by the app's main window) can
    // never become scoped sessions — WAD rejects them, but only after the
    // full ~16s spawn/retry budget. Blacklist them up front (see _windowOwner).
    if (hwndNum && !_scopedFailHwnds.has(hwndNum)) {
        const ownerHwnd = _windowOwner(hwndNum);
        if (ownerHwnd) {
            console.log(\`[session] hwnd=0x\${hwndNum.toString(16)} owned by 0x\${ownerHwnd.toString(16)} — skipping scoped session (WAD rejects owned windows)\`);
            _scopedFailHwnds.add(hwndNum);
        }
    }
    if (hwndNum && !_scopedFailHwnds.has(hwndNum)) {
        const hwndHex = '0x' + hwndNum.toString(16);
        console.log(\`[session] top-level hwnd=\${hwndHex} for "\${title}" → scoped session\`);
        const t0 = Date.now();
        try {
            const sid = await _createSession(hwndHex);
            console.log(\`[session] scoped session on \${hwndHex} ready in \${Date.now() - t0}ms\`);
            // hwnd tracked here (not 0/Root) — a scoped session's element
            // /location returns coordinates relative to that window, not the
            // screen (confirmed 2026-07-08), so callers must add the live
            // window origin before feeding a point to osClick.
            _sessionIds[title] = { sid, rootElId: null, hwnd: hwndNum };
            return _sessionIds[title];
        } catch (e) {
            _scopedFailHwnds.add(hwndNum);
            console.warn(\`[session] scoped session on \${hwndHex} failed after \${Date.now() - t0}ms (\${e.message}) — falling back to desktop-UIA scan for "\${title}"\`);
        }
    }

    // Safety net: EnumWindows found nothing (e.g. an empty/dynamic dialog
    // title) — fall back to the original desktop-UIA XPath scan + Root
    // session reuse.
    console.log(\`[session] Root scan for: "\${title}"\`);
    const shortTitle = title.slice(0, 30).replace(/"/g, '');
    let hwnd = null;
    let matchedEl = null;
    for (const sel of [\`//*[@Name="\${title}"]\`, \`//*[contains(@Name,"\${shortTitle}")]\`]) {
        try {
            const el = await browser.$(sel);
            const raw = await el.getAttribute('NativeWindowHandle');
            const rawNum = parseInt(raw, 10);
            if (rawNum) { hwnd = '0x' + rawNum.toString(16); matchedEl = el; break; }
        } catch {}
    }
    const scanHwndNum = hwnd ? parseInt(hwnd, 16) : 0;
    // Same owned-window pre-check as the EnumWindows path above.
    if (scanHwndNum && !_scopedFailHwnds.has(scanHwndNum)) {
        const ownerHwnd = _windowOwner(scanHwndNum);
        if (ownerHwnd) {
            console.log(\`[session] hwnd=\${hwnd} owned by 0x\${ownerHwnd.toString(16)} — skipping scoped session (WAD rejects owned windows)\`);
            _scopedFailHwnds.add(scanHwndNum);
        }
    }
    if (scanHwndNum && !_scopedFailHwnds.has(scanHwndNum)) {
        console.log(\`[session] hwnd=\${hwnd} → scoped session\`);
        const t0 = Date.now();
        try {
            const sid = await _createSession(hwnd);
            console.log(\`[session] scoped session on \${hwnd} ready in \${Date.now() - t0}ms\`);
            // Scoped window's hwnd tracked — element /location is window-
            // relative here, same distinction as the EnumWindows path above.
            _sessionIds[title] = { sid, rootElId: null, hwnd: scanHwndNum };
            return _sessionIds[title];
        } catch (e) {
            _scopedFailHwnds.add(scanHwndNum);
            console.warn(\`[session] scoped session failed after \${Date.now() - t0}ms (\${e.message}) — reusing Root session for "\${title}"\`);
        }
    }
    // Root-session reuse (proven 2026-07-08): no new session, no WAD spawn.
    // Element lookups are scoped to the matched dialog element's subtree via
    // rootElId; hwnd 0 = /location is already screen-absolute. Deliberately
    // NOT _createSession('Root') — that would spawn yet another WinAppDriver
    // process with the same 30s-hang exposure as the scoped path.
    if (!hwnd) console.warn(\`[session] Window "\${title}" not found — falling back to Root\`);
    _warnings.push('session-fallback:' + title);
    _sessionIds[title] = { sid: browser.sessionId, rootElId: matchedEl ? matchedEl.elementId : null, hwnd: 0 };
    return _sessionIds[title];
}

// 스코프 세션(또는 Root 폴백의 rootElId 서브트리)에서 셀렉터로 요소를 찾아
// element id를 돌려준다 — 좌표 산출 없음. 클릭은 element/click 엔드포인트로
// UIA Invoke를 그대로 태운다 (2026-07-10 좌표 실행 금지).
async function _findElement(sid, rootElId, selector) {
    try {
        const raw = selector.replace(/^['"]|['"]$/g, '');
        const using = raw.startsWith('~') ? 'accessibility id' : 'xpath';
        const value = raw.startsWith('~') ? raw.slice(1) : raw;
        const path = rootElId
            ? \`/session/\${sid}/element/\${rootElId}/element\`
            : \`/session/\${sid}/element\`;
        const el = await _appiumPost(path, { using, value });
        if (!el) return null;
        return el.ELEMENT || el['element-6066-11e4-a52e-4f735466cecf'] || null;
    } catch (e) {
        console.warn('[findElement] lookup failed:', String(e.message || e).substring(0, 120));
        return null;
    }
}

// Diagnostic for a final row-lookup failure: dump the row names UIA actually
// exposes under the dialog RIGHT NOW. Distinguishes list virtualization (the
// target row exists but isn't UIA-exposed until scrolled into view) from a
// name mismatch (row exposed under a different Name) from a dialog that never
// repopulated — the three candidate causes that can't be told apart from a
// bare no-such-element (2026-07-09: STEP 6 "hansung" lookup failed with no
// way to see what the list actually contained).
async function _dumpVisibleRows(s) {
    try {
        const path = s.rootElId
            ? \`/session/\${s.sid}/element/\${s.rootElId}/elements\`
            : \`/session/\${s.sid}/elements\`;
        // Two queries, not an XPath union — WinAppDriver's XPath subset does
        // not reliably support "|".
        let els = await _appiumPost(path, { using: 'xpath', value: '//ListItem' });
        if (!Array.isArray(els) || !els.length) els = await _appiumPost(path, { using: 'xpath', value: '//TreeItem' });
        if (!Array.isArray(els)) { console.warn('[getCenter-diag] row query returned no array'); return; }
        const names = [];
        for (const el of els.slice(0, 20)) {
            const elId = el.ELEMENT || el['element-6066-11e4-a52e-4f735466cecf'];
            if (!elId) continue;
            try {
                const r = await (await _appiumFetch(\`/session/\${s.sid}/element/\${elId}/attribute/Name\`)).json();
                if (typeof r.value === 'string') names.push(r.value);
            } catch {}
        }
        console.warn(\`[getCenter-diag] UIA-exposed rows (\${els.length} total): \${names.join(' | ')}\`);
    } catch (e) {
        console.warn('[getCenter-diag] dump failed:', String(e.message || e).substring(0, 100));
    }
}

// Named-element lookup with condition polling (waitUntil-style — no fixed
// pause). A navigation click (e.g. selecting a drive in the "폴더 열기" nav
// pane) repopulates the dialog's file list ASYNCHRONOUSLY; a zero-wait lookup
// would give up before the list had refreshed (confirmed 2026-07-09: STEP 6
// "hansung" no-such-element twice in a row). Polls once per second up to
// timeoutMs; halfway through it invalidates the cached session/rootElId once
// in case the cached dialog element itself went stale. Returns { elId, s }:
// elId null on timeout (after dumping visible rows for diagnosis).
async function _findScoped(title, selector, timeoutMs = 8000) {
    const deadline = Date.now() + timeoutMs;
    const refreshAt = Date.now() + timeoutMs / 2;
    let refreshed = false;
    for (;;) {
        const s = await getWindowSession(title);
        // Dialog window itself wasn't found (no hwnd, no matched element):
        // a lookup would scan the ENTIRE desktop tree from Root at 10s+ per
        // call. Drop the useless cache entry and fail fast.
        if (!s.hwnd && !s.rootElId) {
            delete _sessionIds[title];
            console.warn(\`[findScoped] window "\${title}" not found — failing fast\`);
            return { elId: null, s };
        }
        const elId = await _findElement(s.sid, s.rootElId, selector);
        if (elId) return { elId, s };
        if (Date.now() >= deadline) {
            await _dumpVisibleRows(s);
            return { elId: null, s };
        }
        if (!refreshed && Date.now() >= refreshAt) {
            refreshed = true;
            delete _sessionIds[title];
        }
        await new Promise(r => setTimeout(r, 1000));
    }
}

// XPath-only click in the window's own session context (HWND 세그먼트).
// element/click = UIA Invoke/기본 액션 — 창이 이동/리사이즈돼도 무관하고
// 좌표는 어디에도 없다. doubleClick은 같은 요소에 클릭 2회 (WinAppDriver에
// 요소 단위 doubleclick 엔드포인트가 없음 — 좌표 기반 moveto/doubleclick은
// 금지 대상이라 쓰지 않는다). 실패는 _failures로 기록되어 _step()의
// Fail-and-Recover(팝업 해제 후 1회 재시도)를 태운 뒤 최종 FAIL로 남는다.
async function _clickScoped(title, selector, dbl = false) {
    const { elId, s } = await _findScoped(title, selector);
    if (!elId) {
        _failures.push('click-not-found:' + String(selector).substring(0, 60));
        return;
    }
    await _appiumPost(\`/session/\${s.sid}/element/\${elId}/click\`, {});
    if (dbl) await _appiumPost(\`/session/\${s.sid}/element/\${elId}/click\`, {});
}

// Returns true on success, false on failure (never pushes to _failures itself
// — WinAppDriver's element/value endpoint outright rejects some native edit
// controls (confirmed 2026-07-08: Win11 Notepad's RichEditD2DPT Document
// control), so the caller falls back to OS-level typing instead of failing).
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
        return true;
    } catch (e) { console.warn('[type] scoped sendKeys failed:', String(e.message || e).substring(0, 100)); return false; }
}

// ── HWND 추적 (창 세그먼팅) ────────────────────────────────────────────────
// Title fragment → hwnd of the window launchApp actually created for this run.
// Populated by launchApp via baseline/diff (see below). Once set, every
// _resolveWinRect/normalizeWindow call for that fragment targets this exact
// hwnd instead of re-searching by title — title substrings are NOT unique
// (e.g. any pre-existing "...- Visual Studio Code" window also matches), and
// replaying clicks against whichever window happens to match/be-foreground
// can land recorded titlebar clicks (including close) on the WRONG window.
const _hwndCache = {};

// Main app window title-fragment, set once in beforeAll (see generateWdio's
// beforeHook) — lets osDismissPopup() identify the main window/PID for
// owner-PID scoping without every call site having to pass it in.
let _mainTitleFrag = '';

// Native (non-Electron) dialog title → its recorded window geometry, set
// once in beforeAll (see generateWdio's beforeHook). _ensureDialog() uses
// this to normalize a dialog to the position/size it was RECORDED at (e.g.
// on a specific monitor in a multi-monitor setup) the first time replay
// touches it — without this, a dialog's rel-offsets (relX/relY captured
// against the recording-time window) point at the wrong pixels once the
// dialog opens at a different position (confirmed 2026-07-07: VSCode's
// "폴더 열기" dialog opened on monitor 1 while recording was done on
// monitor 2, so every rel-offset click/scroll landed off-window).
let _dialogRects = {};
const _dialogsReady = new Set();

// Resolves a dialog's TRUE top-level hwnd via Win32 EnumWindows (title
// substring match — see _listWindowHwnds), then normalizes it to its
// recorded rect and brings it to the foreground, ONCE per title. A no-op
// for the main Electron window or any title not in _dialogRects (both
// _resolveWinRect/getWindowSession callers pass titles indiscriminately —
// this function is the single gate deciding whether a given title is a
// "dialog that needs normalizing" at all).
function _ensureDialog(title) {
    if (!title || !(title in _dialogRects) || _dialogsReady.has(title)) return;
    _dialogsReady.add(title);
    const hs = _listWindowHwnds(title);
    if (!hs.length) {
        console.warn(\`[dialog] "\${title}" not found by EnumWindows — rel-offsets may be unreliable\`);
        return;
    }
    _hwndCache[title] = hs[0];
    const r = _dialogRects[title];
    normalizeWindow(title, r.left, r.top, r.width, r.height);
    osActivate(title, hs[0]);
    console.log(\`[dialog] "\${title}" hwnd=\${hs[0]} normalized to\`, r);
}

function _listWindowHwnds(frag) {
    if (!frag) return [];
    try {
        const out = execSync(
            \`powershell -NoProfile -File "\${join(__dirname, 'osWindowRect.ps1')}" -titleLike "\${frag}" -listOnly\`,
            { stdio: 'pipe', timeout: 15000 }
        ).toString().trim();
        if (!out) return [];
        return out.split(/\\r?\\n/).map(s => s.trim()).filter(Boolean).map(Number);
    } catch {
        return [];
    }
}

// Owner hwnd of a window (0 = unowned). WinAppDriver rejects OWNED windows
// as appTopLevelWindow ("X is not a top level window handle") only after
// appium has burned its full WAD-spawn + retry budget — ~16s per attempt
// (confirmed 2026-07-09: the "폴더 열기" dialog, owned by the VSCode main
// window, cost 16226ms before failing). One cheap PS call up front lets
// getWindowSession skip the doomed attempt entirely. Returns 0 on any
// error so callers fall through to the normal attempt-then-blacklist path.
function _windowOwner(hwndNum) {
    try {
        const out = execSync(
            \`powershell -NoProfile -File "\${join(__dirname, 'osWindowRect.ps1')}" -hwnd \${hwndNum} -ownerOnly\`,
            { stdio: 'pipe', timeout: 15000 }
        ).toString().trim();
        return Number(out) || 0;
    } catch {
        return 0;
    }
}

function _resolveWinRect(frag) {
    if (!frag) return null;
    const hwnd = _hwndCache[frag];
    try {
        const args = hwnd ? \`-hwnd \${hwnd}\` : \`-titleLike "\${frag}"\`;
        const out = execSync(
            \`powershell -NoProfile -File "\${join(__dirname, 'osWindowRect.ps1')}" \${args}\`,
            { stdio: 'pipe', timeout: 15000 }
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
            { stdio: 'pipe', timeout: 15000 }
        );
    } catch (e) {
        _failures.push('moveWindow');
        console.warn('[moveWindow] failed:', String(e.message || e).substring(0, 100));
    }
}

// Bring a dialog (or, if hwnd is unknown, anything matching titleLike) to
// the foreground — same OS-level foreground-lock bypass as SIMPLE_HEADER's
// osActivate, but hwnd-first since _ensureDialog always already has one.
function osActivate(titleLike, hwnd) {
    try {
        const args = hwnd ? \`-hwnd \${hwnd}\` : \`-titleLike "\${titleLike}"\`;
        execSync(
            \`powershell -NoProfile -File "\${join(__dirname, 'osActivate.ps1')}" \${args}\`,
            { stdio: 'pipe', timeout: 15000 }
        );
    } catch (e) {
        console.warn('[osActivate] failed:', String(e.message || e).substring(0, 100));
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
    // A content-dependent recorded title (e.g. Notepad's "*d - 메모장" — the
    // dirty-flag/filename prefix only exists once text has been typed) never
    // matches the fresh, clean window this launch creates ("제목 없음 - 메모장"),
    // so the frag-diff below never fires and every later hwnd lookup falls
    // through to a Root scan (confirmed 2026-07-08). Also snapshot/match on
    // the stable tail token after the last " - " (app name, e.g. "메모장") as
    // a fallback identity. No-op when titleFrag has no " - " (FDM's "Free
    // Download Manager", VSCode's winFrag) since tailFrag === titleFrag then.
    const tailFrag = (titleFrag || '').split(' - ').pop() || titleFrag;
    const baselineTail = tailFrag !== titleFrag ? new Set(_listWindowHwnds(tailFrag)) : null;
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
    let poll = 0;
    while (Date.now() < deadline) {
        poll++;
        const matched = _listWindowHwnds(titleFrag);
        if (titleFrag && !_hwndCache[titleFrag]) {
            const fresh = matched.find(h => !baseline.has(h));
            if (fresh) {
                _hwndCache[titleFrag] = fresh;
                console.log(\`[launch] tracking new window hwnd=\${fresh}\`);
            } else if (baselineTail) {
                const freshTail = _listWindowHwnds(tailFrag).find(h => !baselineTail.has(h));
                if (freshTail) {
                    _hwndCache[titleFrag] = freshTail;
                    console.log(\`[launch] adopted new window hwnd=\${freshTail} via tail fragment "\${tailFrag}" (recorded title "\${titleFrag}" not present at launch)\`);
                }
            }
        }
        // A matched window with width/height 0 is a not-yet-rendered
        // placeholder (Electron/UWP frame created before content loads,
        // same hwnd, resized later) — treat it as "not found yet" and keep
        // polling instead of normalizing/replaying against a window that
        // isn't really there, which sent every later osClick to whatever
        // was actually on screen underneath (e.g. the desktop).
        const liveRect = _resolveWinRect(titleFrag);
        // DIAGNOSTIC (temporary): trace why [launch] window-detection times
        // out — remove once root cause of the Claude Desktop timeout is found.
        console.log(\`[launch-diag] poll=\${poll} titleFrag=\${JSON.stringify(titleFrag)} baseline=[\${[...baseline]}] matched=[\${matched}] hwndCache=\${_hwndCache[titleFrag] ?? 'none'} liveRect=\${JSON.stringify(liveRect)}\`);
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

// OS 키 주입(SendKeys) — 좌표 실행이 아닌 키보드 폴백. _typeScoped가
// 거부되는 컨트롤(예: RichEditD2DPT) 및 Electron 포커스 입력용.
function osType(text) {
    try {
        const b64 = Buffer.from(text, 'utf8').toString('base64');
        execSync(
            \`powershell -NoProfile -File "\${join(__dirname, 'osType.ps1')}" -b64 "\${b64}"\`,
            { stdio: 'pipe', timeout: 15000 }
        );
    } catch (e) {
        _failures.push('osType');
        console.warn('[osType] failed:', String(e.message || e).substring(0, 100));
    }
}

// Fail-and-Recover popup dismissal (v2) — only called from _step() below,
// after a step has already failed, so the happy path pays zero cost.
// Prefers the tracked hwnd for the main app window (_hwndCache[_mainTitleFrag],
// set by launchApp) for deterministic owner-PID scoping; falls back to a
// title-substring match when no hwnd was tracked (e.g. app already running).
// Every hwnd the replay itself is driving (main window + dialogs tracked in
// _hwndCache) is passed as -exclude — a "recovery" that closes the very
// dialog the failed step is about to retry against guarantees the retry
// fails too (confirmed 2026-07-09: dismisser closed the "폴더 열기" flow's
// window, then the retry's Root scan found nothing and the run stalled).
function osDismissPopup() {
    try {
        const hwnd = _hwndCache[_mainTitleFrag];
        let args = hwnd ? \`-hwnd \${hwnd}\` : (_mainTitleFrag ? \`-titleLike "\${_mainTitleFrag}"\` : '');
        const tracked = [...new Set(Object.values(_hwndCache))].filter(Boolean);
        if (tracked.length) args += \` -exclude "\${tracked.join(',')}"\`;
        const out = execSync(
            \`powershell -NoProfile -File "\${join(__dirname, 'osDismissPopup.ps1')}" \${args}\`,
            { stdio: 'pipe', timeout: 15000 }
        ).toString().trim();
        if (out.startsWith('DISMISSED')) { console.log('[popup]', out); return true; }
        return false;
    } catch (e) {
        console.warn('[osDismissPopup] failed:', String(e.message || e).substring(0, 100));
        return false;
    }
}

// ESC fallback — see OS_ESCAPE_PS1. Called only when osDismissPopup() found
// no known dismiss button (rename edit-box, open menu, etc).
function osEscape() {
    try {
        execSync(
            \`powershell -NoProfile -File "\${join(__dirname, 'osEscape.ps1')}"\`,
            { stdio: 'pipe', timeout: 15000 }
        );
        return true;
    } catch (e) {
        console.warn('[osEscape] failed:', String(e.message || e).substring(0, 100));
        return false;
    }
}

// Wraps a single replay step: on the happy path (no exception, no new
// _failures entry) this costs nothing extra. On failure, scans for and
// dismisses a known-shape popup that didn't exist at recording time (e.g.
// FDM's "file already exists"), then retries the step ONCE. If no dismiss
// button was found (e.g. an inline rename edit-box left open by a mistimed
// double-click), falls back to osActivate + ESC to back out of whatever
// modal input state grabbed focus, then retries once. If that still fails,
// the original failure/exception stands untouched (no false PASSED).
async function _step(label, fn) {
    console.log('[STEP] ' + label);
    const before = _failures.length;
    let err = null;
    try { await fn(); } catch (e) { err = e; }
    if (!err && _failures.length === before) return;
    const dismissed = osDismissPopup();
    if (dismissed) {
        _warnings.push('popup-dismissed:' + label);
    } else {
        osActivate('', _hwndCache[_mainTitleFrag]);
        osEscape();
        _warnings.push('esc-recovery:' + label);
    }
    _failures.length = before;
    await fn();
}

`;

function generateWdio(strategy, appName, eventList, useSession, exePath) {
  const base     = toPascal(appName);
  const suffix   = strategy === 'id' ? 'ById' : 'ByClass';
  const testName = `${base}Test${suffix}`;
  const pageName = `${base}Page${suffix}`;
  const selFn    = strategy === 'id' ? wdioSelectorById : wdioSelectorByClass;

  const filtered     = dedupeDoubleClicks(filterEvents(eventList));
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

  // 같은 창(rootHwndHex)의 이벤트는 최초 관측 제목으로 통일 — 콘텐츠에 따라 바뀌는
  // 제목(예: 메모장의 "*a - 메모장" → "*asdf - 메모장")이 같은 창을 여러 개의
  // "다이얼로그"로 쪼개, 재생 시점엔 이미 존재하지 않는 낡은 제목을 getWindowSession이
  // 찾다 실패하는 문제를 막는다. 재생도 같은 텍스트를 입력하므로 창의 최초 제목은
  // 첫 조회 시점엔 유효하고, 이후엔 _sessionIds/_hwndCache 캐시로 계속 추적된다.
  const _firstTitleOfRoot = {};
  for (const e of filtered) {
    const r = e.rootHwndHex, t = e.element?.windowTitle || '';
    if (r && t && !(r in _firstTitleOfRoot)) _firstTitleOfRoot[r] = t;
  }
  // rootHwndHex가 없는(구버전 캡처/좌표 폴백) 이벤트의 raw title이 알려진 그룹의
  // 첫 제목과도 안 맞으면, 그건 새 창이 아니라 메인 창 제목이 다시 바뀐 순간일
  // 가능성이 높다(confirmed 2026-07-08: Notepad "*asdf - 메모장"이 rootHwndHex 없이
  // 캡처되어 별도 유령 다이얼로그로 취급되고, 재생 시 그 제목의 창이 없어 실패).
  // 직전 이벤트가 풀린 그룹 제목을 그대로 승계해 같은 창으로 묶는다.
  const _knownFirstTitles = new Set(Object.values(_firstTitleOfRoot));
  const _groupTitles = [];
  {
    let last = '';
    filtered.forEach(e => {
      let t;
      if (e.rootHwndHex && _firstTitleOfRoot[e.rootHwndHex]) {
        t = _firstTitleOfRoot[e.rootHwndHex];
      } else {
        const raw = e.element?.windowTitle || '';
        if (raw && _knownFirstTitles.has(raw)) t = raw;
        else if (!e.rootHwndHex && raw) t = last || raw;
        else t = raw;
      }
      if (t) last = t;
      _groupTitles.push(t);
    });
  }
  const groupTitle = (e, i) => _groupTitles[i];

  // 네이티브(비-Electron) 다이얼로그 title → 녹화 시점 창 기하. 재생 중 _ensureDialog가
  // 각 다이얼로그를 이 위치로 최초 1회 정규화한다 — 안 하면 rel 오프셋(relX/relY)이
  // 녹화 당시 창 기준이라 다이얼로그가 다른 위치/모니터에서 열릴 경우 어긋난다
  // (confirmed 2026-07-07: VSCode "폴더 열기"가 녹화 모니터와 다른 위치에서 열려
  // 스크롤/클릭이 전부 빗나감). "coordinate" 폴백 이벤트(windowTitle 빈 문자열)는
  // 직전 실제 title을 승계해 같은 다이얼로그로 묶는다 — 위 lastWinTitle 승계 로직과 동일 가정.
  const dialogRects = {};
  {
    let _lastT = '';
    filtered.forEach((e, i) => {
      const gt = groupTitle(e, i);
      const t = gt || _lastT;
      if (gt) _lastT = gt;
      if (!t || (winFragOk && t.includes(winFrag))) return;   // 메인 Electron 창 제외
      if (t in dialogRects) return;
      if (Number.isInteger(e.winLeft) && Number.isInteger(e.winTop) &&
          Number.isInteger(e.winWidth) && Number.isInteger(e.winHeight)) {
        dialogRects[t] = { left: e.winLeft, top: e.winTop, width: e.winWidth, height: e.winHeight };
      }
    });
  }

  // windowTitle이 빈 문자열로 캡처된 이벤트(agent.py가 element.windowTitle을
  // 못 채운 경우)는 직전에 관측된 실제 windowTitle을 이어받아 같은 창 세그먼트로
  // 묶는다 — 같은 다이얼로그 안에서 연속 캡처되므로 안전한 가정이다.
  let lastWinTitle = '';

  filtered.forEach((e, i) => {
    const stepNum  = i + 1;
    const sel      = selFn(e.element);
    const isEdit   = EDITABLE_CONTROL_TYPES.has(e.element?.controlType);
    const winTitle = groupTitle(e, i);
    const relTitle    = winTitle || lastWinTitle;
    if (winTitle) lastWinTitle = winTitle;
    const electronCtx = winFragOk && relTitle.includes(winFrag);

    if (e.action === 'scroll') {
      // 프로그래매틱 스크롤 (2026-07-10 지시): 캡처 시점에 agent.py가 기록한
      // 스크롤 컨테이너(scrollTarget — ScrollPattern 보유 조상)를 재생 시점에
      // 대상 창 hwnd 아래에서 다시 찾아 ScrollPattern.Scroll() / PostMessage
      // 휠로 스크롤한다. e.delta = 노치 수(agent가 합산), 픽셀 좌표 미사용.
      // 구버전 캡처(scrollTarget 없음)는 이벤트 요소 셀렉터로 폴백 — 그래도
      // 좌표는 쓰지 않는다 (셀렉터마저 없으면 창 루트에서 스크롤 가능 요소 탐색).
      const notches = Number.isFinite(e.delta) ? e.delta : (parseInt(e.value, 10) || 0);
      const st = e.scrollTarget || {};
      const el = e.element || {};
      const target = {
        automationId: st.automationId || el.automationId || '',
        className: st.className || el.className || '',
        name: st.name || el.name || '',
      };
      if (useSession) {
        pageMethods.push(
`    async scroll${stepNum}() {
        osScrollEl(_scrollHwnd('${escapeStr(relTitle)}'), ${JSON.stringify(target)}, ${notches});
    }`
        );
      } else {
        pageMethods.push(
`    async scroll${stepNum}() {
        osScrollEl(_appHwnd, ${JSON.stringify(target)}, ${notches});
    }`
        );
      }
      testSteps.push(
`            await _step('${stepNum}:scroll delta=${notches}', () => page.scroll${stepNum}());`
      );
      return;
    }

    if (e.action === 'drag' || e.action === 'rightClick') {
      // scope-out (2026-07-10 스테이크홀더 지시): 이벤트 스코프는
      // Click / Type / DoubleClick / Scroll 4종. drag/rightClick은 캡처는
      // 유지하되 재생하지 않는다 — 좌표 실행 전면 금지로 대체 경로도 없음.
      testSteps.push(
`            // [STEP ${stepNum}] ${e.action} scope-out — replay skipped (event scope: Click/Type/DoubleClick/Scroll, 2026-07-10)`
      );
      return;
    }

    if (e.action === 'type' && isEdit) {
      if (useSession && electronCtx) {
        // Electron 입력 → UIA 세션 조회(45초 실패 경로) 제거, OS 키 주입
        // (SendKeys — 키보드 폴백, 좌표 실행 아님).
        pageMethods.push(
`    async type${stepNum}(value) {
        osType(value);
    }`
        );
      } else if (useSession && relTitle) {
        const elSel = sel || `'//*[@Name="${escapeAttr(e.element?.name)}"]'`;
        const relTitleArg = escapeStr(relTitle);
        pageMethods.push(
`    async type${stepNum}(value) {
        const s = await getWindowSession('${relTitleArg}');
        const ok = await _typeScoped(s.sid, s.rootElId, ${elSel}, value);
        if (!ok) {
            console.warn('[type${stepNum}] scoped sendKeys failed — falling back to OS-level typing');
            osActivate('${relTitleArg}', _hwndCache['${relTitleArg}']);
            osType(value);
        }
    }`
        );
      } else {
        const elSel = sel || `'//*[@Name="${escapeAttr(e.element?.name)}"]'`;
        pageMethods.push(
`    async type${stepNum}(value) {
        try {
            const el = await browser.$(${elSel});
            await el.waitForExist({ timeout: 8000 });
            await el.addValue(value);
        } catch (e) {
            // WinAppDriver's element/value endpoint rejects some native edit
            // controls outright (confirmed 2026-07-08: Win11 Notepad's
            // RichEditD2DPT Document control returns "unknown error" in
            // ~15ms, even after waitForExist/elementClear succeeded) — no
            // amount of retrying helps, so fall back to real OS-level key
            // injection instead of failing the step.
            console.warn('[type${stepNum}] element sendKeys failed — falling back to OS-level typing:', String(e.message || e).substring(0, 100));
            osActivate('');
            osType(value);
        }
    }`
        );
      }
      testSteps.push(
`            await _step('${stepNum}:type ${escapeStr(e.value)}', () => page.type${stepNum}('${escapeStr(e.value)}'));`
      );
    } else if (e.action === 'type') {
      testSteps.push(`            // [STEP ${stepNum}] skip type on non-editable element`);
    } else {
      // Click / DoubleClick — XPath-only (2026-07-10: 좌표 재생 전면 금지).
      // doubleClick 구성 클릭(agent가 동일 좌표에 별도로 방출하는 선행 click들)은
      // dedupeDoubleClicks()가 filtered 생성 시점에 이미 걷어냈으므로, 여기
      // 도달하는 doubleClick은 항상 단일 스텝이다.
      const dbl = e.action === 'doubleClick';
      if (!sel) {
        // 셀렉터도 anchor도 없는 이벤트(예: light-dismiss 오버레이 레이스)는
        // 재생 수단이 없다 — 조용히 건너뛰면 이후 플로우가 어긋난 채 PASSED로
        // 남을 수 있으므로(거짓 통과), 명시적 실패로 기록한다.
        testSteps.push(
`            // [STEP ${stepNum}] ${e.action}: no selector/anchor captured — coordinate replay is forbidden (2026-07-10)
            _failures.push('${stepNum}:${e.action}:no-selector');`
        );
        return;
      }
      if (useSession) {
        // HWND 세그먼트: 이 이벤트가 속한 창의 세션 컨텍스트로 전환한 뒤
        // (getWindowSession — 필요 시 scoped 세션 생성/Root 폴백) 그 안에서
        // 셀렉터를 라이브 조회해 element/click(UIA Invoke)한다.
        pageMethods.push(
`    async click${stepNum}() {
        await _clickScoped('${escapeStr(relTitle)}', ${sel}${dbl ? ', true' : ''});
    }`
        );
      } else {
        // Simple mode: pure XPath click via el.click() (UIA Invoke) — verified
        // on Calculator (session 13). NOTE: no moveTo() before click() —
        // WinAppDriver's Actions endpoint rejects mouse pointerMove ("only pen
        // and touch pointer input source types are supported"). A lookup/click
        // failure propagates to _step()'s Fail-and-Recover (popup dismiss +
        // one retry) and then fails the test — no coordinate fallback exists.
        const secondClick = dbl ? `\n        await el.click();` : '';
        pageMethods.push(
`    async click${stepNum}() {
        const el = await browser.$(${sel});
        await el.waitForExist({ timeout: 8000 });
        await el.click();${secondClick}
    }`
        );
      }
      testSteps.push(
`            await _step('${stepNum}:${e.action} ${escapeStr(e.element?.name || '')}', () => page.click${stepNum}());`
      );
    }
  });

  // OS 주입(osClick/osScroll/type) 실패를 실제 assert로 검증(거짓 통과 방지).
  // SIMPLE_HEADER/SESSION_HEADER 둘 다 _failures/_warnings를 갖는다.
  const assertLine = `            if (_warnings.length) console.warn('[replay-warnings]', _warnings);
            expect(_failures).toEqual([]);`;

  const header   = useSession ? SESSION_HEADER : SIMPLE_HEADER;
  // launchApp identifies the NEW window this run creates (baseline/diff on
  // titleFrag) so replay targets it instead of a stale leftover window from
  // a previous recording/run — previously this only fired for Electron
  // (winFrag), so any native multi-window app (e.g. Notepad) replayed
  // against whatever window with a matching title happened to still be
  // open, title-content drift and all (confirmed 2026-07-08: Notepad replay
  // reused the exact leftover hwnd from the prior recording session).
  const launchFrag = winFragOk ? winFrag : groupTitle(filtered[0] || {}, 0);
  const launchCall = (useSession && exePath && launchFrag)
    ? `        await launchApp(${JSON.stringify(exePath)}, ${JSON.stringify(newWindowArgsFor(exePath))}, ${JSON.stringify(launchFrag)}, ${JSON.stringify(recordedRect)});\n`
    : '';

  // Simple mode has no launchApp/foreground step of its own — the freshly
  // launched window can end up behind other windows, so bring it forward
  // before the first click (see osActivate in SIMPLE_HEADER).
  const simpleWinTitle = !useSession
    ? (filtered.find(e => e.element?.windowTitle)?.element?.windowTitle || '')
    : '';
  const activateStep = simpleWinTitle
    ? `            osActivate(${JSON.stringify(simpleWinTitle)});\n`
    : '';
  // Best available fragment identifying the main app window, for
  // osDismissPopup()'s owner-PID scoping (session mode only — simple mode
  // uses _appHwnd directly, already resolved by initAppHwnd()).
  const mainTitleFrag = useSession ? launchFrag : '';

  const beforeHook = useSession ? `
    beforeAll(async () => {
        _warmupPowerShell();
        _mainTitleFrag = ${JSON.stringify(mainTitleFrag)};
        _dialogRects = ${JSON.stringify(dialogRects)};
        const { hostname, port } = browser.options;
        _APPIUM = \`http://\${hostname || '127.0.0.1'}:\${port || 4723}\`;
        console.log(\`[session] Appium endpoint resolved to \${_APPIUM}\`);
${launchCall}    });
` : '';

  const afterHook = useSession ? `
    afterAll(async () => {
        for (const { sid } of Object.values(_sessionIds)) {
            if (sid === browser.sessionId) continue;
            try { await _appiumFetch(\`/session/\${sid}\`, { method: 'DELETE' }, 5000); } catch {}
        }
    });
` : '';

  // Identify the session's own window by hwnd (deterministic — title match
  // can grab a pre-existing same-titled window instead, see osActivate in
  // SIMPLE_HEADER) and normalize it back to the recorded position/size
  // before the first step.
  const simpleBeforeHook = !useSession ? `
    beforeAll(async () => {
        _warmupPowerShell();
        await initAppHwnd();
        normalizeWindowSimple(${JSON.stringify(recordedRect)});
    });
` : '';

  return header + `class ${pageName} {
${pageMethods.join('\n\n')}
}

describe('${testName}', () => {${beforeHook}${afterHook}${simpleBeforeHook}
    it('should replay recorded flow', async () => {
        const page = new ${pageName}();

${activateStep}${testSteps.join('\n')}

${assertLine}
    });
});
`;
}

// ── WdIO conf 생성 ───────────────────────────────────────────────────────────

// Windows ships some classic exe names as thin redirector stubs that launch,
// hand off to a packaged (MSIX) app hosted by a DIFFERENT process, and exit —
// e.g. calc.exe on this build. appium:app-by-path session creation looks for
// a window owned by the PID it launched, never finds one owned by the real
// host process, and fails with "Failed to locate opened application window"
// (reproduced 2026-07-06). Route known stubs to their real AUMID instead —
// this is the same fix already applied to the Calculator preset in
// ControlPanel.jsx, just also applied to whatever exePath was actually
// recorded with (including a manually-typed calc.exe path).
const KNOWN_UWP_STUB_AUMID = {
  'calc.exe': 'Microsoft.WindowsCalculator_8wekyb3d8bbwe!App',
};

function resolveAppCap(exePath) {
  const isUwp = (exePath || '').includes('!');
  if (isUwp) return exePath;
  const base = path.basename(exePath || '').toLowerCase();
  if (KNOWN_UWP_STUB_AUMID[base]) return KNOWN_UWP_STUB_AUMID[base];
  return (exePath || '').replace(/\\/g, '\\\\');
}

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
  const appCap = resolveAppCap(exePath);
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

// 더 이상 생성하지 않는 좌표 주입 헬퍼(2026-07-10 좌표 실행 전면 금지) — 이전
// 세대 generate가 남긴 파일이 폴더에 있으면 재생성 시점에 지운다.
const OBSOLETE_FILES = ['osClick.ps1', 'osDrag.ps1'];

function saveFiles(files, dir) {
  const savedPaths = [];
  let saveError;
  try {
    fs.mkdirSync(dir, { recursive: true });
    for (const name of OBSOLETE_FILES) {
      const fp = path.join(dir, name);
      if (fs.existsSync(fp)) {
        fs.unlinkSync(fp);
        console.log('[saveFiles] removed obsolete', name);
      }
    }
    for (const f of files) {
      const fp = path.join(dir, f.filename);
      // powershell -File reads a BOM-less file as the system ANSI codepage
      // (CP949 here), not UTF-8 — mangling every Korean literal (e.g.
      // osDismissPopup.ps1's '취소'/'아니요'/'닫기'/'확인'/'예' button names)
      // into mojibake that breaks the PS parser outright (confirmed
      // 2026-07-08: 2 parse errors, "Unexpected token" from a swallowed
      // closing quote). A leading UTF-8 BOM makes PowerShell read it
      // correctly; harmless no-op for the ASCII-only .ps1 helpers.
      const content = f.filename.endsWith('.ps1') && !f.content.startsWith('﻿')
        ? '﻿' + f.content
        : f.content;
      fs.writeFileSync(fp, content, 'utf8');
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
    // 좌표 주입 헬퍼(osClick.ps1/osDrag.ps1)는 더 이상 생성하지 않는다
    // (2026-07-10 좌표 실행 전면 금지). osScroll.ps1은 ScrollPattern +
    // PostMessage 휠 폴백의 프로그래매틱 스크롤로 교체됨.
    const scrollPs1File = { filename: 'osScroll.ps1', content: OS_SCROLL_PS1 };
    const winRectPs1File = { filename: 'osWindowRect.ps1', content: OS_WINRECT_PS1 };
    const moveWinPs1File = { filename: 'osMoveWindow.ps1', content: OS_MOVEWINDOW_PS1 };
    const typePs1File    = { filename: 'osType.ps1',       content: OS_TYPE_PS1 };
    const activatePs1File = { filename: 'osActivate.ps1',  content: OS_ACTIVATE_PS1 };
    const dismissPopupPs1File = { filename: 'osDismissPopup.ps1', content: OS_DISMISS_POPUP_PS1 };
    const escapePs1File = { filename: 'osEscape.ps1', content: OS_ESCAPE_PS1 };
    const { savedPaths: wdioPaths, saveError: wdioErr } = saveFiles(
      [...wdioFiles, confFile, scrollPs1File, winRectPs1File, moveWinPs1File, typePs1File, activatePs1File, dismissPopupPs1File, escapePs1File], wdioOutDir
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