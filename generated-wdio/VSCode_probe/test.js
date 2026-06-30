import { remote } from 'webdriverio';
import { exec } from 'child_process';

function osClick(absX, absY) {
  return new Promise((resolve, reject) => {
    const psCommand = `powershell -NoProfile -Command "` +
      `Add-Type -TypeDefinition '` +
      `using System.Runtime.InteropServices;` +
      `public class W32 {` +
      `[DllImport(\\"user32.dll\\")] public static extern bool SetCursorPos(int x, int y);` +
      `[DllImport(\\"user32.dll\\")] public static extern void mouse_event(uint flags, int dx, int dy, int data, int extra);` +
      `}';` +
      `[W32]::SetCursorPos(${absX}, ${absY});` +
      `Start-Sleep -Milliseconds 100;` +
      `[W32]::mouse_event(0x0002, 0, 0, 0, 0);` +
      `Start-Sleep -Milliseconds 50;` +
      `[W32]::mouse_event(0x0004, 0, 0, 0, 0);` +
      `"`;
    exec(psCommand, (err, stdout, stderr) => {
      if (err) { console.log('[osClick] error:', err.message); reject(err); }
      else resolve();
    });
  });
}

async function centerOf(el) {
  const rect = await el.getLocation();
  const size = await el.getSize();
  return {
    x: Math.round(rect.x + size.width / 2),
    y: Math.round(rect.y + size.height / 2),
  };
}

async function runProbe() {
  const driver = await remote({
    hostname: '127.0.0.1',
    port: 4723,
    path: '/',
    capabilities: {
      platformName: 'Windows',
      'appium:automationName': 'Windows',
      'appium:app': 'Root',
    },
  });

  try {
    console.log('[probe] Root 세션 연결됨');

    // 1. 파일 메뉴 클릭
    console.log('[probe] 파일 메뉴 검색...');
    const fileMenu = await driver.$('//*[@Name="파일" or @Name="File"]');
    await fileMenu.waitForExist({ timeout: 40000 });
    const fileCenter = await centerOf(fileMenu);
    console.log(`[probe] 파일 메뉴 좌표: (${fileCenter.x}, ${fileCenter.y})`);
    await osClick(fileCenter.x, fileCenter.y);
    await driver.pause(1000);

    // 2. 폴더 열기 항목 검색 + 클릭
    console.log('[probe] 폴더 열기 항목 검색...');
    const openFolder = await driver.$('//*[contains(@Name,"폴더 열기") or contains(@Name,"Open Folder")]');
    await openFolder.waitForExist({ timeout: 20000 });
    const folderCenter = await centerOf(openFolder);
    const folderName = await openFolder.getAttribute('Name');
    console.log(`[probe] 폴더 열기 발견: "${folderName}" 좌표: (${folderCenter.x}, ${folderCenter.y})`);
    await osClick(folderCenter.x, folderCenter.y);

    await driver.pause(2000);

    // 3. 네이티브 대화상자가 떴는지 확인
    console.log('[probe] 네이티브 대화상자 확인...');
    try {
      const dialog = await driver.$('//*[@Name="폴더 열기" or @ClassName="DirectUIHWND"]');
      const exists = await dialog.isExisting();
      console.log(`[probe] 대화상자 감지 = ${exists}`);
    } catch (e) {
      console.log('[probe] 대화상자 검색 실패:', e.message);
    }

    console.log('[probe] === 화면을 직접 보고 "폴더 열기" 대화상자가 떴는지 확인하세요 ===');
    await driver.pause(4000);

  } catch (error) {
    console.error('[probe] 오류:', error.message);
  } finally {
    await driver.deleteSession();
  }
}

runProbe();