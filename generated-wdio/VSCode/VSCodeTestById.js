import { execSync, spawn } from 'child_process';
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

// OS-level click via PowerShell user32.dll — verified for Electron menus, native dialogs.
// Retries once before giving up — a single slow PowerShell cold-start under
// process-spawn contention shouldn't fail the whole step (confirmed 2026-07-07).
function osClick(x, y, button = 'left', clicks = 1) {
    const cmd = `powershell -NoProfile -File "${join(__dirname, 'osClick.ps1')}" -x ${x} -y ${y} -button ${button} -clicks ${clicks}`;
    for (let attempt = 1; attempt <= 2; attempt++) {
        try {
            const out = execSync(cmd, { stdio: 'pipe', timeout: 15000 });
            const diag = out.toString().trim();
            if (diag) console.log(diag);
            // Cursor can land off the requested point on a scaled/secondary
            // monitor (DPI-unaware SetCursorPos) — warn, don't fail the test:
            // a few px of drift is often harmless, and asserting on it would
            // risk failing FDM/ClaudeDesktop on sub-pixel drift they already
            // tolerate today.
            const m = diag.match(/requested=\((-?\d+),(-?\d+)\) landed=\((-?\d+),(-?\d+)\)/);
            if (m) {
                const dx = Math.abs(Number(m[1]) - Number(m[3]));
                const dy = Math.abs(Number(m[2]) - Number(m[4]));
                if (dx > 3 || dy > 3) {
                    _warnings.push(`osClick-drift:requested=(${m[1]},${m[2]}) landed=(${m[3]},${m[4]})`);
                }
            }
            return;
        } catch (e) {
            // Full error, not truncated — a truncated 100-char message hid
            // the real cause of intermittent non-zero exits during actual
            // wdio runs (confirmed 2026-07-08: couldn't diagnose from the
            // truncated log; direct standalone repro of the same script
            // never failed, so whatever's failing only shows up under the
            // load of a real run and needs the full text to identify).
            const detail = (e.stderr && e.stderr.toString()) || String(e.message || e);
            if (attempt === 2) {
                _failures.push('osClick');
                console.warn('[osClick] failed after retry:', detail);
            } else {
                console.warn('[osClick] attempt 1 failed, retrying:', detail);
            }
        }
    }
}

// OS-level mouse wheel via PowerShell user32.dll (WinAppDriver has no wheel API).
function osScroll(x, y, delta) {
    try {
        execSync(
            `powershell -NoProfile -File "${join(__dirname, 'osScroll.ps1')}" -x ${x} -y ${y} -delta ${delta}`,
            { stdio: 'pipe', timeout: 15000 }
        );
    } catch (e) {
        _failures.push('osScroll');
        console.warn('[osScroll] failed:', String(e.message || e).substring(0, 100));
    }
}

// OS-level press-hold-move-release drag via PowerShell user32.dll (text
// selection etc.) — same execSync injection approach as osClick/osScroll,
// retried once for the same PowerShell cold-start reason as osClick.
function osDrag(x1, y1, x2, y2) {
    const cmd = `powershell -NoProfile -File "${join(__dirname, 'osDrag.ps1')}" -x1 ${x1} -y1 ${y1} -x2 ${x2} -y2 ${y2}`;
    for (let attempt = 1; attempt <= 2; attempt++) {
        try {
            const out = execSync(cmd, { stdio: 'pipe', timeout: 15000 });
            const diag = out.toString().trim();
            if (diag) console.log(diag);
            return;
        } catch (e) {
            if (attempt === 2) {
                _failures.push('osDrag');
                console.warn('[osDrag] failed after retry:', String(e.message || e).substring(0, 100));
            } else {
                console.warn('[osDrag] attempt 1 failed, retrying:', String(e.message || e).substring(0, 100));
            }
        }
    }
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
    if (!v?.sessionId) throw new Error(`Appium session failed for "${app}": ${JSON.stringify(v)}`);
    return v.sessionId;
}

