import { execSync } from 'child_process';
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

// OS-level click fallback via PowerShell user32.dll. Retries once before
// giving up — a single slow PowerShell cold-start under process-spawn
// contention shouldn't fail the whole step (confirmed 2026-07-07).
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

// Qt/QML controls only: UIA Invoke (el.click()) reaches the accessibility
// tree but often isn't wired to the control's real MouseArea/TapHandler, so
// the click succeeds with no error yet the app never reacts (confirmed
// 2026-07-06 — FreeDM toolbar click "succeeded" but never opened its dialog).
// Resolve the element's LIVE center via XPath (still window-move-safe, no
// hardcoded coordinates), then inject a real OS click there.
// Uses getLocation()+getSize() rather than getRect(): WinAppDriver is a
// JSONWP-era driver bridged into W3C by appium-windows-driver, and el.getRect()
// (the newer W3C /rect endpoint) is unreliable there — confirmed 2026-07-06,
// every FreeDM click silently fell back to its recorded literal coordinates
// (production of a moved-window miss), which only happens if getRect() throws
// on every single call. location/size are the older JSONWP endpoints and are
// consistently supported.
async function getCenterSimple(selector) {
    try {
        const el = await browser.$(selector);
        await el.waitForExist({ timeout: 8000 });
        const loc = await el.getLocation();
        const size = await el.getSize();
        return { x: Math.round(loc.x + size.width / 2), y: Math.round(loc.y + size.height / 2) };
    } catch (e) {
        console.warn('[getCenterSimple] live resolve failed, using recorded fallback coords:', String(e.message || e).substring(0, 120));
        return null;
    }
}

// Reads the window's CURRENT rect — by hwnd when the session's own window is
// known (deterministic), by title match otherwise — and replays a recorded
// window-relative offset against it, so a moved/repositioned window still
// gets clicked in the right spot. Falls back to the recorded absolute
// coordinates only when the window can't be found at all.
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

function osClickRel(titleLike, relX, relY, absX, absY, button = 'left', clicks = 1) {
    const r = _resolveWinRect(titleLike);
    if (r) { osClick(r.left + relX, r.top + relY, button, clicks); }
    else { _failures.push('absCoord-fallback'); osClick(absX, absY, button, clicks); }
}
function osScrollRel(titleLike, relX, relY, absX, absY, delta) {
    const r = _resolveWinRect(titleLike);
    if (r) { osScroll(r.left + relX, r.top + relY, delta); }
    else { _failures.push('absCoord-fallback'); osScroll(absX, absY, delta); }
}
function osDragRel(titleLike, relX1, relY1, relX2, relY2, absX1, absY1, absX2, absY2) {
    const r = _resolveWinRect(titleLike);
    if (r) { osDrag(r.left + relX1, r.top + relY1, r.left + relX2, r.top + relY2); }
    else { _failures.push('absCoord-fallback'); osDrag(absX1, absY1, absX2, absY2); }
}

// Qt/QML controls only: el.setValue() (UIA ValuePattern) crashes WinAppDriver
// with an unhandled "unknown error" on custom text inputs (confirmed
// 2026-07-06 — FreeDM's BaseTextField_QMLTYPE_31 threw mid-suite and aborted
// the whole test). OS-level keystrokes after a real click sidestep the
// unsupported UIA pattern entirely.
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
// _appHwnd (resolved by initAppHwnd() in beforeAll) identifies the main
// app window deterministically for owner-PID scoping.
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

class FreeDMPageById {
    async click1() {
        osClickRel("Free Download Manager", 35, 187, 263, 242);
    }

    async click2() {
        osClickRel("Free Download Manager", 35, 187, 263, 242);
    }

    async click3() {
        osClickRel("Free Download Manager", 35, 187, 263, 242);
    }

    async click4() {
        osClickRel("Free Download Manager", 35, 187, 263, 242, 'left', 2);
    }

    async click5() {
        osClickRel("Free Download Manager", 30, 216, 258, 271);
    }

    async click6() {
        osClickRel("Free Download Manager", 30, 216, 258, 271);
    }

    async click7() {
        osClickRel("Free Download Manager", 30, 216, 258, 271, 'left', 2);
    }

    async click8() {
        osClickRel("Free Download Manager", 44, 55, 272, 110);
    }

    async click9() {
        osClickRel("Free Download Manager", 44, 55, 272, 110);
    }

    async click10() {
        osClickRel("Free Download Manager", 44, 55, 272, 110, 'left', 2);
    }

    async click11() {
        osClickRel("Free Download Manager", 601, 54, 829, 109);
    }

    async click12() {
        osClickRel("Free Download Manager", 326, 338, 554, 393);
    }

    async type13(value) {
        osActivate("Free Download Manager");
        osClickRel("Free Download Manager", 420, 343, 648, 398);
        osType(value);
    }

    async click14() {
        osClickRel("Free Download Manager", 715, 391, 943, 446);
    }

    async drag15() {
        osDragRel('Free Download Manager', 335, 322, 194, 317, 563, 377, 422, 372);
    }

    async click16() {
        osClickRel("Free Download Manager", 644, 387, 872, 442);
    }
}

describe('FreeDMTestById', () => {
    beforeAll(async () => {
        _warmupPowerShell();
        await initAppHwnd();
        normalizeWindowSimple({"left":228,"top":55,"width":965,"height":648});
    });

    it('should replay recorded flow', async () => {
        const page = new FreeDMPageById();

            osActivate("Free Download Manager");
            await _step('1:click ', () => page.click1());
            await _step('2:click ', () => page.click2());
            await _step('3:click ', () => page.click3());
            await _step('4:doubleClick ', () => page.click4());
            await _step('5:click ', () => page.click5());
            await _step('6:click ', () => page.click6());
            await _step('7:doubleClick ', () => page.click7());
            await _step('8:click ', () => page.click8());
            await _step('9:click ', () => page.click9());
            await _step('10:doubleClick ', () => page.click10());
            await _step('11:click ', () => page.click11());
            await _step('12:click ', () => page.click12());
            await _step('13:type asdfasdfasdf', () => page.type13('asdfasdfasdf'));
            await _step('14:click 다운로드 추가', () => page.click14());
            await _step('15:drag (563,377)->(422,372)', () => page.drag15());
            await _step('16:click 다운로드 추가', () => page.click16());

            if (_warnings.length) console.warn('[replay-warnings]', _warnings);
            expect(_failures).toEqual([]);
    });
});
