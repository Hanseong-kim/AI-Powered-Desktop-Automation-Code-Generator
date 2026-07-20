import { execSync, spawn } from 'child_process';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { homedir } from 'os';

const __dirname = dirname(fileURLToPath(import.meta.url));

// 주입/헬스 실패 수집 — 마지막에 실질 assert로 검증.
const _failures = [];
// 조용히 넘어갈 수 있는 성능/폴백 신호 — 실패는 아니지만 재생 품질 저하 가능성을 기록.
const _warnings = [];

// One-time PowerShell/.NET cold-start warm-up. execSync's per-call timeout
// budget was getting eaten by PowerShell's own process-spawn + Add-Type JIT
// cost on the FIRST call of a run (confirmed 2026-07-07 — VSCode multi-window
// osClick timeouts under concurrent PowerShell spawns). Absorbing that cost
// once up front keeps every real step's timeout budget for the actual work.
function _warmupPowerShell() {
    try {
        execSync('powershell -NoProfile -Command "Add-Type -AssemblyName System.Windows.Forms"', { stdio: 'pipe', timeout: 30000 });
    } catch (e) {
        console.warn('[warmup] powershell warm-up failed (non-fatal):', String(e.message || e).substring(0, 100));
    }
}

// Fixed local Appium endpoint — this file starts its own Appium instance
// (see ensureAppium below), so there is no WDIO config to read a
// host/port from anymore.
const _APPIUM = 'http://127.0.0.1:4723';
let _spawnedAppium = null;
// Root-session id (multi-window replay) / single-app-session id (simple
// replay) — set once in run()'s startup, consumed everywhere below.
let _rootSid = null;
let _appSid = null;

// Starts Appium if nothing is already listening on _APPIUM, otherwise
// reuses whatever is already running there (e.g. a dev Appium left up from
// a previous run). Spawned via the shared generated-wdio/node_modules
// install (one `npm install` for the whole generated-wdio/ tree, not per
// app) so no per-app setup step is required before `node <file>.js`.
async function ensureAppium() {
    try {
        const r = await fetch(`${_APPIUM}/status`, { signal: AbortSignal.timeout(2000) });
        if (r.ok) { console.log(`[appium] reusing already-running Appium at ${_APPIUM}`); return; }
    } catch {}
    console.log('[appium] starting Appium...');
    // node_modules/appium's package.json declares bin: { appium: 'index.js' } —
    // target that documented entry point directly rather than build/lib/main.js
    // (an internal build artifact that only works via an explicitly-documented
    // backwards-compat shim, confirmed 2026-07-17 by reading the installed
    // package; index.js is the stable contract across appium versions).
    const appiumBin = join(__dirname, '..', 'node_modules', 'appium', 'index.js');
    // '*:winappdriver' not bare 'winappdriver' — Appium 3.x's insecure-feature
    // validator requires '<automationName-or-*>:<featureName>' and throws on
    // a bare name (confirmed 2026-07-17 against the installed appium@3.5.2:
    // "The full feature name must include both the destination automation
    // name or the '*' wildcard ... Got 'winappdriver' instead"). This was a
    // pre-existing latent bug shared with wdio.conf.js's identical args —
    // just never hit because nothing had actually spawned Appium with these
    // exact CLI args end-to-end this session before ensureAppium() did.
    _spawnedAppium = spawn(process.execPath, [appiumBin, '--allow-insecure', '*:winappdriver', '--port', '4723'], { stdio: 'pipe' });
    _spawnedAppium.on('error', (e) => console.warn('[appium] spawn error:', String(e.message || e).substring(0, 150)));
    const deadline = Date.now() + 30000;
    while (Date.now() < deadline) {
        try {
            const r = await fetch(`${_APPIUM}/status`, { signal: AbortSignal.timeout(2000) });
            if (r.ok) { console.log('[appium] ready'); return; }
        } catch {}
        await new Promise(res => setTimeout(res, 1000));
    }
    throw new Error('Appium did not become ready within 30s');
}