// Health-check with a hard timeout — a dead/stale session must never hang the suite.
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
            console.log(`[session] hwnd=0x${hwndNum.toString(16)} owned by 0x${ownerHwnd.toString(16)} — skipping scoped session (WAD rejects owned windows)`);
            _scopedFailHwnds.add(hwndNum);
        }
    }
    if (hwndNum && !_scopedFailHwnds.has(hwndNum)) {
        const hwndHex = '0x' + hwndNum.toString(16);
        console.log(`[session] top-level hwnd=${hwndHex} for "${title}" → scoped session`);
        const t0 = Date.now();
        try {
            const sid = await _createSession(hwndHex);
            console.log(`[session] scoped session on ${hwndHex} ready in ${Date.now() - t0}ms`);
            // hwnd tracked here (not 0/Root) — a scoped session's element
            // /location returns coordinates relative to that window, not the
            // screen (confirmed 2026-07-08), so callers must add the live
            // window origin before feeding a point to osClick.
            _sessionIds[title] = { sid, rootElId: null, hwnd: hwndNum };
            return _sessionIds[title];
        } catch (e) {
            _scopedFailHwnds.add(hwndNum);
            console.warn(`[session] scoped session on ${hwndHex} failed after ${Date.now() - t0}ms (${e.message}) — falling back to desktop-UIA scan for "${title}"`);
        }
    }

    // Safety net: EnumWindows found nothing (e.g. an empty/dynamic dialog
    // title) — fall back to the original desktop-UIA XPath scan + Root
    // session reuse.
    console.log(`[session] Root scan for: "${title}"`);
    const shortTitle = title.slice(0, 30).replace(/"/g, '');
    let hwnd = null;
    let matchedEl = null;
    for (const sel of [`//*[@Name="${title}"]`, `//*[contains(@Name,"${shortTitle}")]`]) {
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
            console.log(`[session] hwnd=${hwnd} owned by 0x${ownerHwnd.toString(16)} — skipping scoped session (WAD rejects owned windows)`);
            _scopedFailHwnds.add(scanHwndNum);
        }
    }
    if (scanHwndNum && !_scopedFailHwnds.has(scanHwndNum)) {
        console.log(`[session] hwnd=${hwnd} → scoped session`);
        const t0 = Date.now();
        try {
            const sid = await _createSession(hwnd);
            console.log(`[session] scoped session on ${hwnd} ready in ${Date.now() - t0}ms`);
            // Scoped window's hwnd tracked — element /location is window-
            // relative here, same distinction as the EnumWindows path above.
            _sessionIds[title] = { sid, rootElId: null, hwnd: scanHwndNum };
            return _sessionIds[title];
        } catch (e) {
            _scopedFailHwnds.add(scanHwndNum);
            console.warn(`[session] scoped session failed after ${Date.now() - t0}ms (${e.message}) — reusing Root session for "${title}"`);
        }
    }
    // Root-session reuse (proven 2026-07-08): no new session, no WAD spawn.
    // Element lookups are scoped to the matched dialog element's subtree via
    // rootElId; hwnd 0 = /location is already screen-absolute. Deliberately
    // NOT _createSession('Root') — that would spawn yet another WinAppDriver
    // process with the same 30s-hang exposure as the scoped path.
    if (!hwnd) console.warn(`[session] Window "${title}" not found — falling back to Root`);
    _warnings.push('session-fallback:' + title);
    _sessionIds[title] = { sid: browser.sessionId, rootElId: matchedEl ? matchedEl.elementId : null, hwnd: 0 };
    return _sessionIds[title];
}

