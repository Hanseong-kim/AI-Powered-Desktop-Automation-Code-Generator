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

// OS-level window activation via PowerShell user32.dll. Simple-mode tests
// never run launchApp's foreground/normalize step, so a freshly launched app
// can be spawned behind other windows or off-position — bring it forward
// before the first click so OS-level input actually reaches it.
function osActivate(titleLike) {
    try {
        execSync(
            `powershell -NoProfile -File "${join(__dirname, 'osActivate.ps1')}" -titleLike "${titleLike}"`,
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

class CalculatorPageById {
    async click1() {
        try {
            const el = await browser.$('~num7Button');
            await el.waitForExist({ timeout: 8000 });
            await el.click();
        } catch (e) {
            console.warn('[click1] FALLBACK TO ABSOLUTE COORDINATES: [2946, -29] — xpath click failed:', String(e.message || e).substring(0, 100));
            osClick(2946, -29);
        }
    }

    async click2() {
        try {
            const el = await browser.$('~num8Button');
            await el.waitForExist({ timeout: 8000 });
            await el.click();
        } catch (e) {
            console.warn('[click2] FALLBACK TO ABSOLUTE COORDINATES: [3048, -26] — xpath click failed:', String(e.message || e).substring(0, 100));
            osClick(3048, -26);
        }
    }

    async click3() {
        try {
            const el = await browser.$('~num9Button');
            await el.waitForExist({ timeout: 8000 });
            await el.click();
        } catch (e) {
            console.warn('[click3] FALLBACK TO ABSOLUTE COORDINATES: [3131, -26] — xpath click failed:', String(e.message || e).substring(0, 100));
            osClick(3131, -26);
        }
    }

    async click4() {
        try {
            const el = await browser.$('~multiplyButton');
            await el.waitForExist({ timeout: 8000 });
            await el.click();
        } catch (e) {
            console.warn('[click4] FALLBACK TO ABSOLUTE COORDINATES: [3212, -24] — xpath click failed:', String(e.message || e).substring(0, 100));
            osClick(3212, -24);
        }
    }

    async click5() {
        try {
            const el = await browser.$('~num6Button');
            await el.waitForExist({ timeout: 8000 });
            await el.click();
        } catch (e) {
            console.warn('[click5] FALLBACK TO ABSOLUTE COORDINATES: [3117, 49] — xpath click failed:', String(e.message || e).substring(0, 100));
            osClick(3117, 49);
        }
    }

    async click6() {
        try {
            const el = await browser.$('~num5Button');
            await el.waitForExist({ timeout: 8000 });
            await el.click();
        } catch (e) {
            console.warn('[click6] FALLBACK TO ABSOLUTE COORDINATES: [3023, 57] — xpath click failed:', String(e.message || e).substring(0, 100));
            osClick(3023, 57);
        }
    }

    async click7() {
        try {
            const el = await browser.$('~num4Button');
            await el.waitForExist({ timeout: 8000 });
            await el.click();
        } catch (e) {
            console.warn('[click7] FALLBACK TO ABSOLUTE COORDINATES: [2938, 57] — xpath click failed:', String(e.message || e).substring(0, 100));
            osClick(2938, 57);
        }
    }

    async click8() {
        try {
            const el = await browser.$('~divideButton');
            await el.waitForExist({ timeout: 8000 });
            await el.click();
        } catch (e) {
            console.warn('[click8] FALLBACK TO ABSOLUTE COORDINATES: [3217, -94] — xpath click failed:', String(e.message || e).substring(0, 100));
            osClick(3217, -94);
        }
    }

    async click9() {
        try {
            const el = await browser.$('~num1Button');
            await el.waitForExist({ timeout: 8000 });
            await el.click();
        } catch (e) {
            console.warn('[click9] FALLBACK TO ABSOLUTE COORDINATES: [2926, 109] — xpath click failed:', String(e.message || e).substring(0, 100));
            osClick(2926, 109);
        }
    }

    async click10() {
        try {
            const el = await browser.$('~num2Button');
            await el.waitForExist({ timeout: 8000 });
            await el.click();
        } catch (e) {
            console.warn('[click10] FALLBACK TO ABSOLUTE COORDINATES: [3003, 99] — xpath click failed:', String(e.message || e).substring(0, 100));
            osClick(3003, 99);
        }
    }

    async click11() {
        try {
            const el = await browser.$('~num3Button');
            await el.waitForExist({ timeout: 8000 });
            await el.click();
        } catch (e) {
            console.warn('[click11] FALLBACK TO ABSOLUTE COORDINATES: [3099, 91] — xpath click failed:', String(e.message || e).substring(0, 100));
            osClick(3099, 91);
        }
    }

    async click12() {
        try {
            const el = await browser.$('~equalButton');
            await el.waitForExist({ timeout: 8000 });
            await el.click();
        } catch (e) {
            console.warn('[click12] FALLBACK TO ABSOLUTE COORDINATES: [3211, 173] — xpath click failed:', String(e.message || e).substring(0, 100));
            osClick(3211, 173);
        }
    }
}

describe('CalculatorTestById', () => {
    it('should replay recorded flow', async () => {
        const page = new CalculatorPageById();

            osActivate("계산기");
            console.log('[STEP 1] click: 7');
            await page.click1();
            console.log('[STEP 2] click: 8');
            await page.click2();
            console.log('[STEP 3] click: 9');
            await page.click3();
            console.log('[STEP 4] click: 곱');
            await page.click4();
            console.log('[STEP 5] click: 6');
            await page.click5();
            console.log('[STEP 6] click: 5');
            await page.click6();
            console.log('[STEP 7] click: 4');
            await page.click7();
            console.log('[STEP 8] click: 나누기');
            await page.click8();
            console.log('[STEP 9] click: 1');
            await page.click9();
            console.log('[STEP 10] click: 2');
            await page.click10();
            console.log('[STEP 11] click: 3');
            await page.click11();
            console.log('[STEP 12] click: 일치');
            await page.click12();

            expect(_failures).toEqual([]);
    });
});