function _killSpawnedAppium() {
    if (_spawnedAppium) {
        try { _spawnedAppium.kill(); } catch {}
        _spawnedAppium = null;
    }
}

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
        return await fetch(`${_APPIUM}${path}`, { ...opts, signal: ctrl.signal });
    } catch (e) {
        if (e.name === 'AbortError') throw new Error(`Appium request timed out after ${timeoutMs}ms: ${opts.method || 'GET'} ${path}`);
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
    const cap = isHwnd
        ? { platformName: 'Windows', 'appium:automationName': 'Windows', 'appium:appTopLevelWindow': app, 'appium:newCommandTimeout': 60000, 'appium:createSessionTimeout': 15000 }
        : { platformName: 'Windows', 'appium:automationName': 'Windows', 'appium:app': app, 'appium:newCommandTimeout': 60000, 'appium:createSessionTimeout': 15000 };
    const v = await _appiumPost('/session', { capabilities: { alwaysMatch: cap } }, 30000);
    if (!v?.sessionId) throw new Error(`Appium session failed for "${app}": ${JSON.stringify(v)}`);
    return v.sessionId;
}

async function _isSessionAlive(sid) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 1500);
    try {
        const r = await fetch(`${_APPIUM}/session/${sid}`, { signal: ctrl.signal });
        if (!r.ok) return false;
        const j = await r.json();
        return !!j?.value;
    } catch {
        return false;
    } finally {
        clearTimeout(timer);
    }
}

// 셀렉터로 요소를 찾아 element id를 돌려준다 — 좌표 산출 없음 (2026-07-10
// 좌표 실행 금지). sid/rootElId만 받는 일반형이라 세션 모드(title-keyed
// 캐시)와 simple 모드(단일 _appSid) 양쪽에서 그대로 재사용된다.
async function _findElement(sid, rootElId, selector) {
    try {
        const raw = selector.replace(/^['"]|['"]$/g, '');
        const using = raw.startsWith('~') ? 'accessibility id' : 'xpath';
        const value = raw.startsWith('~') ? raw.slice(1) : raw;
        const path = rootElId
            ? `/session/${sid}/element/${rootElId}/element`
            : `/session/${sid}/element`;
        const el = await _appiumPost(path, { using, value });
        if (!el) return null;
        return el.ELEMENT || el['element-6066-11e4-a52e-4f735466cecf'] || null;
    } catch (e) {
        console.warn('[findElement] lookup failed:', String(e.message || e).substring(0, 120));
        return null;
    }
}

// XPath-only click by raw session id — element/click = UIA Invoke, no
// coordinates anywhere. Used by simple mode (single _appSid, no title
// cache needed); session mode uses the title-keyed _clickScoped instead.
async function _clickBySid(sid, rootElId, selector, dbl = false) {
    const elId = await _findElement(sid, rootElId, selector);
    if (!elId) {
        _failures.push('click-not-found:' + String(selector).substring(0, 60));
        return;
    }
    await _appiumPost(`/session/${sid}/element/${elId}/click`, {});
    if (dbl) await _appiumPost(`/session/${sid}/element/${elId}/click`, {});
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
            ? `/session/${sid}/element/${rootElId}/element`
            : `/session/${sid}/element`;
        const el = await _appiumPost(path, { using, value });
        if (!el) throw new Error('element not found');
        const elId = el.ELEMENT || el['element-6066-11e4-a52e-4f735466cecf'];
        await _appiumPost(`/session/${sid}/element/${elId}/clear`, {});
        await _appiumPost(`/session/${sid}/element/${elId}/value`, { text });
        return true;
    } catch (e) { console.warn('[type] scoped sendKeys failed:', String(e.message || e).substring(0, 100)); return false; }
}

// 프로그래매틱 스크롤 — osScroll.py가 추적된 top-level hwnd 아래에서 녹화된
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
            `python "${join(__dirname, 'osScroll.py')}" --hwnd ${hwnd} --sel-b64 "${selB64}" --delta ${delta}`,
            { stdio: 'pipe', timeout: 20000 }
        ).toString().trim();
        if (out) console.log(out);
    } catch (e) {
        _failures.push('osScroll');
        console.warn('[osScroll] failed:', String((e.stderr && e.stderr.toString()) || e.message || e).substring(0, 200));
    }
}

