// Probe B: absolute screen coords (x=2346, y=-526) + pointerType:'touch'
// If this opens the File menu → WinAppDriver viewport = screen coords.

describe('Probe B — absolute coords + touch', () => {
    it('coordinate click: abs x=2346 y=-526 pointerType:touch', async () => {
        await browser.pause(2000); // let VSCode finish loading
        console.log('[PROBE B] clicking at abs x=2346, y=-526 with touch');
        await browser.action('pointer', { parameters: { pointerType: 'touch' } })
            .move({ x: 2346, y: -526, origin: 'viewport' })
            .down().up()
            .perform();
        await browser.pause(1000);
        try {
            const menuItem = await browser.$('//*[@Name="새 텍스트 파일"]');
            const visible = await menuItem.isDisplayed();
            console.log(`[PROBE B] 새 텍스트 파일 visible: ${visible}`);
        } catch (e) {
            console.log('[PROBE B] menu item not found — click did not open File menu');
        }
        expect(true).toBe(true);
    });
});
