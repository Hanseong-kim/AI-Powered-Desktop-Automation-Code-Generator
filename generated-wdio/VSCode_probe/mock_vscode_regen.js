/**
 * Regenerates generated-wdio/VSCode/ using mock events that match a real
 * VSCode "파일 > 폴더 열기" flow captured with agent.py.
 *
 * Usage (server must be running on :3002):
 *   node generated-wdio/VSCode_probe/mock_vscode_regen.js
 *
 * Then run the test:
 *   cd generated-wdio && npx wdio run VSCode/wdio.conf.js
 */

const BASE = 'http://localhost:3002';

// Events reconstructed from the probe sessions + old generated files.
// windowTitle is what agent.py captures via GetWindowText(GetAncestor(hwnd, GA_ROOT)).
const EVENTS = [
  // ── VSCode main window ─────────────────────────────────────────────────
  {
    action: 'click',
    x: 2368, y: -490,
    isElectron: true,
    element: {
      windowTitle: 'Visual Studio Code',
      automationId: '',
      name: '파일',
      className: 'Chrome_WidgetWin_1',
      controlType: 'MenuItem',
    },
  },
  {
    action: 'click',
    x: 2396, y: -335,
    isElectron: true,
    element: {
      windowTitle: 'Visual Studio Code',
      automationId: '',
      name: '폴더 열기...',
      className: 'Chrome_WidgetWin_1',
      controlType: 'MenuItem',
    },
  },
  // ── 폴더 열기 (OS native file dialog) ──────────────────────────────────
  {
    action: 'click',
    x: 2406, y: -307,
    isElectron: false,
    element: {
      windowTitle: '폴더 열기',
      automationId: '',
      name: '즐겨찾기 시작 - 바탕 화면(고정됨)',
      className: 'ToolbarWindow32',
      controlType: 'Button',
    },
  },
  {
    action: 'click',
    x: 2907, y: -65,
    isElectron: false,
    element: {
      windowTitle: '폴더 열기',
      automationId: '1',
      name: '폴더 선택(S)',
      className: 'Button',
      controlType: 'Button',
    },
  },
  // ── Back to VSCode for second flow ────────────────────────────────────
  {
    action: 'click',
    x: 2366, y: -493,
    isElectron: true,
    element: {
      windowTitle: 'Visual Studio Code',
      automationId: '',
      name: '파일',
      className: 'Chrome_WidgetWin_1',
      controlType: 'MenuItem',
    },
  },
  {
    action: 'click',
    x: 2427, y: -336,
    isElectron: true,
    element: {
      windowTitle: 'Visual Studio Code',
      automationId: '',
      name: '폴더 열기...',
      className: 'Chrome_WidgetWin_1',
      controlType: 'MenuItem',
    },
  },
  // scroll event (should be skipped in generated code)
  {
    action: 'scroll',
    x: 2406, y: -332,
    element: { windowTitle: '폴더 열기', name: '', controlType: 'Pane' },
  },
  {
    action: 'click',
    x: 2406, y: -332,
    isElectron: false,
    element: {
      windowTitle: '폴더 열기',
      automationId: '',
      name: 'live_crop',
      className: 'DirectUIHWND',
      controlType: 'ListItem',
    },
  },
  {
    action: 'click',
    x: 2924, y: -69,
    isElectron: false,
    element: {
      windowTitle: '폴더 열기',
      automationId: '1',
      name: '폴더 선택(S)',
      className: 'Button',
      controlType: 'Button',
    },
  },
];

async function post(path, body) {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return res.json();
}

async function del(path) {
  const res = await fetch(`${BASE}${path}`, { method: 'DELETE' });
  return res.json();
}

(async () => {
  try {
    console.log('[regen] Clearing existing events...');
    await del('/api/events');

    console.log(`[regen] Injecting ${EVENTS.length} mock VSCode events...`);
    for (const e of EVENTS) {
      await post('/api/events', e);
    }

    console.log('[regen] Calling /api/generate (appName=VSCode, exePath=VSCode)...');
    const t0 = Date.now();
    const result = await post('/api/generate', { appName: 'VSCode', exePath: 'VSCode' });
    const ms = Date.now() - t0;

    if (!result.ok) {
      console.error('[regen] Generation failed:', result.message);
      process.exit(1);
    }

    console.log(`[regen] Done in ${ms}ms`);
    console.log('[regen] Files:', result.files?.map(f => f.filename).join(', '));
    console.log('[regen] Folder:', result.folder);
    console.log('[regen] Run command:', result.runCommand);
    console.log('\n--- VSCodeTestById.js (first 80 lines) ---');
    const byId = result.files?.find(f => f.filename.endsWith('TestById.js'));
    if (byId) {
      console.log(byId.content.split('\n').slice(0, 80).join('\n'));
    }
  } catch (e) {
    console.error('[regen] Error (is the server running on :3002?):', e.message);
    process.exit(1);
  }
})();
