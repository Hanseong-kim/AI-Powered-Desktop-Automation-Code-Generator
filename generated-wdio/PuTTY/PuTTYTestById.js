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
            `powershell -NoProfile -File "${join(__dirname, 'osScroll.ps1')}" -hwnd ${hwnd} -selB64 "${selB64}" -delta ${delta}`,
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

class PuTTYPageById {
    async click1() {
        const el = await browser.$('//*[@Name="Logging"]');
        await el.waitForExist({ timeout: 8000 });
        await el.click();
    }

    async click2() {
        const el = await browser.$('//*[@Name="Keyboard"]');
        await el.waitForExist({ timeout: 8000 });
        await el.click();
    }

    async click4() {
        const el = await browser.$('//*[@Name="Appearance"]');
        await el.waitForExist({ timeout: 8000 });
        await el.click();
    }

    async click5() {
        const el = await browser.$('//*[@Name="Behaviour"]');
        await el.waitForExist({ timeout: 8000 });
        await el.click();
    }

    async click6() {
        const el = await browser.$('//*[@Name="Selection"]');
        await el.waitForExist({ timeout: 8000 });
        await el.click();
    }

    async click7() {
        const el = await browser.$('//*[@Name="Selection"]');
        await el.waitForExist({ timeout: 8000 });
        await el.click();
    }

    async click8() {
        const el = await browser.$('//*[@Name="System menu appears on ALT-Space"]');
        await el.waitForExist({ timeout: 8000 });
        await el.click();
        await el.click();
    }

    async click9() {
        const el = await browser.$('//*[@Name="System menu appears on ALT-Space"]');
        await el.waitForExist({ timeout: 8000 });
        await el.click();
    }

    async click10() {
        const el = await browser.$('//*[@Name="Open"]');
        await el.waitForExist({ timeout: 8000 });
        await el.click();
    }
}

describe('PuTTYTestById', () => {
    beforeAll(async () => {
        _warmupPowerShell();
        await initAppHwnd();
        normalizeWindowSimple({"left":521,"top":211,"width":494,"height":441});
    });

    it('should replay recorded flow', async () => {
        const page = new PuTTYPageById();

            osActivate("PuTTY Configuration");
            await _step('1:click Logging', () => page.click1());
            await _step('2:click Keyboard', () => page.click2());
            // [STEP 3] click: no selector/anchor captured — coordinate replay is forbidden (2026-07-10)
            _failures.push('3:click:no-selector');
            await _step('4:click Appearance', () => page.click4());
            await _step('5:click Behaviour', () => page.click5());
            await _step('6:click Selection', () => page.click6());
            await _step('7:click Selection', () => page.click7());
            await _step('8:doubleClick System menu appears on ALT-Space', () => page.click8());
            await _step('9:click System menu appears on ALT-Space', () => page.click9());
            await _step('10:click Open', () => page.click10());

            if (_warnings.length) console.warn('[replay-warnings]', _warnings);
            expect(_failures).toEqual([]);
    });
});
