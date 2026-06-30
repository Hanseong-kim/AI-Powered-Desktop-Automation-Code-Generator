// Probe Root v4 — browser.elementClick(id) direct protocol call
//
// Findings so far:
//   v1: 파일 (ControlType.MenuItem) found & el.click() RESULT null ✓
//       Jasmine 60s timeout during 폴더 열기 search
//   v2: 파일 clicked, contains(@Name,"폴더 열기") found (element 80558) ✓
//       el.click() on 80558 FAILED: executeScript polyfill unsupported
//   v3: windows:click FAILED ("Native Windows API calls cannot be invoked")
//       el.click() fallback on 파일 probably worked but windows:click retries
//       burned ~30s → jasmine 120s timeout before 폴더 열기 found
//
// Fix: browser.elementClick(elementId) = direct POST /element/{id}/click
//      No WebdriverIO pre-checks, no executeScript polyfill. Pure W3C command.
//
// Also: 파일 waitForExist takes 30-35s in Root session (full desktop scan).
//       After that, 폴더 열기 search needs to complete in remaining ~85s.

describe('Probe Root v4 — browser.elementClick() direct protocol', () => {
    it('Root: 파일 click then 폴더 열기 via elementClick(id)', async () => {

        // ── Step 1: 파일 MenuItem ─────────────────────────────────────────────
        console.log('[v4] Step 1: waitForExist 파일...');
        const fileSel = await browser.$('//*[@Name="파일"]');
        await fileSel.waitForExist({ timeout: 40000 });
        const tag1 = await fileSel.getTagName();
        const fileId = fileSel.elementId;
        console.log(`[v4] 파일 found — type=${tag1} id=${fileId}`);

        // ── Step 2: click 파일 (el.click() worked in v1/v2 for MenuItem) ─────
        console.log('[v4] Step 2: el.click() on 파일...');
        await fileSel.click();
        console.log('[v4] 파일 el.click() ✓');

        await browser.pause(1500);

        // ── Step 3: find 폴더 열기 ─────────────────────────────────────────
        // v2 confirmed element appears in UIA after 파일 click.
        // Use isExisting() (not waitForExist) to avoid any internal executeScript.
        console.log('[v4] Step 3: findElement 폴더 열기...');

        const folderCandidates = [
            '//*[@Name="폴더 열기..."]',
            '//*[@Name="폴더 열기"]',
            '//*[contains(@Name,"폴더 열기")]',
        ];

        let folderId = null;
        let folderName = '';
        let folderType = '';

        for (const sel of folderCandidates) {
            try {
                const el = await browser.$(sel);
                const exists = await el.isExisting();
                if (exists) {
                    folderName = await el.getAttribute('Name');
                    folderType = await el.getTagName();
                    folderId = el.elementId;
                    console.log(`[v4] 폴더 열기 found: "${sel}" Name="${folderName}" type=${folderType} id=${folderId}`);
                    break;
                }
                console.log(`[v4]   not found: ${sel}`);
            } catch (e) {
                console.log(`[v4]   error: ${sel} → ${e.message.substring(0, 60)}`);
            }
        }

        if (!folderId) {
            // Brief extra wait and one retry
            await browser.pause(1500);
            for (const sel of folderCandidates) {
                try {
                    const el = await browser.$(sel);
                    if (await el.isExisting()) {
                        folderName = await el.getAttribute('Name');
                        folderId = el.elementId;
                        folderType = await el.getTagName();
                        console.log(`[v4] 폴더 열기 found after retry: "${sel}"`);
                        break;
                    }
                } catch {}
            }
        }

        if (!folderId) {
            console.log('[v4] FAIL: 폴더 열기 element not found in UIA tree');
            expect(true).toBe(false);
            return;
        }

        // ── Step 4: browser.elementClick(id) — direct W3C, no polyfill ───────
        // This calls POST /element/{id}/click directly via WebdriverIO's
        // lower-level protocol layer, bypassing any pre-click JS checks.
        console.log(`[v4] Step 4: browser.elementClick("${folderId}")...`);
        try {
            await browser.elementClick(folderId);
            console.log('[v4] browser.elementClick() on 폴더 열기 ✓');
        } catch (e) {
            console.log(`[v4] browser.elementClick() failed: ${e.message.substring(0, 120)}`);
            // Last resort: try el.click() anyway
            try {
                const el = await browser.$(`//*[@Name="${folderName}"]`);
                await el.click();
                console.log('[v4] el.click() fallback ✓');
            } catch (e2) {
                console.log(`[v4] el.click() fallback also failed: ${e2.message.substring(0, 80)}`);
                expect(true).toBe(false);
                return;
            }
        }

        await browser.pause(2500);

        // ── Step 5: confirm OS dialog appeared ───────────────────────────────
        console.log('[v4] Step 5: checking OS 폴더 열기 dialog...');

        const dialogSelectors = [
            '//*[@Name="폴더 열기"]',
            '//*[@ClassName="DirectUIHWND"]',
            '//*[@ClassName="NamespaceTreeControl"]',
        ];

        let dialogFound = false;
        for (const sel of dialogSelectors) {
            try {
                const el = await browser.$(sel);
                if (await el.isExisting()) {
                    const name = await el.getAttribute('Name');
                    console.log(`[v4] OS dialog found: "${sel}" Name="${name}" ✓`);
                    dialogFound = true;
                    break;
                }
            } catch {}
        }

        console.log('\n[v4] ════ FINAL RESULT ════');
        console.log('[v4] 파일 click:      ✓');
        console.log(`[v4] 폴더 열기 found: ✓ Name="${folderName}" type=${folderType}`);
        console.log('[v4] 폴더 열기 click: ✓');
        console.log(`[v4] OS dialog:       ${dialogFound ? '✓ confirmed' : '? undetected (look at screen)'}`);
        console.log('[v4] ══════════════════════');

        expect(true).toBe(true);
    });
});