async function getCenter(sid, rootElId, selector) {
    try {
        const raw = selector.replace(/^['"]|['"]$/g, '');
        const using = raw.startsWith('~') ? 'accessibility id' : 'xpath';
        const value = raw.startsWith('~') ? raw.slice(1) : raw;
        const path = rootElId
            ? `/session/${sid}/element/${rootElId}/element`
            : `/session/${sid}/element`;
        const el = await _appiumPost(path, { using, value });
        if (!el) return null;
        const elId = el.ELEMENT || el['element-6066-11e4-a52e-4f735466cecf'];
        // location+size (JSONWP) rather than /rect (W3C) — see getCenterSimple
        // for why WinAppDriver's /rect support can't be relied on.
        const locR = await (await _appiumFetch(`/session/${sid}/element/${elId}/location`)).json();
        const sizeR = await (await _appiumFetch(`/session/${sid}/element/${elId}/size`)).json();
        const loc = locR.value, size = sizeR.value;
        // A JSONWP error response is a truthy object too ({error, message,
        // stacktrace}), so "loc && size" alone doesn't catch it — loc.x then
        // comes back undefined, Math.round(undefined + ...) is NaN, and NaN
        // reaching osClick's PowerShell -x/-y params throws a parameter-
        // binding error (confirmed 2026-07-08: "'x' 매개 변수... 'NaN'을
        // 'System.Int32'로 변환할 수 없음", previously misread as a mystery
        // intermittent PowerShell failure). Require actual finite numbers.
        if (!loc || !size || !Number.isFinite(loc.x) || !Number.isFinite(loc.y) ||
            !Number.isFinite(size.width) || !Number.isFinite(size.height)) return null;
        return { x: Math.round(loc.x + size.width / 2), y: Math.round(loc.y + size.height / 2) };
    } catch (e) {
        console.warn('[getCenter] live resolve failed:', String(e.message || e).substring(0, 120));
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
            ? `/session/${s.sid}/element/${s.rootElId}/elements`
            : `/session/${s.sid}/elements`;
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
                const r = await (await _appiumFetch(`/session/${s.sid}/element/${elId}/attribute/Name`)).json();
                if (typeof r.value === 'string') names.push(r.value);
            } catch {}
        }
        console.warn(`[getCenter-diag] UIA-exposed rows (${els.length} total): ${names.join(' | ')}`);
    } catch (e) {
        console.warn('[getCenter-diag] dump failed:', String(e.message || e).substring(0, 100));
    }
}

// Named-element lookup with condition polling (waitUntil-style — no fixed
// pause). A navigation click (e.g. selecting a drive in the "폴더 열기" nav
// pane) repopulates the dialog's file list ASYNCHRONOUSLY; the old zero-wait
// getCenter → rescan → getCenter sequence declared coord-fallback before the
// list had refreshed (confirmed 2026-07-09: STEP 6 "hansung" no-such-element
// twice in a row, then the recorded-coordinate fallback double-clicked
// whatever row happened to sit at the old pixel position). Polls once per
// second up to timeoutMs; halfway through it invalidates the cached
// session/rootElId once in case the cached dialog element itself went stale.
// Returns { c, s }: c null on timeout (after dumping visible rows), s always
// the last session used so the caller can apply its window-origin correction.
async function getCenterWithWait(title, selector, timeoutMs = 8000) {
    const deadline = Date.now() + timeoutMs;
    const refreshAt = Date.now() + timeoutMs / 2;
    let refreshed = false;
    for (;;) {
        const s = await getWindowSession(title);
        // Dialog window itself wasn't found (no hwnd, no matched element):
        // getCenter would scan the ENTIRE desktop tree from Root at 10s+ per
        // call. Drop the useless cache entry and fail fast — the caller's
        // coord-fallback (recorded as a _failures entry) takes it from here.
        if (!s.hwnd && !s.rootElId) {
            delete _sessionIds[title];
            console.warn(`[getCenter] window "${title}" not found — failing fast`);
            return { c: null, s };
        }
        const c = await getCenter(s.sid, s.rootElId, selector);
        if (c) return { c, s };
        if (Date.now() >= deadline) {
            await _dumpVisibleRows(s);
            return { c: null, s };
        }
        if (!refreshed && Date.now() >= refreshAt) {
            refreshed = true;
            delete _sessionIds[title];
        }
        await new Promise(r => setTimeout(r, 1000));
    }
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
        console.warn(`[dialog] "${title}" not found by EnumWindows — rel-offsets may be unreliable`);
        return;
    }
    _hwndCache[title] = hs[0];
    const r = _dialogRects[title];
    normalizeWindow(title, r.left, r.top, r.width, r.height);
    osActivate(title, hs[0]);
    console.log(`[dialog] "${title}" hwnd=${hs[0]} normalized to`, r);
}