// ExpandCollapsePattern 재생 — ComboBox 드롭다운/메뉴바 MenuItem/트리 +- 토글은
// 일반 클릭(InvokePattern)만으로 재현 안 됨(2026-07-13 진단, poc/diag_expandcollapse.py
// 실측: PuTTY ComboBox는 드롭다운이 안 열리고, FileZilla 메뉴바는 Expand()는
// 성공해도 하위 항목이 새 최상위 팝업 창에 생겨 원래 서브트리에서 안 보임).
// WinAppDriver REST를 거치지 않고 COM UIA로 직접 처리(osExpandCollapse.py —
// comtypes, 레거시 SysTreeView32 TreeItem까지 보임; 2026-07-14 .NET managed UIA
// 맹점 수정). 세션이 새 팝업 창을 못 보는 제약도 우회. itemName이 있으면 펼친
// 뒤 그 항목을 찾아 Invoke, 없으면 펼치기/접기 자체만(트리 +- 토글).
function osExpandCollapse(hwnd, target, itemName) {
    if (!hwnd) {
        _failures.push('osExpandCollapse:no-hwnd');
        console.warn('[osExpandCollapse] no window hwnd — cannot expand without a window handle');
        return;
    }
    try {
        const selB64 = Buffer.from(JSON.stringify(target || {}), 'utf8').toString('base64');
        const itemArg = itemName ? `--item-name-b64 "${Buffer.from(itemName, 'utf8').toString('base64')}"` : '';
        const out = execSync(
            `python "${join(__dirname, 'osExpandCollapse.py')}" --hwnd ${hwnd} --sel-b64 "${selB64}" ${itemArg}`,
            { stdio: 'pipe', timeout: 20000 }
        ).toString().trim();
        if (out) console.log(out);
    } catch (e) {
        _failures.push('osExpandCollapse');
        console.warn('[osExpandCollapse] failed:', String((e.stderr && e.stderr.toString()) || e.message || e).substring(0, 200));
    }
}

