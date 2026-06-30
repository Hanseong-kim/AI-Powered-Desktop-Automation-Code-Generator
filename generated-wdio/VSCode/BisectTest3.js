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

// NOTE: getWindowSession and getCenter NOT included here

describe('BisectTest3', () => {
    after(async () => {
        for (const sid of Object.values(_sessionIds)) {
            try { await fetch(`${_APPIUM}/session/${sid}`, { method: 'DELETE' }); } catch {}
        }
    });

    it('should run without getWindowSession', async () => {
        console.log('[bisect3] _createSession type:', typeof _createSession);
        expect(typeof _createSession).toBe('function');
    });
});