function _listWindowHwnds(frag) {
    if (!frag) return [];
    try {
        const out = execSync(
            `powershell -NoProfile -File "${join(__dirname, 'osWindowRect.ps1')}" -titleLike "${frag}" -listOnly`,
            { stdio: 'pipe', timeout: 15000 }
        ).toString().trim();
        if (!out) return [];
        return out.split(/\r?\n/).map(s => s.trim()).filter(Boolean).map(Number);
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
            `powershell -NoProfile -File "${join(__dirname, 'osWindowRect.ps1')}" -hwnd ${hwndNum} -ownerOnly`,
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
        const args = hwnd ? `-hwnd ${hwnd}` : `-titleLike "${frag}"`;
        const out = execSync(
            `powershell -NoProfile -File "${join(__dirname, 'osWindowRect.ps1')}" ${args}`,
            { stdio: 'pipe', timeout: 15000 }
        ).toString().trim();
        const m = out.match(/(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)/);
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
        const target = hwnd ? `-hwnd ${hwnd}` : `-titleLike "${frag}"`;
        execSync(
            `powershell -NoProfile -File "${join(__dirname, 'osMoveWindow.ps1')}" ${target} -left ${left} -top ${top} -width ${width} -height ${height}`,
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
        const args = hwnd ? `-hwnd ${hwnd}` : `-titleLike "${titleLike}"`;
        execSync(
            `powershell -NoProfile -File "${join(__dirname, 'osActivate.ps1')}" ${args}`,
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
    const isAumid = /!/.test(exePath) && !/[\/]/.test(exePath);
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
            spawn('explorer.exe', ['shell:AppsFolder\\' + exePath], { detached: true, stdio: 'ignore' }).unref();
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
                console.log(`[launch] tracking new window hwnd=${fresh}`);
            } else if (baselineTail) {
                const freshTail = _listWindowHwnds(tailFrag).find(h => !baselineTail.has(h));
                if (freshTail) {
                    _hwndCache[titleFrag] = freshTail;
                    console.log(`[launch] adopted new window hwnd=${freshTail} via tail fragment "${tailFrag}" (recorded title "${titleFrag}" not present at launch)`);
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
        console.log(`[launch-diag] poll=${poll} titleFrag=${JSON.stringify(titleFrag)} baseline=[${[...baseline]}] matched=[${matched}] hwndCache=${_hwndCache[titleFrag] ?? 'none'} liveRect=${JSON.stringify(liveRect)}`);
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
    _ensureDialog(frag);
    const r = _resolveWinRect(frag);
    if (r) { osClick(r.left + relX, r.top + relY, button, clicks); }
    else { _failures.push('absCoord-fallback'); osClick(absX, absY, button, clicks); }      // 창 못 찾으면 녹화 절대좌표로 폴백 — 실패로 기록
}
function osScrollRel(frag, relX, relY, absX, absY, delta) {
    _ensureDialog(frag);
    const r = _resolveWinRect(frag);
    if (r) { osScroll(r.left + relX, r.top + relY, delta); }
    else { _failures.push('absCoord-fallback'); osScroll(absX, absY, delta); }
}
function osDragRel(frag, relX1, relY1, relX2, relY2, absX1, absY1, absX2, absY2) {
    _ensureDialog(frag);
    const r = _resolveWinRect(frag);
    if (r) { osDrag(r.left + relX1, r.top + relY1, r.left + relX2, r.top + relY2); }
    else { _failures.push('absCoord-fallback'); osDrag(absX1, absY1, absX2, absY2); }
}

// Electron 입력: 직전 osClick이 포커스를 잡아둔 상태 → OS 키 주입.
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
        let args = hwnd ? `-hwnd ${hwnd}` : (_mainTitleFrag ? `-titleLike "${_mainTitleFrag}"` : '');
        const tracked = [...new Set(Object.values(_hwndCache))].filter(Boolean);
        if (tracked.length) args += ` -exclude "${tracked.join(',')}"`;
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

class VSCodePageById {
    async click1() {
        osClickRel('시작 - Visual Studio Code', 1144, 22, 1305, 54);
    }

    async click2() {
        osClickRel('시작 - Visual Studio Code', 55, 21, 48, 14);
    }

    async click3() {
        osClickRel('시작 - Visual Studio Code', 116, 187, 109, 180);
    }

    async scroll4() {
        osScrollRel('폴더 열기', 71, 238, 81, 244, -4680);
    }

    async click5() {
        let { c, s } = await getCenterWithWait('폴더 열기', '//*[@Name="로컬 디스크 (C:)"]');
        if (c && s.hwnd) {
            const r = _resolveWinRect('폴더 열기');
            if (r) c = { x: r.left + c.x, y: r.top + c.y };
        }
        if (!c) { _failures.push('click5:coord-fallback'); }
        c = c ?? { x: 110, y: 286 };
        osClick(c.x, c.y);
    }

    async click6() {
        let { c, s } = await getCenterWithWait('폴더 열기', '//*[@Name="hansung"]');
        if (c && s.hwnd) {
            const r = _resolveWinRect('폴더 열기');
            if (r) c = { x: r.left + c.x, y: r.top + c.y };
        }
        if (!c) { _failures.push('click6:coord-fallback'); }
        c = c ?? { x: 245, y: 193 };
        osClick(c.x, c.y, 'left', 2);
    }

    async click7() {
        let { c, s } = await getCenterWithWait('폴더 열기', '//*[@Name="project"]');
        if (c && s.hwnd) {
            const r = _resolveWinRect('폴더 열기');
            if (r) c = { x: r.left + c.x, y: r.top + c.y };
        }
        if (!c) { _failures.push('click7:coord-fallback'); }
        c = c ?? { x: 270, y: 198 };
        osClick(c.x, c.y, 'left', 2);
    }

    async scroll8() {
        osScrollRel('폴더 열기', 260, 192, 270, 198, -4440);
    }

    async click9() {
        let { c, s } = await getCenterWithWait('폴더 열기', '//*[@Name="run"]');
        if (c && s.hwnd) {
            const r = _resolveWinRect('폴더 열기');
            if (r) c = { x: r.left + c.x, y: r.top + c.y };
        }
        if (!c) { _failures.push('click9:coord-fallback'); }
        c = c ?? { x: 285, y: 311 };
        osClick(c.x, c.y);
    }

    async click10() {
        osClickRel('폴더 열기', 601, 442, 611, 448);
    }
}

describe('VSCodeTestById', () => {
    beforeAll(async () => {
        _warmupPowerShell();
        _mainTitleFrag = "시작 - Visual Studio Code";
        _dialogRects = {"폴더 열기":{"left":10,"top":6,"width":768,"height":481}};
        const { hostname, port } = browser.options;
        _APPIUM = `http://${hostname || '127.0.0.1'}:${port || 4723}`;
        console.log(`[session] Appium endpoint resolved to ${_APPIUM}`);
        await launchApp("C:\\Users\\user\\AppData\\Local\\Programs\\Microsoft VS Code\\Code.exe", ["-n"], "시작 - Visual Studio Code", {"left":161,"top":32,"width":1218,"height":810});
    });

    afterAll(async () => {
        for (const { sid } of Object.values(_sessionIds)) {
            if (sid === browser.sessionId) continue;
            try { await _appiumFetch(`/session/${sid}`, { method: 'DELETE' }, 5000); } catch {}
        }
    });

    it('should replay recorded flow', async () => {
        const page = new VSCodePageById();

            await _step('1:click ', () => page.click1());
            await _step('2:click 파일', () => page.click2());
            await _step('3:click 편집기를 사용하여 작업 속도를 향상하는 방법에 관한 개요입니다.', () => page.click3());
            await _step('4:scroll delta=-4680', () => page.scroll4());
            await _step('5:click 로컬 디스크 (C:)', () => page.click5());
            await _step('6:doubleClick hansung', () => page.click6());
            await _step('7:doubleClick project', () => page.click7());
            await _step('8:scroll delta=-4440', () => page.scroll8());
            await _step('9:click run', () => page.click9());
            await _step('10:click 폴더 선택(S)', () => page.click10());

            if (_warnings.length) console.warn('[replay-warnings]', _warnings);
            expect(_failures).toEqual([]);
    });
});
