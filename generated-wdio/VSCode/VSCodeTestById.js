import { execSync, spawn } from 'child_process';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));

// 주입/헬스 실패 수집 — 마지막에 실질 assert로 검증.
const _failures = [];

// OS-level click via PowerShell user32.dll — verified for Electron menus, native dialogs.
function osClick(x, y, button = 'left', clicks = 1) {
    try {
        execSync(
            `powershell -NoProfile -File "${join(__dirname, 'osClick.ps1')}" -x ${x} -y ${y} -button ${button} -clicks ${clicks}`,
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
            `powershell -NoProfile -File "${join(__dirname, 'osScroll.ps1')}" -x ${x} -y ${y} -delta ${delta}`,
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
    else console.warn(`[session] Window "${title}" not found — falling back to Root`);
    try {
        const sid = await _createSession(app);
        _sessionIds[title] = { sid, rootElId: null };
    } catch (e) {
        console.warn(`[session] scoped session failed (${e.message}) — reusing Root session for "${title}"`);
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
        const r = await (await fetch(`${_APPIUM}/session/${sid}/element/${elId}/rect`)).json();
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

function _listWindowHwnds(frag) {
    if (!frag) return [];
    try {
        const out = execSync(
            `powershell -NoProfile -File "${join(__dirname, 'osWindowRect.ps1')}" -titleLike "${frag}" -listOnly`,
            { stdio: 'pipe', timeout: 5000 }
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
            { stdio: 'pipe', timeout: 5000 }
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
    const baseline = new Set(_listWindowHwnds(titleFrag));
    try {
        spawn(exePath, args, { detached: true, stdio: 'ignore' }).unref();
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
                console.log(`[launch] tracking new window hwnd=${fresh}`);
            }
        }
        if (_resolveWinRect(titleFrag)) {
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
            `powershell -NoProfile -File "${join(__dirname, 'osType.ps1')}" -b64 "${b64}"`,
            { stdio: 'pipe', timeout: 8000 }
        );
    } catch (e) {
        _failures.push('osType');
        console.warn('[osType] failed:', String(e.message || e).substring(0, 100));
    }
}

class VSCodePageById {
    async click1() {
        let s = await getWindowSession('Visual Studio Code');
        let c = await getCenter(s.sid, s.rootElId, '//*[@Name="데스크톱 1"]');
        if (!c) {
            delete _sessionIds['Visual Studio Code'];
            s = await getWindowSession('Visual Studio Code');
            c = await getCenter(s.sid, s.rootElId, '//*[@Name="데스크톱 1"]');
        }
        c = c ?? { x: 1615, y: 67 };
        osClick(c.x, c.y);
    }

    async click2() {
        osClickRel('시작 - Visual Studio Code', 89, 40, 82, 33);
    }

    async click3() {
        osClickRel('시작 - Visual Studio Code', 130, 224, 123, 217);
    }

    async scroll4() {
        osScrollRel('폴더 열기', 108, 313, 108, 313, -4200);
    }

    async click5() {
        let s = await getWindowSession('폴더 열기');
        let c = await getCenter(s.sid, s.rootElId, '//*[@Name="탐색 창"]');
        if (!c) {
            delete _sessionIds['폴더 열기'];
            s = await getWindowSession('폴더 열기');
            c = await getCenter(s.sid, s.rootElId, '//*[@Name="탐색 창"]');
        }
        c = c ?? { x: 106, y: 357 };
        osClick(c.x, c.y);
    }

    async click6() {
        let s = await getWindowSession('폴더 열기');
        let c = await getCenter(s.sid, s.rootElId, '~System.ItemNameDisplay');
        if (!c) {
            delete _sessionIds['폴더 열기'];
            s = await getWindowSession('폴더 열기');
            c = await getCenter(s.sid, s.rootElId, '~System.ItemNameDisplay');
        }
        c = c ?? { x: 327, y: 192 };
        osClick(c.x, c.y);
    }

    async click7() {
        osClickRel('폴더 열기', 327, 192, 327, 192);
    }

    async click8() {
        osClickRel('폴더 열기', 327, 192, 327, 192, 'left', 2);
    }

    async click9() {
        let s = await getWindowSession('폴더 열기');
        let c = await getCenter(s.sid, s.rootElId, '//*[@Name="항목 보기"]');
        if (!c) {
            delete _sessionIds['폴더 열기'];
            s = await getWindowSession('폴더 열기');
            c = await getCenter(s.sid, s.rootElId, '//*[@Name="항목 보기"]');
        }
        c = c ?? { x: 302, y: 269 };
        osClick(c.x, c.y);
    }

    async click10() {
        osClickRel('폴더 열기', 302, 269, 302, 269);
    }

    async click11() {
        osClickRel('폴더 열기', 302, 269, 302, 269, 'left', 2);
    }

    async scroll12() {
        osScrollRel('폴더 열기', 348, 299, 348, 299, -5160);
    }

    async click13() {
        let s = await getWindowSession('폴더 열기');
        let c = await getCenter(s.sid, s.rootElId, '~ScrollbarThumb');
        if (!c) {
            delete _sessionIds['폴더 열기'];
            s = await getWindowSession('폴더 열기');
            c = await getCenter(s.sid, s.rootElId, '~ScrollbarThumb');
        }
        c = c ?? { x: 316, y: 369 };
        osClick(c.x, c.y);
    }

    async click14() {
        osClickRel('폴더 열기', 710, 558, 703, 551);
    }
}

describe('VSCodeTestById', () => {
    beforeAll(async () => {
        const { hostname, port } = browser.options;
        _APPIUM = `http://${hostname || '127.0.0.1'}:${port || 4723}`;
        console.log(`[session] Appium endpoint resolved to ${_APPIUM}`);
    });

    afterAll(async () => {
        for (const { sid } of Object.values(_sessionIds)) {
            if (sid === browser.sessionId) continue;
            try { await fetch(`${_APPIUM}/session/${sid}`, { method: 'DELETE' }); } catch {}
        }
    });

    it('should replay recorded flow', async () => {
        const page = new VSCodePageById();

            console.log('[STEP 1] click: 데스크톱 1');
            await page.click1();
            console.log('[STEP 2] click: 파일');
            await page.click2();
            console.log('[STEP 3] click: 편집기를 사용하여 작업 속도를 향상하는 방법에 관한 개요입니다.');
            await page.click3();
            console.log('[STEP 4] scroll: delta=-4200');
            await page.scroll4();
            console.log('[STEP 5] click: 탐색 창');
            await page.click5();
            console.log('[STEP 6] click: 이름');
            await page.click6();
            console.log('[STEP 7] click: ');
            await page.click7();
            console.log('[STEP 8] click: ');
            await page.click8();
            console.log('[STEP 9] click: 항목 보기');
            await page.click9();
            console.log('[STEP 10] click: ');
            await page.click10();
            console.log('[STEP 11] click: ');
            await page.click11();
            console.log('[STEP 12] scroll: delta=-5160');
            await page.scroll12();
            console.log('[STEP 13] click: 위치');
            await page.click13();
            console.log('[STEP 14] click: ');
            await page.click14();

            expect(_failures).toEqual([]);
    });
});