// 창-교차 클릭 재생 — 이벤트의 캡처 시점 창 크기/위치가 이 앱의 메인 창과
// 다르면(2026-07-13, PuTTY "Remote character set:" 콤보박스 조사: 옆
// "DropDown" 버튼 클릭으로 열리는 목록이 별도 최상위 창(Win32 클래스
// "ComboLBox")으로 뜸 — FileZilla 메뉴 팝업과 같은 부류) 그 대상은
// WinAppDriver 세션(메인 창에 스코프) 밖에 있다. osScopedInvoke.py가
// COM UIA로 메인 창 → 그 외 모든 최상위 창 순으로 직접 찾아 Invoke.
// triggerTarget이 있으면(버튼 클릭으로 여는 경우) 같은 스크립트 실행 안에서
// 그 트리거를 먼저 클릭한 뒤 곧바로 항목을 검색한다 — 트리거 클릭과 항목
// 검색을 별도 스텝(별도 프로세스)으로 쪼개면 그 사이 지연 동안 드롭다운이
// 자동으로 닫혀버림을 실측으로 확인(2026-07-13 재현) — 한 프로세스 실행
// 안에서 끊김 없이 처리해 그 레이스를 없앤다.
function osScopedInvoke(hwnd, target, triggerTarget) {
    if (!hwnd) {
        _failures.push('osScopedInvoke:no-hwnd');
        console.warn('[osScopedInvoke] no window hwnd — cannot search without a window handle');
        return;
    }
    try {
        const selB64 = Buffer.from(JSON.stringify(target || {}), 'utf8').toString('base64');
        const triggerArg = triggerTarget
            ? `--trigger-sel-b64 "${Buffer.from(JSON.stringify(triggerTarget), 'utf8').toString('base64')}"`
            : '';
        const out = execSync(
            `python "${join(__dirname, 'osScopedInvoke.py')}" --hwnd ${hwnd} --sel-b64 "${selB64}" ${triggerArg}`,
            { stdio: 'pipe', timeout: 20000 }
        ).toString().trim();
        if (out) console.log(out);
    } catch (e) {
        _failures.push('osScopedInvoke');
        // stdout carries any WARN lines (e.g. trigger-not-found) written before
        // the script's final Write-Error — surface both so the WARN isn't lost
        // behind the terminal error (2026-07-14: this WARN is what pinpoints
        // "dropdown never opened" vs. other reasons the item search failed).
        const stdoutMsg = (e.stdout && e.stdout.toString().trim()) || '';
        if (stdoutMsg) console.log(stdoutMsg);
        console.warn('[osScopedInvoke] failed:', String((e.stderr && e.stderr.toString()) || e.message || e).substring(0, 200));
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
        const r = await _appiumFetch(`/session/${_appSid}/window`);
        const j = await r.json();
        const h = j.value;   // e.g. "0x00061D2C"
        _appHwnd = parseInt(h, 16);
        console.log(`[hwnd] session window hwnd=${_appHwnd} (0x${_appHwnd.toString(16)})`);
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
        const args = _appHwnd ? `-hwnd ${_appHwnd}` : `-titleLike "${titleLike}"`;
        execSync(
            `powershell -NoProfile -File "${join(__dirname, 'osActivate.ps1')}" ${args}`,
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
        const args = _appHwnd ? `-hwnd ${_appHwnd}` : `-titleLike "${titleLike}"`;
        const out = execSync(
            `powershell -NoProfile -File "${join(__dirname, 'osWindowRect.ps1')}" ${args}`,
            { stdio: 'pipe', timeout: 15000 }
        ).toString().trim();
        const m = out.match(/(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)/);
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
            `powershell -NoProfile -File "${join(__dirname, 'osMoveWindow.ps1')}" -hwnd ${_appHwnd} -left ${rect.left} -top ${rect.top} -width ${rect.width} -height ${rect.height}`,
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
            `powershell -NoProfile -File "${join(__dirname, 'osType.ps1')}" -b64 "${b64}"`,
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
        const args = _appHwnd ? `-hwnd ${_appHwnd}` : '';
        const out = execSync(
            `powershell -NoProfile -File "${join(__dirname, 'osDismissPopup.ps1')}" ${args}`,
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
            `powershell -NoProfile -File "${join(__dirname, 'osEscape.ps1')}"`,
            { stdio: 'pipe', timeout: 15000 }
        );
        return true;
    } catch (e) {
        console.warn('[osEscape] failed:', String(e.message || e).substring(0, 100));
        return false;
    }
}

// Current foreground window handle (user32!GetForegroundWindow via a base64
// -EncodedCommand — no quote-escaping, read-only). _step() uses it to decide
// whether an ESC would land on a real popup or on the main dialog itself.
function osForegroundHwnd() {
    try {
        const out = execSync(
            `powershell -NoProfile -EncodedCommand QQBkAGQALQBUAHkAcABlACAAQAAiAAoAdQBzAGkAbgBnACAAUwB5AHMAdABlAG0AOwAKAHUAcwBpAG4AZwAgAFMAeQBzAHQAZQBtAC4AUgB1AG4AdABpAG0AZQAuAEkAbgB0AGUAcgBvAHAAUwBlAHIAdgBpAGMAZQBzADsACgBwAHUAYgBsAGkAYwAgAGMAbABhAHMAcwAgAEYAZwAgAHsAIABbAEQAbABsAEkAbQBwAG8AcgB0ACgAIgB1AHMAZQByADMAMgAuAGQAbABsACIAKQBdACAAcAB1AGIAbABpAGMAIABzAHQAYQB0AGkAYwAgAGUAeAB0AGUAcgBuACAASQBuAHQAUAB0AHIAIABHAGUAdABGAG8AcgBlAGcAcgBvAHUAbgBkAFcAaQBuAGQAbwB3ACgAKQA7ACAAfQAKACIAQAAgAC0ARQByAHIAbwByAEEAYwB0AGkAbwBuACAAUwBpAGwAZQBuAHQAbAB5AEMAbwBuAHQAaQBuAHUAZQAKAFsARgBnAF0AOgA6AEcAZQB0AEYAbwByAGUAZwByAG8AdQBuAGQAVwBpAG4AZABvAHcAKAApAC4AVABvAEkAbgB0ADYANAAoACkA`,
            { stdio: 'pipe', timeout: 15000 }
        ).toString().trim();
        const m = out.match(/-?\d+/);
        return m ? (parseInt(m[0], 10) || 0) : 0;
    } catch (e) {
        console.warn('[osForegroundHwnd] failed:', String(e.message || e).substring(0, 100));
        return 0;
    }
}

// Wraps a single replay step: on the happy path (no exception, no new
// _failures entry) this costs nothing extra. On failure, scans for and
// dismisses a known-shape popup that didn't exist at recording time (e.g.
// FDM's "file already exists"), then retries the step ONCE. If no dismiss
// button was found, it may ESC to back out of a transient modal state — but
// only when a real popup (not the main dialog) holds the foreground; see below.
// If recovery still fails, the original failure/exception stands (no false PASSED).
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
        // No known popup button found. On a dialog-based main window (PuTTY
        // Configuration) ESC == Cancel == close the app, so an unconditional
        // ESC here nukes the whole run on the first failed step (confirmed
        // 2026-07-14: the old osActivate('')+ESC closed PuTTY every time). Only
        // ESC when a DIFFERENT top-level window (a real popup/dropdown) holds
        // the foreground; if OUR main window is foreground there is nothing to
        // dismiss and ESC would only kill the app — skip it.
        const fg = osForegroundHwnd();
        if (_appHwnd && fg === _appHwnd) {
            _warnings.push('esc-skipped-main-foreground:' + label);
        } else {
            osEscape();
            // Backstop: if ESC did land on a dialog-based window and closed the
            // app, surface the ORIGINAL failure cleanly instead of a misleading
            // no-such-window cascade (2026-07-13).
            if (_appHwnd && !_resolveWinRect('')) {
                _failures.push('esc-recovery-closed-app:' + label);
                throw new Error(`ESC recovery closed the app window during step: ${label}`);
            }
            _warnings.push('esc-recovery:' + label);
        }
    }
    _failures.length = before;
    await fn();
}

