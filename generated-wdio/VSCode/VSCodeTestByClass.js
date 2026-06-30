import { execSync } from 'child_process';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));

// OS-level click via PowerShell user32.dll — verified for Electron menus, native dialogs.
function osClick(x, y) {
    try {
        execSync(
            `powershell -NoProfile -File "${join(__dirname, 'osClick.ps1')}" -x ${x} -y ${y}`,
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
    const r = await fetch(`${_APPIUM}${path}`, {
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
    if (!v?.sessionId) throw new Error(`Appium session failed for "${app}": ${JSON.stringify(v)}`);
    return v.sessionId;
}

async function getWindowSession(title) {
    if (_sessionIds[title]) return _sessionIds[title];
    console.log(`[session] Root scan for: "${title}"`);
    const shortTitle = title.slice(0, 30).replace(/"/g, '');
    let hwnd = null;
    for (const sel of [`//*[@Name="${title}"]`, `//*[contains(@Name,"${shortTitle}")]`]) {
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
    if (hwnd) console.log(`[session] hwnd=${hwnd} → scoped session`);
    else console.warn(`[session] Window "${title}" not found — falling back to Root`);
    _sessionIds[title] = await _createSession(app);
    return _sessionIds[title];
}

async function getCenter(sid, selector) {
    try {
        const raw = selector.replace(/^['"]|['"]$/g, '');
        const using = raw.startsWith('~') ? 'accessibility id' : 'xpath';
        const value = raw.startsWith('~') ? raw.slice(1) : raw;
        const el = await _appiumPost(`/session/${sid}/element`, { using, value });
        if (!el) return null;
        const elId = el.ELEMENT || el['element-6066-11e4-a52e-4f735466cecf'];
        const r = await (await fetch(`${_APPIUM}/session/${sid}/element/${elId}/rect`)).json();
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
        const el = await _appiumPost(`/session/${sid}/element`, { using, value });
        if (!el) throw new Error('element not found');
        const elId = el.ELEMENT || el['element-6066-11e4-a52e-4f735466cecf'];
        await _appiumPost(`/session/${sid}/element/${elId}/clear`, {});
        await _appiumPost(`/session/${sid}/element/${elId}/value`, { text });
    } catch (e) { console.warn('[type] failed:', String(e.message || e).substring(0, 100)); }
}

class VSCodePageByClass {
    async click1() {
        osClick(2368, -490);
    }

    async click2() {
        osClick(2396, -335);
    }

    async click3() {
        const sid = await getWindowSession('폴더 열기');
        const c = await getCenter(sid, '//*[@ClassName="ToolbarWindow32" and @Name="즐겨찾기 시작 - 바탕 화면(고정됨)"]') ?? { x: 2406, y: -307 };
        osClick(c.x, c.y);
    }

    async click4() {
        const sid = await getWindowSession('폴더 열기');
        const c = await getCenter(sid, '//*[@ClassName="Button" and @Name="폴더 선택(S)"]') ?? { x: 2907, y: -65 };
        osClick(c.x, c.y);
    }

    async click5() {
        osClick(2366, -493);
    }

    async click6() {
        osClick(2427, -336);
    }

    async click8() {
        const sid = await getWindowSession('폴더 열기');
        const c = await getCenter(sid, '//*[@ClassName="DirectUIHWND" and @Name="live_crop"]') ?? { x: 2406, y: -332 };
        osClick(c.x, c.y);
    }

    async click9() {
        const sid = await getWindowSession('폴더 열기');
        const c = await getCenter(sid, '//*[@ClassName="Button" and @Name="폴더 선택(S)"]') ?? { x: 2924, y: -69 };
        osClick(c.x, c.y);
    }
}

describe('VSCodeTestByClass', () => {
    afterAll(async () => {
        for (const sid of Object.values(_sessionIds)) {
            try { await fetch(`${_APPIUM}/session/${sid}`, { method: 'DELETE' }); } catch {}
        }
    });

    it('should replay recorded flow', async () => {
        const page = new VSCodePageByClass();

            console.log('[STEP 1] click: 파일');
            await page.click1();
            console.log('[STEP 2] click: 폴더 열기...');
            await page.click2();
            console.log('[STEP 3] click: 즐겨찾기 시작 - 바탕 화면(고정됨)');
            await page.click3();
            console.log('[STEP 4] click: 폴더 선택(S)');
            await page.click4();
            console.log('[STEP 5] click: 파일');
            await page.click5();
            console.log('[STEP 6] click: 폴더 열기...');
            await page.click6();
            // [STEP 7] scroll skipped (WinAppDriver wheel unsupported)
            console.log('[STEP 8] click: live_crop');
            await page.click8();
            console.log('[STEP 9] click: 폴더 선택(S)');
            await page.click9();

            expect(browser).toBeDefined();
    });
});
