// Probe C: relX/relY + pointerType:'mouse'
// Same position as Probe A but mouse type instead of touch.
// If this works but A failed → WinAppDriver/VSCode ignores touch events.

describe('Probe C — relX/relY + mouse', () => {
    it('coordinate click: relX=435 relY=160 pointerType:mouse', async () => {
        await browser.pause(2000);
        console.log('[PROBE C] clicking at relX=435, relY=160 with mouse');
        await browser.action('pointer', { parameters: { pointerType: 'mouse' } })
            .move({ x: 435, y: 160, origin: 'viewport' })
            .down().up()
            .perform();
        await browser.pause(1000);
        try {
            const menuItem = await browser.$('//*[@Name="새 텍스트 파일"]');
            const visible = await menuItem.isDisplayed();
            console.log(`[PROBE C] 새 텍스트 파일 visible: ${visible}`);
        } catch (e) {
            console.log('[PROBE C] menu item not found — click did not open File menu');
        }
        expect(true).toBe(true);
    });
});
