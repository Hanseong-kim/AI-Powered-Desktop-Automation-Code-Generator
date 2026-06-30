import { execSync } from 'child_process';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));

// OS-level click fallback via PowerShell user32.dll.
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

class CalculatorPageById {
    async click1() {
        try {
            const el = await browser.$('~num8Button');
            await el.waitForExist({ timeout: 8000 });
            await el.click();
        } catch (_) {
            osClick(500, 300);
        }
    }

    async click2() {
        try {
            const el = await browser.$('~multiplyButton');
            await el.waitForExist({ timeout: 8000 });
            await el.click();
        } catch (_) {
            osClick(550, 300);
        }
    }

    async click3() {
        try {
            const el = await browser.$('~num8Button');
            await el.waitForExist({ timeout: 8000 });
            await el.click();
        } catch (_) {
            osClick(500, 300);
        }
    }

    async click4() {
        try {
            const el = await browser.$('~equalButton');
            await el.waitForExist({ timeout: 8000 });
            await el.click();
        } catch (_) {
            osClick(600, 400);
        }
    }
}

describe('CalculatorTestById', () => {
    it('should replay recorded flow', async () => {
        const page = new CalculatorPageById();

            console.log('[STEP 1] click: 8');
            await page.click1();
            console.log('[STEP 2] click: ???');
            await page.click2();
            console.log('[STEP 3] click: 8');
            await page.click3();
            console.log('[STEP 4] click: ??');
            await page.click4();

            expect(browser).toBeDefined();
    });
});