class CalculatorPageById {
    async click1() {
        await _clickBySid(_appSid, null, '~num5Button');
    }

    async click3() {
        await _clickBySid(_appSid, null, '~plusButton');
    }

    async click4() {
        await _clickBySid(_appSid, null, '~num3Button');
    }

    async click6() {
        await _clickBySid(_appSid, null, '~equalButton');
    }

    async click7() {
        await _clickBySid(_appSid, null, '//Text[@AutomationId="CalculatorResults" and @Name="Result display"]', true);
    }

    async scroll8() {
        osScrollEl(_appHwnd, {"automationId":"","className":"ScrollViewer","name":""}, -3);
    }

    async click11() {
        await _clickBySid(_appSid, null, '//*[@AutomationId="NumberPad"]/Button[3]');
    }
}

// Plain async entry point — replaces the old Jasmine describe/it wrapper
// (2026-07-17: standalone execution, no WDIO/Jasmine runner needed).
async function run() {
    // Everything — including Appium/session startup — runs inside this
    // try/finally, not just the replay steps: a failure in ensureAppium()/
    // _createSession() (e.g. a bad capability) must still kill any Appium
    // process this run spawned. Node does not reliably reap child processes
    // on Windows when the parent exits, so leaving startup outside the
    // finally risked leaking an orphaned Appium instance on every startup
    // failure (confirmed 2026-07-17 while verifying the standalone runner).
    try {
        _warmupPowerShell();

    await ensureAppium();
    _appSid = await _createSession("");
    console.log(`[session] app session ${_appSid} ready`);
    await initAppHwnd();
    normalizeWindowSimple(null);

        const page = new CalculatorPageById();
            osActivate("Calculator");
            await _step('1:click Five', () => page.click1());
            // [STEP 2] skip type on non-editable element
            await _step('3:click Plus', () => page.click3());
            await _step('4:click Three', () => page.click4());
            // [STEP 5] skip type on non-editable element
            await _step('6:click Equals', () => page.click6());
            await _step('7:doubleClick Result display', () => page.click7());
            await _step('8:scroll delta=-3', () => page.scroll8());
            // [STEP 9] rightClick scope-out — replay skipped (event scope: Click/Type/DoubleClick/Scroll, 2026-07-10)
            // [STEP 10] drag scope-out — replay skipped (event scope: Click/Type/DoubleClick/Scroll, 2026-07-10)
            await _step('11:click ', () => page.click11());
    } finally {

        if (_appSid) { try { await _appiumFetch(`/session/${_appSid}`, { method: 'DELETE' }, 5000); } catch {} }
        _killSpawnedAppium();
    }
    if (_warnings.length) console.warn('[replay-warnings]', _warnings);
    if (_failures.length) { console.error('[FAIL]', _failures); process.exitCode = 1; }
    else console.log('[PASS] all steps completed');
}

run().catch(e => { console.error('[FATAL]', e); process.exitCode = 1; });
