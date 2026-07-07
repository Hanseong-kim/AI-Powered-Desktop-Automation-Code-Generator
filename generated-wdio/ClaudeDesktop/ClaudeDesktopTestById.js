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
            return;
        } catch (e) {
            if (attempt === 2) {
                _failures.push('osClick');
                console.warn('[osClick] failed after retry:', String(e.message || e).substring(0, 100));
            } else {
                console.warn('[osClick] attempt 1 failed, retrying:', String(e.message || e).substring(0, 100));
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

async function _appiumPost(path, body) {
    const r = await fetch(`${_APPIUM}${path}`, {
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
    console.log(`[session] Root scan for: "${title}"`);
    const shortTitle = title.slice(0, 30).replace(/"/g, '');
    let hwnd = null;
    let matchedEl = null;
    for (const sel of [`//*[@Name="${title}"]`, `//*[contains(@Name,"${shortTitle}")]`]) {
        try {
            const el = await browser.$(sel);
            const raw = await el.getAttribute('NativeWindowHandle');
            const hwndNum = parseInt(raw, 10);
            if (hwndNum) { hwnd = '0x' + hwndNum.toString(16); matchedEl = el; break; }
        } catch {}
    }
    const app = hwnd || 'Root';
    if (hwnd) console.log(`[session] hwnd=${hwnd} → scoped session`);
    else { console.warn(`[session] Window "${title}" not found — falling back to Root`); _warnings.push('session-fallback:' + title); }
    try {
        const sid = await _createSession(app);
        _sessionIds[title] = { sid, rootElId: null };
    } catch (e) {
        console.warn(`[session] scoped session failed (${e.message}) — reusing Root session for "${title}"`);
        _warnings.push('session-fallback:' + title);
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
            ? `/session/${sid}/element/${rootElId}/element`
            : `/session/${sid}/element`;
        const el = await _appiumPost(path, { using, value });
        if (!el) return null;
        const elId = el.ELEMENT || el['element-6066-11e4-a52e-4f735466cecf'];
        // location+size (JSONWP) rather than /rect (W3C) — see getCenterSimple
        // for why WinAppDriver's /rect support can't be relied on.
        const locR = await (await fetch(`${_APPIUM}/session/${sid}/element/${elId}/location`)).json();
        const sizeR = await (await fetch(`${_APPIUM}/session/${sid}/element/${elId}/size`)).json();
        const loc = locR.value, size = sizeR.value;
        if (!loc || !size) return null;
        return { x: Math.round(loc.x + size.width / 2), y: Math.round(loc.y + size.height / 2) };
    } catch (e) {
        console.warn('[getCenter] live resolve failed:', String(e.message || e).substring(0, 120));
        return null;
    }
}

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

// Main app window title-fragment, set once in beforeAll (see generateWdio's
// beforeHook) — lets osDismissPopup() identify the main window/PID for
// owner-PID scoping without every call site having to pass it in.
let _mainTitleFrag = '';

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
    const r = _resolveWinRect(frag);
    if (r) { osClick(r.left + relX, r.top + relY, button, clicks); }
    else { _failures.push('absCoord-fallback'); osClick(absX, absY, button, clicks); }      // 창 못 찾으면 녹화 절대좌표로 폴백 — 실패로 기록
}
function osScrollRel(frag, relX, relY, absX, absY, delta) {
    const r = _resolveWinRect(frag);
    if (r) { osScroll(r.left + relX, r.top + relY, delta); }
    else { _failures.push('absCoord-fallback'); osScroll(absX, absY, delta); }
}
function osDragRel(frag, relX1, relY1, relX2, relY2, absX1, absY1, absX2, absY2) {
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

// Fail-and-Recover popup dismissal (v1) — only called from _step() below,
// after a step has already failed, so the happy path pays zero cost.
// Prefers the tracked hwnd for the main app window (_hwndCache[_mainTitleFrag],
// set by launchApp) for deterministic owner-PID scoping; falls back to a
// title-substring match when no hwnd was tracked (e.g. app already running).
function osDismissPopup() {
    try {
        const hwnd = _hwndCache[_mainTitleFrag];
        const args = hwnd ? `-hwnd ${hwnd}` : (_mainTitleFrag ? `-titleLike "${_mainTitleFrag}"` : '');
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

// Wraps a single replay step: on the happy path (no exception, no new
// _failures entry) this costs nothing extra. On failure, scans for and
// dismisses a known-shape popup that didn't exist at recording time (e.g.
// FDM's "file already exists"), then retries the step ONCE. If no popup was
// found, the original failure/exception stands untouched.
async function _step(label, fn) {
    console.log('[STEP] ' + label);
    const before = _failures.length;
    let err = null;
    try { await fn(); } catch (e) { err = e; }
    if (!err && _failures.length === before) return;
    const dismissed = osDismissPopup();
    if (!dismissed) { if (err) throw err; return; }
    _warnings.push('popup-dismissed:' + label);
    _failures.length = before;
    await fn();
}

class ClaudeDesktopPageById {
    async click1() {
        let s = await getWindowSession('Claude');
        let c = await getCenter(s.sid, s.rootElId, '//*[@ClassName="Windows.UI.Input.InputSite.WindowClass"]');
        if (!c) {
            delete _sessionIds['Claude'];
            s = await getWindowSession('Claude');
            c = await getCenter(s.sid, s.rootElId, '//*[@ClassName="Windows.UI.Input.InputSite.WindowClass"]');
        }
        if (!c) { _failures.push('click1:coord-fallback'); }
        c = c ?? { x: 926, y: 752 };
        osClick(c.x, c.y);
    }

    async scroll2() {
        osScrollRel('Claude', 723, 530, 716, 523, 360);
    }

    async scroll3() {
        osScrollRel('Claude', 723, 530, 716, 523, 600);
    }

    async scroll4() {
        osScrollRel('Claude', 723, 530, 716, 523, 600);
    }

    async scroll5() {
        osScrollRel('Claude', 722, 529, 715, 522, 720);
    }

    async scroll6() {
        osScrollRel('Claude', 722, 529, 715, 522, 840);
    }

    async scroll7() {
        osScrollRel('Claude', 722, 529, 715, 522, 840);
    }

    async scroll8() {
        osScrollRel('Claude', 722, 529, 715, 522, 840);
    }

    async scroll9() {
        osScrollRel('Claude', 722, 529, 715, 522, 840);
    }

    async scroll10() {
        osScrollRel('Claude', 722, 529, 715, 522, 960);
    }

    async scroll11() {
        osScrollRel('Claude', 722, 529, 715, 522, -720);
    }

    async scroll12() {
        osScrollRel('Claude', 722, 529, 715, 522, -840);
    }

    async scroll13() {
        osScrollRel('Claude', 722, 529, 715, 522, -7320);
    }
}

describe('ClaudeDesktopTestById', () => {
    beforeAll(async () => {
        _warmupPowerShell();
        _mainTitleFrag = "Claude";
        const { hostname, port } = browser.options;
        _APPIUM = `http://${hostname || '127.0.0.1'}:${port || 4723}`;
        console.log(`[session] Appium endpoint resolved to ${_APPIUM}`);
        await launchApp("Claude_pzs8sxrjxfjjc!Claude", [], "Claude", {"left":-7,"top":-7,"width":1550,"height":830});
    });

    afterAll(async () => {
        for (const { sid } of Object.values(_sessionIds)) {
            if (sid === browser.sessionId) continue;
            try { await fetch(`${_APPIUM}/session/${sid}`, { method: 'DELETE' }); } catch {}
        }
    });

    it('should replay recorded flow', async () => {
        const page = new ClaudeDesktopPageById();

            await _step('1:click ', () => page.click1());
            await _step('2:scroll delta=360', () => page.scroll2());
            await _step('3:scroll delta=600', () => page.scroll3());
            await _step('4:scroll delta=600', () => page.scroll4());
            await _step('5:scroll delta=720', () => page.scroll5());
            await _step('6:scroll delta=840', () => page.scroll6());
            await _step('7:scroll delta=840', () => page.scroll7());
            await _step('8:scroll delta=840', () => page.scroll8());
            await _step('9:scroll delta=840', () => page.scroll9());
            await _step('10:scroll delta=960', () => page.scroll10());
            await _step('11:scroll delta=-720', () => page.scroll11());
            await _step('12:scroll delta=-840', () => page.scroll12());
            await _step('13:scroll delta=-7320', () => page.scroll13());

            if (_warnings.length) console.warn('[replay-warnings]', _warnings);
            expect(_failures).toEqual([]);
    });
});
