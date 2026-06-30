// Probe D: runtime getLocation() to identify coordinate system, then click with pen type.
// WinAppDriver supports: touch, pen. Rejects: mouse, key.
// VSCode ignores touch (WM_TOUCH). pen maps to WM_POINTER with POINTER_TYPE_PEN.
// Question 1: does getLocation() return window-relative or screen-absolute?
// Question 2: does pen type actually click in VSCode?

describe('Probe D — runtime getLocation + pen type', () => {
    it('log coordinate system + click with pen', async () => {
        await browser.pause(3000); // wait for VSCode to fully load

        let el;
        try {
            // Try broad selector first
            el = await browser.$('//*[@Name="파일"]');
            await el.waitForExist({ timeout: 8000 });
            console.log('[PROBE D] found element by Name="파일"');
        } catch {
            console.log('[PROBE D] Name="파일" not found — dumping root children names');
            try {
                const root = await browser.$('//*');
                console.log('[PROBE D] root found');
            } catch (e2) {
                console.log('[PROBE D] root also not found:', e2.message);
            }
            expect(true).toBe(true);
            return;
        }

        const loc = await el.getLocation();
        const size = await el.getSize();
        const cx = Math.round(loc.x + size.width / 2);
        const cy = Math.round(loc.y + size.height / 2);

        console.log(`[PROBE D] getLocation x=${loc.x} y=${loc.y}  size=${size.width}x${size.height}`);
        console.log(`[PROBE D] center cx=${cx} cy=${cy}`);
        console.log(`[PROBE D] ref window-rel  relX=435 relY=160  match=${Math.abs(cx-435)<40 && Math.abs(cy-160)<40}`);
        console.log(`[PROBE D] ref screen-abs  x=2346 y=-526     match=${Math.abs(cx-2346)<40 && Math.abs(cy+526)<40}`);

        // Click with pen type (WinAppDriver accepts pen; maps to WM_POINTER pen)
        try {
            await browser.action('pointer', { parameters: { pointerType: 'pen' } })
                .move({ x: cx, y: cy, origin: 'viewport' })
                .down().up()
                .perform();
            await browser.pause(1000);
            console.log('[PROBE D] pen click: RESULT null (succeeded)');

            try {
                const menuItem = await browser.$('//*[@Name="새 텍스트 파일"]');
                const visible = await menuItem.isDisplayed();
                console.log(`[PROBE D] 새 텍스트 파일 visible after pen click: ${visible}`);
            } catch {
                console.log('[PROBE D] 새 텍스트 파일 not found after pen click');
            }
        } catch (e) {
            console.log(`[PROBE D] pen click error: ${e.message}`);
        }

        // Also try el.click() — uses /element/{id}/click, not W3C Actions
        try {
            await el.click();
            await browser.pause(1000);
            console.log('[PROBE D] el.click() succeeded');
            try {
                const menuItem = await browser.$('//*[@Name="새 텍스트 파일"]');
                const visible = await menuItem.isDisplayed();
                console.log(`[PROBE D] 새 텍스트 파일 visible after el.click(): ${visible}`);
            } catch {
                console.log('[PROBE D] 새 텍스트 파일 not found after el.click()');
            }
        } catch (e) {
            console.log(`[PROBE D] el.click() error: ${e.message}`);
        }

        expect(true).toBe(true);
    });
});
