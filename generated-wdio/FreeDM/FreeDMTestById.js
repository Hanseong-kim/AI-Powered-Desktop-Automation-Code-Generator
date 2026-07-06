import { execSync } from 'child_process';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));

// 주입/헬스 실패 수집 — 마지막에 실질 assert로 검증.
const _failures = [];

// OS-level click fallback via PowerShell user32.dll.
function osClick(x, y, button = 'left', clicks = 1) {
    try {
        const out = execSync(
            `powershell -NoProfile -File "${join(__dirname, 'osClick.ps1')}" -x ${x} -y ${y} -button ${button} -clicks ${clicks}`,
            { stdio: 'pipe', timeout: 5000 }
        );
        const diag = out.toString().trim();
        if (diag) console.log(diag);
    } catch (e) {
        _failures.push('osClick');
        console.warn('[osClick] failed:', String(e.message || e).substring(0, 100));
    }
}

// OS-level mouse wheel via PowerShell user32.dll (WinAppDriver has no wheel API).
function osScroll(x, y, delta) {
    try {
        execSync(
            `powershell -NoProfile -File "${join(__dirname, 'osScroll.ps1')}" -x ${x} -y ${y} -delta ${delta}`,
            { stdio: 'pipe', timeout: 5000 }
        );
    } catch (e) {
        _failures.push('osScroll');
        console.warn('[osScroll] failed:', String(e.message || e).substring(0, 100));
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
            { stdio: 'pipe', timeout: 5000 }
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
            { stdio: 'pipe', timeout: 5000 }
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
            { stdio: 'pipe', timeout: 8000 }
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
    if (r) osClick(r.left + relX, r.top + relY, button, clicks);
    else   osClick(absX, absY, button, clicks);   // window not found — absolute fallback only
}
function osScrollRel(titleLike, relX, relY, absX, absY, delta) {
    const r = _resolveWinRect(titleLike);
    if (r) osScroll(r.left + relX, r.top + relY, delta);
    else   osScroll(absX, absY, delta);
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
            { stdio: 'pipe', timeout: 8000 }
        );
    } catch (e) {
        _failures.push('osType');
        console.warn('[osType] failed:', String(e.message || e).substring(0, 100));
    }
}

class FreeDMPageById {
    async click1() {
        osClickRel("Free Download Manager", 28, 199, 2125, -324);
    }

    async click2() {
        osClickRel("Free Download Manager", 40, 233, 2137, -290);
    }

    async click3() {
        osClickRel("Free Download Manager", 46, 59, 2143, -464);
    }

    async click4() {
        osClickRel("Free Download Manager", 618, 57, 2715, -466);
    }

    async click5() {
        osClickRel("Free Download Manager", 346, 345, 2443, -178);
    }

    async type6(value) {
        osActivate("Free Download Manager");
        osClickRel("Free Download Manager", 422, 343, 2519, -180);
        osType(value);
    }

    async click7() {
        osClickRel("Free Download Manager", 716, 395, 2813, -128);
    }

    async click8() {
        osClickRel("Free Download Manager", 643, 409, 2740, -114);
    }
}

describe('FreeDMTestById', () => {
    beforeAll(async () => {
        await initAppHwnd();
        normalizeWindowSimple({"left":2097,"top":-523,"width":968,"height":647});
    });

    it('should replay recorded flow', async () => {
        const page = new FreeDMPageById();

            osActivate("Free Download Manager");
            console.log('[STEP 1] click: ');
            await page.click1();
            console.log('[STEP 2] click: ');
            await page.click2();
            console.log('[STEP 3] click: ');
            await page.click3();
            console.log('[STEP 4] click: ');
            await page.click4();
            console.log('[STEP 5] click: ');
            await page.click5();
            console.log('[STEP 6] type: asdfasdf');
            await page.type6('asdfasdf');
            console.log('[STEP 7] click: 다운로드 추가');
            await page.click7();
            console.log('[STEP 8] click: 다운로드 추가');
            await page.click8();

            expect(_failures).toEqual([]);
    });
});
