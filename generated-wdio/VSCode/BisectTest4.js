import { execSync } from 'child_process';
const _APPIUM = 'http://127.0.0.1:4723';
const _sessionIds = {};

// Only the after() hook — no _createSession

describe('BisectTest4', () => {
    after(async () => {
        for (const sid of Object.values(_sessionIds)) {
            try { await fetch(`${_APPIUM}/session/${sid}`, { method: 'DELETE' }); } catch {}
        }
    });

    it('should run with just after hook', async () => {
        console.log('[bisect4] RUNNING');
        expect(1).toBe(1);
    });
});
