import { execSync } from 'child_process';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));

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

describe('BisectTest', () => {
    it('should run with top-level imports and helpers', async () => {
        console.log('[bisect] __dirname:', __dirname);
        console.log('[bisect] _APPIUM:', _APPIUM);
        console.log('[bisect] typeof _appiumPost:', typeof _appiumPost);
        expect(1).toBe(1);
    });
});
