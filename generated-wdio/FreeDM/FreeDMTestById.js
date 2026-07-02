import { execSync } from 'child_process';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));

// 주입/헬스 실패 수집 — 마지막에 실질 assert로 검증.
const _failures = [];

// OS-level click fallback via PowerShell user32.dll.
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

// 실제 마우스 클릭(osClick)을 요소 중심 좌표에 쏘기 위해 wdio의 표준 getRect로
// 현재 화면상 위치를 조회한다 — el.click()(UIA Invoke)은 SetCursorPos를 하지
// 않아 호버 의존 UI(Qt/QML 케밥 버튼 등)가 렌더링되지 않는 문제가 있었다.
async function getCenterSimple(selector) {
    try {
        const el = await browser.$(selector);
        await el.waitForExist({ timeout: 8000 });
        const rect = await el.getRect();
        return { x: Math.round(rect.x + rect.width / 2), y: Math.round(rect.y + rect.height / 2) };
    } catch { return null; }
}

class FreeDMPageById {
    async click1() {
        osClick(3119, -456);
    }

    async click2() {
        const c = await getCenterSimple('~AppQApplication.ApplicationWindow_QMLTYPE_210_QML_223.ApplicationWindowContentControl.WaStackView_QMLTYPE_197.DownloadsPage_QMLTYPE_87.DownloadsViewItem_V2_QMLTYPE_362');
        if (c) osClick(c.x, c.y);
        else osClick(2451, -353);
    }

    async click3() {
        const c = await getCenterSimple('~AppQApplication.ApplicationWindow_QMLTYPE_210_QML_223.ApplicationWindowContentControl.WaStackView_QMLTYPE_197.DownloadsPage_QMLTYPE_87.DownloadsViewItem_V2_QMLTYPE_362');
        if (c) osClick(c.x, c.y);
        else osClick(3026, -384);
    }

    async click4() {
        const c = await getCenterSimple('~AppQApplication.ApplicationWindow_QMLTYPE_210_QML_223.DownloadsViewItemContextMenu.BaseContextMenuItem_QMLTYPE_33');
        if (c) osClick(c.x, c.y);
        else osClick(2921, -152);
    }

    async click5() {
        const c = await getCenterSimple('~AppQApplication.ApplicationWindow_QMLTYPE_210_QML_223.ApplicationWindowContentControl.WaStackView_QMLTYPE_197.DownloadsPage_QMLTYPE_87.DownloadsViewItem_V2_QMLTYPE_362.CheckBox_V2_QMLTYPE_40.BaseLabel_QMLTYPE_9');
        if (c) osClick(c.x, c.y);
        else osClick(2427, -350);
    }

    async click6() {
        const c = await getCenterSimple('~AppQApplication.ApplicationWindow_QMLTYPE_210_QML_223.ApplicationWindowContentControl.WaStackView_QMLTYPE_197.DownloadsPage_QMLTYPE_87.DownloadsViewItem_V2_QMLTYPE_362.CheckBox_V2_QMLTYPE_40.BaseLabel_QMLTYPE_9');
        if (c) osClick(c.x, c.y);
        else osClick(2421, -387);
    }

    async click7() {
        const c = await getCenterSimple('~AppQApplication.ApplicationWindow_QMLTYPE_210_QML_223.ApplicationWindowContentControl.WaStackView_QMLTYPE_197.DownloadsPage_QMLTYPE_87.DownloadsViewItem_V2_QMLTYPE_362.CheckBox_V2_QMLTYPE_40.BaseLabel_QMLTYPE_9');
        if (c) osClick(c.x, c.y);
        else osClick(2421, -387);
    }

    async click8() {
        const c = await getCenterSimple('~AppQApplication.ApplicationWindow_QMLTYPE_210_QML_223.ApplicationWindowContentControl.WaStackView_QMLTYPE_197.DownloadsPage_QMLTYPE_87.DownloadsViewItem_V2_QMLTYPE_362.CheckBox_V2_QMLTYPE_40.BaseLabel_QMLTYPE_9');
        if (c) osClick(c.x, c.y);
        else osClick(2424, -360);
    }
}

describe('FreeDMTestById', () => {
    it('should replay recorded flow', async () => {
        const page = new FreeDMPageById();

            console.log('[STEP 1] click: ');
            await page.click1();
            console.log('[STEP 2] click: ');
            await page.click2();
            console.log('[STEP 3] click: ');
            await page.click3();
            console.log('[STEP 4] click: 링크 복사');
            await page.click4();
            console.log('[STEP 5] click: ');
            await page.click5();
            console.log('[STEP 6] click: ');
            await page.click6();
            console.log('[STEP 7] click: ');
            await page.click7();
            console.log('[STEP 8] click: ');
            await page.click8();

            expect(_failures).toEqual([]);
    });
});
