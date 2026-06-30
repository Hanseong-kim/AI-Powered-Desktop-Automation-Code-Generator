import { execSync } from 'child_process';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const _APPIUM = 'http://127.0.0.1:4723';
const _sessionIds = {};

async function _appiumPost(path, body) {
    const r = await fetch(`${_APPIUM}${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    return (await r.json()).value;
}

async function _createSession(app) {
    const v = await _appiumPost('/session', { capabilities: { alwaysMatch: { platformName: 'Windows', 'appium:automationName': 'Windows', 'appium:app': app, 'appium:newCommandTimeout': 60000 } } });
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
                if (raw) { hwnd = '0x' + parseInt(raw, 10).toString(16); break; }
            }
        } catch {}
    }
    const app = hwnd || 'Root';
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

describe('BisectTest2', () => {
    after(async () => {
        for (const sid of Object.values(_sessionIds)) {
            try { await fetch(`${_APPIUM}/session/${sid}`, { method: 'DELETE' }); } catch {}
        }
    });

    it('should run with full session helpers', async () => {
        console.log('[bisect2] all helpers loaded, typeof getWindowSession:', typeof getWindowSession);
        expect(typeof getWindowSession).toBe('function');
        expect(typeof getCenter).toBe('function');
    });
});
