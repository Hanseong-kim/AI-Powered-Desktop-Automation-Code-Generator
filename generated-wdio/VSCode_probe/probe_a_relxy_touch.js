// Probe A: relX/relY (window-relative) + pointerType:'touch'
// Target: VSCode "파일" menu button
// Captured: relX=435, relY=160 (winLeft=1911, winTop=-686)
// Expected: File menu opens → 파일 menu panel visible

describe('Probe A — relX/relY + touch', () => {
    it('selector baseline: click 파일 via selector', async () => {
        const el = await browser.$('//*[@ClassName="menubar-menu-button" and @Name="파일"]');
        await el.waitForDisplayed({ timeout: 10000 });
        await el.click();
        await browser.pause(800);
        // Press Escape to close menu before coordinate test
        await browser.keys(['Escape']);
        await browser.pause(400);
        console.log('[BASELINE] selector click passed');
    });

    it('coordinate click: relX=435 relY=160 pointerType:touch', async () => {
        console.log('[PROBE A] clicking at relX=435, relY=160 with touch');
        await browser.action('pointer', { parameters: { pointerType: 'touch' } })
            .move({ x: 435, y: 160, origin: 'viewport' })
            .down().up()
            .perform();
        await browser.pause(800);
        // Check if "파일" menu panel opened (a menu item like "새 텍스트 파일" becomes visible)
        try {
            const menuItem = await browser.$('//*[@Name="새 텍스트 파일"]');
            const visible = await menuItem.isDisplayed();
            console.log(`[PROBE A] 파일 menu item visible: ${visible}`);
            if (visible) await browser.keys(['Escape']);
        } catch (e) {
            console.log('[PROBE A] menu item not found — click did not open File menu');
        }
        expect(true).toBe(true); // always pass; result is in log
    });
});
