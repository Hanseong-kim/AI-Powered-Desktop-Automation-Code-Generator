import { execSync, spawn } from 'child_process';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { homedir } from 'os';

const __dirname = dirname(fileURLToPath(import.meta.url));

// 주입/헬스 실패 수집 — 마지막에 실질 assert로 검증.
const _failures = [];
// 조용히 넘어갈 수 있는 성능/폴백 신호 — 실패는 아니지만 재생 품질 저하 가능성을 기록.
const _warnings = [];

// One-time PowerShell/.NET cold-start warm-up. execSync's per-call timeout
// budget was getting eaten by PowerShell's own process-spawn + Add-Type JIT
// cost on the FIRST call of a run (confirmed 2026-07-07 — VSCode multi-window
// osClick timeouts under concurrent PowerShell spawns). Absorbing that cost
// once up front keeps every real step's timeout budget for the actual work.
function _warmupPowerShell() {
    try {
        execSync('powershell -NoProfile -Command "Add-Type -AssemblyName System.Windows.Forms"', { stdio: 'pipe', timeout: 30000 });
    } catch (e) {
        console.warn('[warmup] powershell warm-up failed (non-fatal):', String(e.message || e).substring(0, 100));
    }
}

// Fixed local Appium endpoint — this file starts its own Appium instance
// (see ensureAppium below), so there is no WDIO config to read a
// host/port from anymore.
const _APPIUM = 'http://127.0.0.1:4723';
let _spawnedAppium = null;
// Root-session id (multi-window replay) / single-app-session id (simple
// replay) — set once in run()'s startup, consumed everywhere below.
let _rootSid = null;
let _appSid = null;

// Starts Appium if nothing is already listening on _APPIUM, otherwise
// reuses whatever is already running there (e.g. a dev Appium left up from
// a previous run). Spawned via the shared generated-wdio/node_modules
// install (one `npm install` for the whole generated-wdio/ tree, not per
// app) so no per-app setup step is required before `node <file>.js`.
async function ensureAppium() {
    try {
        const r = await fetch(`${_APPIUM}/status`, { signal: AbortSignal.timeout(2000) });
        if (r.ok) { console.log(`[appium] reusing already-running Appium at ${_APPIUM}`); return; }
    } catch {}
    console.log('[appium] starting Appium...');
    // node_modules/appium's package.json declares bin: { appium: 'index.js' } —
    // target that documented entry point directly rather than build/lib/main.js
    // (an internal build artifact that only works via an explicitly-documented
    // backwards-compat shim, confirmed 2026-07-17 by reading the installed
    // package; index.js is the stable contract across appium versions).
    const appiumBin = join(__dirname, '..', 'node_modules', 'appium', 'index.js');
    // '*:winappdriver' not bare 'winappdriver' — Appium 3.x's insecure-feature
    // validator requires '<automationName-or-*>:<featureName>' and throws on
    // a bare name (confirmed 2026-07-17 against the installed appium@3.5.2:
    // "The full feature name must include both the destination automation
    // name or the '*' wildcard ... Got 'winappdriver' instead"). This was a
    // pre-existing latent bug shared with wdio.conf.js's identical args —
    // just never hit because nothing had actually spawned Appium with these
    // exact CLI args end-to-end this session before ensureAppium() did.
    _spawnedAppium = spawn(process.execPath, [appiumBin, '--allow-insecure', '*:winappdriver', '--port', '4723'], { stdio: 'pipe' });
    _spawnedAppium.on('error', (e) => console.warn('[appium] spawn error:', String(e.message || e).substring(0, 150)));
    const deadline = Date.now() + 30000;
    while (Date.now() < deadline) {
        try {
            const r = await fetch(`${_APPIUM}/status`, { signal: AbortSignal.timeout(2000) });
            if (r.ok) { console.log('[appium] ready'); return; }
        } catch {}
        await new Promise(res => setTimeout(res, 1000));
    }
    throw new Error('Appium did not become ready within 30s');
}

function _killSpawnedAppium() {
    if (_spawnedAppium) {
        try { _spawnedAppium.kill(); } catch {}
        _spawnedAppium = null;
    }
}

// Hard timeout on every Appium HTTP call — WinAppDriver can block internally
// on a POST /session for a hwnd whose window is mid-close (confirmed
// 2026-07-09: STEP replay hung forever inside _createSession with no
// "failed" log ever printed, because the fetch neither resolved nor
// rejected). Without this, getWindowSession's existing catch-and-fall-back-
// to-Root-scan path never runs, since a promise that never settles never
// reaches a catch block.
async function _appiumFetch(path, opts = {}, timeoutMs = 20000) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
        return await fetch(`${_APPIUM}${path}`, { ...opts, signal: ctrl.signal });
    } catch (e) {
        if (e.name === 'AbortError') throw new Error(`Appium request timed out after ${timeoutMs}ms: ${opts.method || 'GET'} ${path}`);
        throw e;
    } finally {
        clearTimeout(timer);
    }
}

async function _appiumPost(path, body, timeoutMs = 20000) {
    const r = await _appiumFetch(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    }, timeoutMs);
    return (await r.json()).value;
}

async function _createSession(app) {
    const isHwnd = /^0x[0-9a-f]+$/i.test(app);
    const cap = isHwnd
        ? { platformName: 'Windows', 'appium:automationName': 'Windows', 'appium:appTopLevelWindow': app, 'appium:newCommandTimeout': 60000, 'appium:createSessionTimeout': 15000 }
        : { platformName: 'Windows', 'appium:automationName': 'Windows', 'appium:app': app, 'appium:newCommandTimeout': 60000, 'appium:createSessionTimeout': 15000 };
    const v = await _appiumPost('/session', { capabilities: { alwaysMatch: cap } }, 30000);
    if (!v?.sessionId) throw new Error(`Appium session failed for "${app}": ${JSON.stringify(v)}`);
    return v.sessionId;
}

async function _isSessionAlive(sid) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 1500);
    try {
        const r = await fetch(`${_APPIUM}/session/${sid}`, { signal: ctrl.signal });
        if (!r.ok) return false;
        const j = await r.json();
        return !!j?.value;
    } catch {
        return false;
    } finally {
        clearTimeout(timer);
    }
}

// 셀렉터로 요소를 찾아 element id를 돌려준다 — 좌표 산출 없음 (2026-07-10
// 좌표 실행 금지). sid/rootElId만 받는 일반형이라 세션 모드(title-keyed
// 캐시)와 simple 모드(단일 _appSid) 양쪽에서 그대로 재사용된다.
// 세션이 응답 불능이 됐을 때 남은 스텝마다 20초씩 더 태우지 않기 위한 게이트.
// 2026-07-24 Calculator 실측: STEP 19부터 모든 element 조회가 20초 타임아웃
// (not-found 아님)으로 끝났고, 같은 시점에 독립 COM UIA 클라이언트는 동일한
// 버튼(num8Button)을 46ms만에 찾았다 — 앱은 멀쩡하고 WinAppDriver 세션만 죽은
// 상태다. 그런데도 재생은 남은 6스텝 × 2회 × 20초 = 약 4분을 더 기다린 뒤에야
// 끝났고, 로그에는 "왜"에 대한 단서가 없었다. 연속 타임아웃이 임계치를 넘으면
// 세션을 죽은 것으로 확정하고 이후 조회를 즉시 실패시킨다(거짓 PASS 없이,
// 원인은 그대로 드러낸 채 빠르게 끝난다).
let _sessionDead = false;
let _consecutiveTimeouts = 0;
const _SESSION_DEAD_AFTER = 3;

async function _findElement(sid, rootElId, selector) {
    if (_sessionDead) {
        _failures.push('session-unresponsive:' + String(selector).substring(0, 60));
        return null;
    }
    try {
        const raw = selector.replace(/^['"]|['"]$/g, '');
        const using = raw.startsWith('~') ? 'accessibility id' : 'xpath';
        const value = raw.startsWith('~') ? raw.slice(1) : raw;
        const path = rootElId
            ? `/session/${sid}/element/${rootElId}/element`
            : `/session/${sid}/element`;
        const el = await _appiumPost(path, { using, value });
        _consecutiveTimeouts = 0;
        if (!el) return null;
        return el.ELEMENT || el['element-6066-11e4-a52e-4f735466cecf'] || null;
    } catch (e) {
        const msg = String(e.message || e);
        console.warn('[findElement] lookup failed:', msg.substring(0, 120));
        if (msg.includes('timed out after')) {
            _consecutiveTimeouts += 1;
            if (_consecutiveTimeouts >= _SESSION_DEAD_AFTER) {
                _sessionDead = true;
                console.error(
                    `[session] ${_consecutiveTimeouts} consecutive element lookups timed out — ` +
                    'treating the WinAppDriver session as unresponsive and failing the ' +
                    'remaining steps immediately instead of waiting 20s each. The app ' +
                    'itself may well be fine: run poc/diag_calc_alive.py against it to ' +
                    'tell "app died" from "driver died".');
            }
        } else {
            _consecutiveTimeouts = 0;
        }
        return null;
    }
}

// XPath-only click by raw session id — element/click = UIA Invoke, no
// coordinates anywhere. Used by simple mode (single _appSid, no title
// cache needed); session mode uses the title-keyed _clickScoped instead.
async function _clickBySid(sid, rootElId, selector, dbl = false) {
    const elId = await _findElement(sid, rootElId, selector);
    if (!elId) {
        _failures.push('click-not-found:' + String(selector).substring(0, 60));
        return;
    }
    await _appiumPost(`/session/${sid}/element/${elId}/click`, {});
    if (dbl) await _appiumPost(`/session/${sid}/element/${elId}/click`, {});
}

// Returns true on success, false on failure (never pushes to _failures itself
// — WinAppDriver's element/value endpoint outright rejects some native edit
// controls (confirmed 2026-07-08: Win11 Notepad's RichEditD2DPT Document
// control), so the caller falls back to OS-level typing instead of failing).
async function _typeScoped(sid, rootElId, selector, text) {
    try {
        const raw = selector.replace(/^['"]|['"]$/g, '');
        const using = raw.startsWith('~') ? 'accessibility id' : 'xpath';
        const value = raw.startsWith('~') ? raw.slice(1) : raw;
        const path = rootElId
            ? `/session/${sid}/element/${rootElId}/element`
            : `/session/${sid}/element`;
        const el = await _appiumPost(path, { using, value });
        if (!el) throw new Error('element not found');
        const elId = el.ELEMENT || el['element-6066-11e4-a52e-4f735466cecf'];
        await _appiumPost(`/session/${sid}/element/${elId}/clear`, {});
        await _appiumPost(`/session/${sid}/element/${elId}/value`, { text });
        return true;
    } catch (e) { console.warn('[type] scoped sendKeys failed:', String(e.message || e).substring(0, 100)); return false; }
}

// _step()의 ESC 복구가 오히려 재시도를 망치는 스텝 종류를 가려낸다.
// 2026-07-24 FileZilla 실측: "새 사이트(N)" 직후의 인라인 이름변경 상자에
// 타이핑하는 스텝이 1차 실패 → ESC 복구 → 2차 실패로 끝났는데, 이름변경
// 상자에서 ESC는 이름변경 자체를 취소하므로 2차 시도는 구조적으로 실패가
// 보장된다(이 현상 자체는 2026-07-17에 osScopedInvoke.py 주석으로 이미
// 기록돼 있었으나 정작 _step()에는 반영돼 있지 않았다). 팝업 해제 스캔은
// 그대로 두고 ESC만 건너뛴다.
function _escWouldHarm(label) {
    return /^\d+:type\b/.test(label);
}

// 프로그래매틱 스크롤 — osScroll.py가 대상 창 hwnd 아래에서 녹화된 컨테이너를
// UIA로 찾아 ScrollPattern.Scroll()을 호출하고, ScrollPattern 미지원 레거시
// 컨트롤에만 hwnd-scoped WM_MOUSEWHEEL을 PostMessageW로 전달한다. 픽셀
// 좌표/물리 커서 주입 없음 (2026-07-10 좌표 실행 금지 지시).
function osScrollEl(hwnd, target, delta) {
    if (!hwnd) {
        _failures.push('osScroll:no-hwnd');
        console.warn('[osScroll] no window hwnd — cannot scroll without a window handle');
        return;
    }
    try {
        const selB64 = Buffer.from(JSON.stringify(target || {}), 'utf8').toString('base64');
        const out = execSync(
            `python "${join(__dirname, 'osScroll.py')}" --hwnd ${hwnd} --sel-b64 "${selB64}" --delta ${delta}`,
            { stdio: 'pipe', timeout: 20000 }
        ).toString().trim();
        if (out) console.log(out);
    } catch (e) {
        _failures.push('osScroll');
        console.warn('[osScroll] failed:', String((e.stderr && e.stderr.toString()) || e.message || e).slice(-1500));
    }
}

// 스크롤 대상 창의 top-level hwnd 해석 — launchApp/_ensureDialog가 채운
// _hwndCache 우선, 없으면 EnumWindows 타이틀 매치로 1회 해석 후 캐시.
function _scrollHwnd(title) {
    _ensureDialog(title);
    if (_hwndCache[title]) return _hwndCache[title];
    const hs = _listWindowHwnds(title);
    if (hs.length) { _hwndCache[title] = hs[0]; return hs[0]; }
    return 0;
}

// ExpandCollapsePattern 재생 (SIMPLE_HEADER의 동일 함수와 동일 구현 —
// 2026-07-16, session 모드에도 필요해짐: FileZilla처럼 "파일(F) 메뉴 열기 →
// 사이트 관리자(S) 항목 선택"으로 두 번째 창을 여는 앱은 session 모드로
// 코드생성되는데, 이 함수 자체가 SESSION_HEADER에 없어서 재생 시
// "osExpandCollapse is not defined"로 즉시 죽었다 — mergeExpandCollapseClicks()가
// 병합한 이벤트를 재생하는 분기(generateWdio)가 useSession 여부와 무관하게
// 이 함수를 호출하므로, 두 헤더 템플릿 모두에 정의돼 있어야 한다.
function osExpandCollapse(hwnd, target, itemName) {
    if (!hwnd) {
        _failures.push('osExpandCollapse:no-hwnd');
        console.warn('[osExpandCollapse] no window hwnd — cannot expand without a window handle');
        return;
    }
    try {
        const selB64 = Buffer.from(JSON.stringify(target || {}), 'utf8').toString('base64');
        const itemArg = itemName ? `--item-name-b64 "${Buffer.from(itemName, 'utf8').toString('base64')}"` : '';
        const out = execSync(
            `python "${join(__dirname, 'osExpandCollapse.py')}" --hwnd ${hwnd} --sel-b64 "${selB64}" ${itemArg}`,
            { stdio: 'pipe', timeout: 20000 }
        ).toString().trim();
        if (out) console.log(out);
    } catch (e) {
        _failures.push('osExpandCollapse');
        console.warn('[osExpandCollapse] failed:', String((e.stderr && e.stderr.toString()) || e.message || e).slice(-1500));
    }
}

// 창-교차 클릭 재생 (SIMPLE_HEADER의 동일 함수와 동일 구현 — 2026-07-15,
// 세션 모드에도 필요해짐: 같은 리터럴 타이틀을 쓰는 다이얼로그+메인 창(예:
// 7-Zip — 파일 목록 창도, "압축 대상 추가" 다이얼로그도 둘 다 그냥 "7-Zip")은
// getWindowSession(title)의 title-키 캐시가 두 창을 구분 못 해 다이얼로그가
// 닫힌 뒤에도 그 죽은 세션을 계속 재사용한다(확인됨: STEP 6+ 메인 창 더블클릭이
// 전부 click-not-found). osScopedInvoke.py는 hwnd로 메인 창 서브트리 → 그 외
// 모든 최상위 창 순으로 직접 찾아 Invoke하므로 title 충돌 자체가 없다 —
// 다이얼로그 내부의 개별 클릭들(트리거 병합과 무관하게 각자 cross-window로
// 캡처됨)도 이 경로로 독립적으로 처리된다.
function osScopedInvoke(hwnd, target, triggerTarget) {
    if (!hwnd) {
        _failures.push('osScopedInvoke:no-hwnd');
        console.warn('[osScopedInvoke] no window hwnd — cannot search without a window handle');
        return;
    }
    try {
        const selB64 = Buffer.from(JSON.stringify(target || {}), 'utf8').toString('base64');
        const triggerArg = triggerTarget
            ? `--trigger-sel-b64 "${Buffer.from(JSON.stringify(triggerTarget), 'utf8').toString('base64')}"`
            : '';
        const out = execSync(
            `python "${join(__dirname, 'osScopedInvoke.py')}" --hwnd ${hwnd} --sel-b64 "${selB64}" ${triggerArg}`,
            { stdio: 'pipe', timeout: 20000 }
        ).toString().trim();
        if (out) console.log(out);
    } catch (e) {
        _failures.push('osScopedInvoke');
        const stdoutMsg = (e.stdout && e.stdout.toString().trim()) || '';
        if (stdoutMsg) console.log(stdoutMsg);
        console.warn('[osScopedInvoke] failed:', String((e.stderr && e.stderr.toString()) || e.message || e).slice(-1500));
    }
}

// owned 다이얼로그(WAD가 scoped session을 거부하는 창) 안의 Edit 컨트롤에
// COM으로 직접 타이핑 — getWindowSession()의 owned-창 폴백이 예전엔 Root
// 세션 REST XPath 검색을 썼는데, 실측(2026-07-17 FileZilla Site Manager
// 진단): 이 REST 호출은 매치 여부와 무관하게 매번 15~20초 고정 비용이
// 든다(빈 결과조차 15.6초 — WinAppDriver 3.5.2의 Root 세션 자체 특성으로
// 보임). hwnd는 EnumWindows로 이미 알고 있으므로, 클릭과 동일한 COM 스택
// (osScopedInvoke.py --text-b64)으로 타이핑도 처리해 그 15~20초를 우회한다.
function osScopedType(hwnd, target, text) {
    if (!hwnd) {
        _failures.push('osScopedType:no-hwnd');
        console.warn('[osScopedType] no window hwnd — cannot search without a window handle');
        return;
    }
    try {
        const selB64 = Buffer.from(JSON.stringify(target || {}), 'utf8').toString('base64');
        const textB64 = Buffer.from(text ?? '', 'utf8').toString('base64');
        const out = execSync(
            `python "${join(__dirname, 'osScopedInvoke.py')}" --hwnd ${hwnd} --sel-b64 "${selB64}" --text-b64 "${textB64}"`,
            { stdio: 'pipe', timeout: 20000 }
        ).toString().trim();
        if (out) console.log(out);
    } catch (e) {
        _failures.push('osScopedType');
        const stdoutMsg = (e.stdout && e.stdout.toString().trim()) || '';
        if (stdoutMsg) console.log(stdoutMsg);
        console.warn('[osScopedType] failed:', String((e.stderr && e.stderr.toString()) || e.message || e).slice(-1500));
    }
}

// Window session pool: title → Appium sessionId.
// _rootSid (Standalone preamble, run() creates it once) is scanned per new
// windowTitle for hwnd discovery; a fast scoped appTopLevelWindow session is
// then opened via Appium REST API (_appiumFetch/_createSession — shared
// preamble).
const _sessionIds = {};
// hwnds whose scoped-session creation already failed once this run.
// appium-windows-driver spawns a NEW WinAppDriver.exe per session and WAD's
// POST /session can block indefinitely attaching to some dialog hwnds
// (confirmed 2026-07-09: "폴더 열기" attach timed out, then the Root-scan
// fallback re-derived the SAME hwnd and paid the full timeout again).
// Never retry a handle that failed — go straight to Root-session reuse.
// Keyed by hwnd, not title: a reopened dialog gets a fresh hwnd and is
// allowed a new attempt.
const _scopedFailHwnds = new Set();

// Cache entries are { sid, rootElId }. rootElId scopes element lookups to the
// discovered dialog's subtree when sid is a Root-session fallback (see below) —
// without it, every lookup walks the ENTIRE desktop UI tree (VSCode's full
// Electron accessibility tree included), costing 10s+ per call.
async function getWindowSession(title) {
    const cached = _sessionIds[title];
    // owned:true entries have no Appium sid (sid: null, COM-routed instead) —
    // nothing to health-check, reuse the cached hwnd directly.
    if (cached && cached.owned) return cached;
    if (cached && cached.rootFallback) return cached;
    if (cached && await _isSessionAlive(cached.sid)) return cached;
    delete _sessionIds[title];
    _ensureDialog(title);

    // Preferred path: Win32 EnumWindows (_listWindowHwnds) finds the TRUE
    // top-level window by title — no ambiguity with a child element's own
    // NativeWindowHandle (confirmed 2026-07-07: the desktop-UIA XPath scan
    // below matched a child control inside the "폴더 열기" dialog, whose
    // NativeWindowHandle Appium rejected with "not a top level window
    // handle", which silently degraded every subsequent getCenter() call to
    // garbage coordinates). _ensureDialog() above already resolved and
    // cached this hwnd (and normalized the window to its recorded rect), so
    // this is normally just a cache read.
    let hwndNum = _hwndCache[title];
    if (!hwndNum) {
        // 2026-07-23 실측(FileZilla "비밀번호를 기억할까요?" 다이얼로그 재현):
        // _switchWindow() 직후 곧바로 단 1회 EnumWindows 스캔만 하면, 클릭
        // 직후 팝업이 아직 렌더링되기 전인 경우(비동기 창 생성) 못 찾고
        // hwndNum이 끝까지 비어 있는 채로 아래 "Root scan"(매 시도 15~20초
        // 고정비용, §4 2026-07-17 2차 실측)으로 영구히 떨어진다 — 게다가 그
        // 폴백조차 매번 다시 desktop-wide XPath를 훑으므로 이후 스텝마다
        // 반복해서 20초씩 걸린다(두 실패 로그 모두에서 재현). 창 생성은
        // 보통 수백 ms 안에 끝나므로, 비용이 훨씬 싼 EnumWindows(단일 PS
        // 호출 ~수십ms)을 짧게 폴링해 먼저 hwnd를 잡는다 — 못 찾을 때만
        // 기존 Root-scan 폴백으로 넘어간다(동작 변화 없음, 지연만 흡수).
        for (let attempt = 0; attempt < 10 && !hwndNum; attempt++) {
            if (attempt > 0) await _sleep(200);
            const hs = _listWindowHwnds(title);
            if (hs.length) hwndNum = hs[0];
        }
        if (hwndNum) _hwndCache[title] = hwndNum;
    }
    // Owned windows (native dialogs owned by the app's main window) can
    // never become scoped sessions — WAD rejects them, but only after the
    // full ~16s spawn/retry budget.
    //
    // Ownership is checked UNCONDITIONALLY here (not gated by
    // _scopedFailHwnds) — 2026-07-17 bug found while verifying the fix
    // below: _scopedFailHwnds was designed only to stop RE-ATTEMPTING
    // _createSession on a hwnd that already failed, but gating the
    // ownership check on it too meant that once a hwnd got blacklisted on
    // the first call, a LATER call (e.g. after _findScoped's cache-eviction
    // refresh, or after _switchWindow) would skip re-detecting "owned"
    // entirely and fall all the way through to the slow Root-scan below —
    // exactly defeating the COM fast path it was meant to protect.
    // _windowOwner() itself is a single cheap PowerShell call (not the
    // 15-20s Root-scan cost), so re-checking it every time is fine.
    if (hwndNum) {
        const ownerHwnd = _windowOwner(hwndNum);
        if (ownerHwnd) {
            if (!_scopedFailHwnds.has(hwndNum)) {
                console.log(`[session] hwnd=0x${hwndNum.toString(16)} owned by 0x${ownerHwnd.toString(16)} — skipping scoped session (WAD rejects owned windows)`);
                _scopedFailHwnds.add(hwndNum);
            }
            // 2026-07-17: owned 창을 예전엔 곧장 아래 "Root scan"(desktop-wide
            // REST XPath)으로 보냈는데, 실측 확정: 이 Root-세션 REST 호출은
            // 쿼리 내용/매치 여부와 무관하게 매번 15~20초 고정 비용이 든다
            // (빈 결과조차 15.6초 — WinAppDriver 3.5.2의 Root 세션 자체
            // 특성으로 보임, FileZilla Site Manager 다이얼로그 진단으로 확정).
            // hwnd는 이미 알고 있으므로 REST 폴백 없이 즉시 COM 라우팅
            // 마커(owned:true)를 반환 — _clickScoped/_typeScopedOrCom이
            // osScopedInvoke.py(COM, 1초 미만)를 hwnd 기반으로 직접 쓴다.
            _sessionIds[title] = { sid: null, rootElId: null, hwnd: hwndNum, owned: true };
            return _sessionIds[title];
        }
    }
    if (hwndNum && !_scopedFailHwnds.has(hwndNum)) {
        const hwndHex = '0x' + hwndNum.toString(16);
        console.log(`[session] top-level hwnd=${hwndHex} for "${title}" → scoped session`);
        const t0 = Date.now();
        try {
            const sid = await _createSession(hwndHex);
            console.log(`[session] scoped session on ${hwndHex} ready in ${Date.now() - t0}ms`);
            // hwnd tracked here (not 0/Root) — a scoped session's element
            // /location returns coordinates relative to that window, not the
            // screen (confirmed 2026-07-08), so callers must add the live
            // window origin before feeding a point to osClick.
            _sessionIds[title] = { sid, rootElId: null, hwnd: hwndNum };
            return _sessionIds[title];
        } catch (e) {
            _scopedFailHwnds.add(hwndNum);
            console.warn(`[session] scoped session on ${hwndHex} failed after ${Date.now() - t0}ms (${e.message}) — falling back to desktop-UIA scan for "${title}"`);
        }
    }

    // Safety net: EnumWindows found nothing (e.g. an empty/dynamic dialog
    // title) — fall back to the original desktop-UIA XPath scan + Root
    // session reuse.
    console.log(`[session] Root scan for: "${title}"`);
    // 2026-07-24 실측(FileZilla "사이트 관리자 - 데이터 이상" 창 재현): 이
    // Root-session REST 조회는 매치 여부와 무관하게 매번 15~20초 고정비용이다
    // (§4 2026-07-17 2차). 위 EnumWindows 폴링(최대 2초)이 이미 이 title을
    // 못 찾았다면, 그 창은 십중팔구 실제로 존재하지 않는다(예: 녹화 때는 실제
    // Enter 키 입력이 유발한 검증 에러 창이었는데, 재생의 SetValue()는 개행을
    // 실제 키 입력으로 보내지 않아 그 창 자체가 안 뜬 경우) — 이런 경우 두 개의
    // XPath 후보를 순차로 20초씩 태우는 건 이미 죽은 단서를 두 번 쫓는 것.
    // contains() 폴백(더 느슨한 매치)까지는 시도하지 않고 정확매치 1회로
    // 줄여 최악 지연을 40초 → 20초로 낮춘다 — "존재 자체가 의심스러운 창"에
    // 대한 재시도 예산은 아껴서, 그 예산을 아래 캐싱(찾았든 못 찾았든 이
    // title에 대해 다시 스캔하지 않음)으로 돌린다.
    let hwnd = null;
    let matchedElId = null;
    try {
        const elId = await _findElement(_rootSid, null, `//*[@Name="${title}"]`);
        if (elId) {
            const r = await (await _appiumFetch(`/session/${_rootSid}/element/${elId}/attribute/NativeWindowHandle`)).json();
            const rawNum = parseInt(r.value, 10);
            if (rawNum) { hwnd = '0x' + rawNum.toString(16); matchedElId = elId; }
        }
    } catch {}
    const scanHwndNum = hwnd ? parseInt(hwnd, 16) : 0;
    // Same owned-window pre-check as the EnumWindows path above.
    if (scanHwndNum && !_scopedFailHwnds.has(scanHwndNum)) {
        const ownerHwnd = _windowOwner(scanHwndNum);
        if (ownerHwnd) {
            console.log(`[session] hwnd=${hwnd} owned by 0x${ownerHwnd.toString(16)} — skipping scoped session (WAD rejects owned windows)`);
            _scopedFailHwnds.add(scanHwndNum);
        }
    }
    if (scanHwndNum && !_scopedFailHwnds.has(scanHwndNum)) {
        console.log(`[session] hwnd=${hwnd} → scoped session`);
        const t0 = Date.now();
        try {
            const sid = await _createSession(hwnd);
            console.log(`[session] scoped session on ${hwnd} ready in ${Date.now() - t0}ms`);
            // Scoped window's hwnd tracked — element /location is window-
            // relative here, same distinction as the EnumWindows path above.
            _sessionIds[title] = { sid, rootElId: null, hwnd: scanHwndNum };
            return _sessionIds[title];
        } catch (e) {
            _scopedFailHwnds.add(scanHwndNum);
            console.warn(`[session] scoped session failed after ${Date.now() - t0}ms (${e.message}) — reusing Root session for "${title}"`);
        }
    }
    // Root-session reuse (proven 2026-07-08): no new session, no WAD spawn —
    // reuse the single _rootSid run() already created at startup. Element
    // lookups are scoped to the matched dialog element's subtree via
    // rootElId; hwnd 0 = /location is already screen-absolute.
    if (!hwnd) console.warn(`[session] Window "${title}" not found — falling back to Root`);
    _warnings.push('session-fallback:' + title);
    // 2026-07-24: rootFallback:true — 다음 호출부터는 맨 위의 _isSessionAlive()
    // 헬스체크(최대 1.5초 REST 호출, 실패 시 캐시를 버리고 이 함수 전체를
    // 처음부터 재실행)를 건너뛰고 이 결과를 그대로 재사용한다. 이 title이 이번
    // 스캔에서 못 찾아졌다면(matchedElId=null) 다음 스텝에서 다시 찾아질 리
    // 없으므로, 매 스텝마다 EnumWindows 재폴링 + 최대 20초 Root-scan을 반복하는
    // 대신(사용자 실측: "사이트 관리자 - 데이터 이상" 창에서 스텝마다 20초씩
    // 반복 — 이하 §4 2026-07-24) 이번 결과를 이 title에 대해 고정시킨다.
    _sessionIds[title] = { sid: _rootSid, rootElId: matchedElId, hwnd: 0, rootFallback: true };
    return _sessionIds[title];
}

// 윈도우 세그먼트 경계에서 호출 (2026-07-16, 멀티윈도우 세그먼팅) — 이 title로
// 캐시된 세션/hwnd가 있으면 무조건 버리고 getWindowSession()이 새로 스캔하게
// 한다. 캐시를 그대로 믿으면, 다이얼로그가 닫히고 같은 리터럴 타이틀의 메인
// 창으로 돌아왔을 때(예: 7-Zip — 메인 창도 다이얼로그도 전부 그냥 "7-Zip")
// 이미 닫힌 다이얼로그의 죽은 세션/hwnd를 계속 재사용해 click-not-found가
// 반복된다(2026-07-15 "버그2" — cross-window-trigger 경로는 hwnd 기반
// osScopedInvoke로 패치됐지만 이 일반 getWindowSession 경로는 미패치였음).
// 녹화 시점 hwnd 값 자체는 재생 시 재사용할 수 없으므로(창마다 매번 새
// hwnd가 배정됨) 복합 키가 아니라 "세그먼트 전환 시 강제 재조회"로 고친다.
async function _switchWindow(title) {
    delete _sessionIds[title];
    delete _hwndCache[title];
    return await getWindowSession(title);
}

// _findElement is defined once in the shared preamble (sid/rootElId
// generic — session mode and simple mode both reuse it).

// Diagnostic for a final row-lookup failure: dump the row names UIA actually
// exposes under the dialog RIGHT NOW. Distinguishes list virtualization (the
// target row exists but isn't UIA-exposed until scrolled into view) from a
// name mismatch (row exposed under a different Name) from a dialog that never
// repopulated — the three candidate causes that can't be told apart from a
// bare no-such-element (2026-07-09: STEP 6 "hansung" lookup failed with no
// way to see what the list actually contained).
async function _dumpVisibleRows(s) {
    try {
        const path = s.rootElId
            ? `/session/${s.sid}/element/${s.rootElId}/elements`
            : `/session/${s.sid}/elements`;
        // Two queries, not an XPath union — WinAppDriver's XPath subset does
        // not reliably support "|".
        let els = await _appiumPost(path, { using: 'xpath', value: '//ListItem' });
        if (!Array.isArray(els) || !els.length) els = await _appiumPost(path, { using: 'xpath', value: '//TreeItem' });
        if (!Array.isArray(els)) { console.warn('[getCenter-diag] row query returned no array'); return; }
        const names = [];
        for (const el of els.slice(0, 20)) {
            const elId = el.ELEMENT || el['element-6066-11e4-a52e-4f735466cecf'];
            if (!elId) continue;
            try {
                const r = await (await _appiumFetch(`/session/${s.sid}/element/${elId}/attribute/Name`)).json();
                if (typeof r.value === 'string') names.push(r.value);
            } catch {}
        }
        console.warn(`[getCenter-diag] UIA-exposed rows (${els.length} total): ${names.join(' | ')}`);
    } catch (e) {
        console.warn('[getCenter-diag] dump failed:', String(e.message || e).substring(0, 100));
    }
}

// Named-element lookup with condition polling (waitUntil-style — no fixed
// pause). A navigation click (e.g. selecting a drive in the "폴더 열기" nav
// pane) repopulates the dialog's file list ASYNCHRONOUSLY; a zero-wait lookup
// would give up before the list had refreshed (confirmed 2026-07-09: STEP 6
// "hansung" no-such-element twice in a row). Polls once per second up to
// timeoutMs; halfway through it invalidates the cached session/rootElId once
// in case the cached dialog element itself went stale. Returns { elId, s }:
// elId null on timeout (after dumping visible rows for diagnosis).
async function _findScoped(title, selector, timeoutMs = 8000) {
    const deadline = Date.now() + timeoutMs;
    const refreshAt = Date.now() + timeoutMs / 2;
    let refreshed = false;
    for (;;) {
        const s = await getWindowSession(title);
        // Dialog window itself wasn't found (no hwnd, no matched element):
        // a lookup would scan the ENTIRE desktop tree from Root at 10s+ per
        // call. Fail fast instead of attempting it.
        //
        // 2026-07-24: this used to also delete _sessionIds[title] right
        // here — meaning EVERY subsequent step that targets the same
        // permanently-missing window (e.g. FileZilla's "사이트 관리자 -
        // 데이터 이상" error dialog, which never opens on replay because
        // SetValue() doesn't send the real Enter keystroke that triggered it
        // during recording) re-ran the full EnumWindows+Root-scan discovery
        // from scratch on its very next call, repeating the ~20s cost per
        // step instead of once (confirmed in a real FileZilla run: STEP
        // "switch to window" AND the following STEP both independently paid
        // the full Root-scan cost). getWindowSession() now caches this
        // "confirmed not found" verdict as rootFallback:true specifically so
        // repeat lookups against the same title short-circuit; deleting it
        // here defeated that. _switchWindow() (segment-boundary) still
        // evicts the cache on purpose when the recording actually revisits
        // this title later, so a stale negative result doesn't stick forever.
        if (!s.hwnd && !s.rootElId) {
            console.warn(`[findScoped] window "${title}" not found — failing fast`);
            return { elId: null, s };
        }
        const elId = await _findElement(s.sid, s.rootElId, selector);
        if (elId) return { elId, s };
        if (Date.now() >= deadline) {
            await _dumpVisibleRows(s);
            return { elId: null, s };
        }
        if (!refreshed && Date.now() >= refreshAt) {
            refreshed = true;
            delete _sessionIds[title];
        }
        await new Promise(r => setTimeout(r, 1000));
    }
}

// ── WAD-primary / COM-narrow-exception 아키텍처 경계 (2026-07-24, 리뷰
// 피드백 확정) ───────────────────────────────────────────────────────────
// 2026-07-24 리뷰 허들에서 "WinAppDriver 전면 제거 + 단일 COM 스택" 제안이
// 나왔다가 기각됐다 — 표면적 근거(다중창/성능/크래시)는 실재하는 문제였지만
// 결론(WAD 제거)이 틀렸다는 판정. 이 경계는 앞으로도 지켜야 하는 규칙이다:
//   - WAD가 주도: 메인 창 + WAD가 attach 가능한 모든 창(scoped session/
//     appTopLevelWindow) — 여기 클릭/타이핑은 반드시 WAD의 element/click,
//     element/value를 거친다. 이게 실제로 화면에 보이는 입력이다(사람이
//     지켜보며 재생을 확인할 수 있어야 한다는 요구사항, §6). 순수 COM
//     InvokePattern/ValuePattern.SetValue는 커서 이동도 타이핑 과정도 없는
//     프로그래매틱 호출이라 이 요구를 못 채운다 — 대체 엔진으로 승격 금지.
//   - COM이 주도(osScopedInvoke.py/osExpandCollapse.py/osScroll.py만): (a)
//     WAD가 session 생성을 실제로 거부하는 owned 다이얼로그(아래 s0.owned
//     분기 — 추측이 아니라 실측된 거부에만 탄다), (b) 부모 세션에 안 묶이는
//     네이티브 팝업(ComboBox 드롭다운/TrackPopupMenu), (c) ScrollPattern
//     (WAD에 스크롤 엔드포인트 자체가 없음).
//   - 같은 hwnd에 WAD와 COM을 동시에 태우지 않는다 — COMError -2147220991의
//     원인이 정확히 이거였다. _scopedFailHwnds/owned 게이팅이 "WAD가 이미
//     그 hwnd에 부적합하다고 확인된 뒤에만 COM" 순서를 강제하므로, 이
//     불변식을 깨는 코드(WAD가 세션을 들고 있는 hwnd를 COM이 기회적으로
//     찔러보는 경로 등)를 추가하지 않는다.
// osUiaReplay.py(단일 COM 엔진으로 click/type까지 통합하려던 시도)는 위
// 첫 번째 규칙 위반이라 되돌렸다 — 실제 클릭/타이핑 경로에 연결된 적은 없음.
//
// 2026-07-24 (후속) 개정 — COM 구간의 클릭은 이제 시각적으로 재생된다:
// 스테이크홀더가 §3 좌표 규칙을 재정의했다(금지 대상은 "저장된 static 좌표"이며,
// 런타임에 UIA로 resolve한 요소의 dynamic ClickablePoint + SendInput은 정상적인
// input emulation). 그에 따라 COM_INPUT_PY의 send_input_click()이 위 COM 구간
// (a)(b)의 invoke_item() 체인 맨 앞에 붙었다. 경계 자체는 그대로다 — WAD가
// 붙는 창은 여전히 WAD가 처리하고, 이 변경은 WAD가 애초에 못 붙는 구간의 재생
// 품질만 WAD 수준으로 맞춘다. 안전 검증(offscreen/WindowFromPoint PID/
// ElementFromPoint 왕복)을 통과 못 하면 기존 Invoke/Select 체인으로 조용히
// 폴백하므로 동작 자체는 어떤 경우에도 퇴행하지 않는다.
//
// XPath-only click in the window's own session context (HWND 세그먼트).
// element/click = UIA Invoke/기본 액션 — 창이 이동/리사이즈돼도 무관하고
// 좌표는 어디에도 없다. doubleClick은 같은 요소에 클릭 2회 (WinAppDriver에
// 요소 단위 doubleclick 엔드포인트가 없음 — 좌표 기반 moveto/doubleclick은
// 금지 대상이라 쓰지 않는다). 실패는 _failures로 기록되어 _step()의
// Fail-and-Recover(팝업 해제 후 1회 재시도)를 태운 뒤 최종 FAIL로 남는다.
async function _clickScoped(title, selector, dbl = false) {
    // 2026-07-17: owned 다이얼로그면 REST 폴백(15~20초 고정 비용, 실측 확정)을
    // 아예 타지 않고 COM(osScopedInvoke, 1초 미만)으로 즉시 처리한다. 셀렉터가
    // COM 조건으로 못 옮기는 형태(anchor 상대 경로 등)면 null을 반환해 아래
    // REST 경로로 안전하게 폴백한다.
    const s0 = await getWindowSession(title);
    if (s0.owned && s0.hwnd) {
        const target = _parseSelectorToTarget(selector);
        if (target) {
            osScopedInvoke(s0.hwnd, target);
            if (dbl) osScopedInvoke(s0.hwnd, target);
            return;
        }
    }
    const { elId, s } = await _findScoped(title, selector);
    if (!elId) {
        _failures.push('click-not-found:' + String(selector).substring(0, 60));
        return;
    }
    await _appiumPost(`/session/${s.sid}/element/${elId}/click`, {});
    if (dbl) await _appiumPost(`/session/${s.sid}/element/${elId}/click`, {});
}

// COM 라우팅(owned 다이얼로그)이 필요한 session-mode 타이핑 — 위
// _clickScoped와 동일한 이유/동일한 15~20초 회피. selector가 COM 조건으로
// 못 옮기는 형태면 기존 REST 기반 _typeScoped(공유 preamble)로 폴백한다.
async function _typeScopedOrCom(title, selector, text) {
    const s = await getWindowSession(title);
    if (s.owned && s.hwnd) {
        const target = _parseSelectorToTarget(selector);
        if (target) {
            osScopedType(s.hwnd, target, text);
            return true;
        }
    }
    return await _typeScoped(s.sid, s.rootElId, selector, text);
}

// wdioSelectorById/wdioSelectorByClass가 만드는 단순 셀렉터 형태를
// {automationId,className,name} 객체로 변환한다 — osScopedInvoke.py의
// AND-조건 포맷과 동일. 태그는 '*'뿐 아니라 controlType(예: //TreeItem[...])도
// 나올 수 있음(2026-07-17 실측: FileZilla "내 사이트" 셀렉터가
// '//TreeItem[@Name="내 사이트"]'였는데 '*'만 매칭하는 첫 버전 정규식이
// 이걸 못 잡아 owned-창 COM 우회가 이 스텝에서만 발동 안 하고 조용히
// 느린 REST 경로로 떨어졌다) — 태그는 UIA ControlType이지 Win32 className이
// 아니므로 그냥 무시(캡처 못함), Name/AutomationId/ClassName 속성만 뽑는다.
// anchor 상대 경로(//*[@AutomationId="X"]/Tag[i])나 contains() 등은 COM
// FindFirst 단일 조건으로 표현 불가하므로 null을 반환해 호출부가 기존
// REST 경로로 폴백하게 한다.
function _parseSelectorToTarget(selector) {
    const raw = String(selector).replace(/^['"]|['"]$/g, '');
    if (raw.startsWith('~')) return { automationId: raw.slice(1), className: '', name: '' };
    let m = raw.match(/^\/\/[A-Za-z*]+\[@AutomationId="([^"]*)"\]$/);
    if (m) return { automationId: m[1], className: '', name: '' };
    m = raw.match(/^\/\/[A-Za-z*]+\[@AutomationId="([^"]*)" and @Name="([^"]*)"\]$/);
    if (m) return { automationId: m[1], className: '', name: m[2] };
    m = raw.match(/^\/\/[A-Za-z*]+\[@ClassName="([^"]*)" and @Name="([^"]*)"\]$/);
    if (m) return { automationId: '', className: m[1], name: m[2] };
    m = raw.match(/^\/\/[A-Za-z*]+\[@ClassName="([^"]*)"\]$/);
    if (m) return { automationId: '', className: m[1], name: '' };
    m = raw.match(/^\/\/[A-Za-z*]+\[@Name="([^"]*)"\]$/);
    if (m) return { automationId: '', className: '', name: m[1] };
    return null;
}

// _typeScoped(sid, rootElId, selector, text) is defined once in the shared
// preamble (generic over sid — used here with a title-resolved sid/rootElId,
// and by simple mode with _appSid directly).

// ── HWND 추적 (창 세그먼팅) ────────────────────────────────────────────────
// Title fragment → hwnd of the window launchApp actually created for this run.
// Populated by launchApp via baseline/diff (see below). Once set, every
// _resolveWinRect/normalizeWindow call for that fragment targets this exact
// hwnd instead of re-searching by title — title substrings are NOT unique
// (e.g. any pre-existing "...- Visual Studio Code" window also matches), and
// replaying clicks against whichever window happens to match/be-foreground
// can land recorded titlebar clicks (including close) on the WRONG window.
const _hwndCache = {};

// Main app window title-fragment, set once in beforeAll (see generateWdio's
// beforeHook) — lets osDismissPopup() identify the main window/PID for
// owner-PID scoping without every call site having to pass it in.
let _mainTitleFrag = '';

// Native (non-Electron) dialog title → its recorded window geometry, set
// once in beforeAll (see generateWdio's beforeHook). _ensureDialog() uses
// this to normalize a dialog to the position/size it was RECORDED at (e.g.
// on a specific monitor in a multi-monitor setup) the first time replay
// touches it — without this, a dialog's rel-offsets (relX/relY captured
// against the recording-time window) point at the wrong pixels once the
// dialog opens at a different position (confirmed 2026-07-07: VSCode's
// "폴더 열기" dialog opened on monitor 1 while recording was done on
// monitor 2, so every rel-offset click/scroll landed off-window).
let _dialogRects = {};
const _dialogsReady = new Set();

// Resolves a dialog's TRUE top-level hwnd via Win32 EnumWindows (title
// substring match — see _listWindowHwnds), then normalizes it to its
// recorded rect and brings it to the foreground, ONCE per title. A no-op
// for the main Electron window or any title not in _dialogRects (both
// _resolveWinRect/getWindowSession callers pass titles indiscriminately —
// this function is the single gate deciding whether a given title is a
// "dialog that needs normalizing" at all).
function _ensureDialog(title) {
    if (!title || !(title in _dialogRects) || _dialogsReady.has(title)) return;
    _dialogsReady.add(title);
    const hs = _listWindowHwnds(title);
    if (!hs.length) {
        console.warn(`[dialog] "${title}" not found by EnumWindows — rel-offsets may be unreliable`);
        return;
    }
    _hwndCache[title] = hs[0];
    const r = _dialogRects[title];
    normalizeWindow(title, r.left, r.top, r.width, r.height);
    osActivate(title, hs[0]);
    console.log(`[dialog] "${title}" hwnd=${hs[0]} normalized to`, r);
}

function _listWindowHwnds(frag) {
    if (!frag) return [];
    try {
        const out = execSync(
            `powershell -NoProfile -File "${join(__dirname, 'osWindowRect.ps1')}" -titleLike "${frag}" -listOnly`,
            { stdio: 'pipe', timeout: 15000 }
        ).toString().trim();
        if (!out) return [];
        return out.split(/\r?\n/).map(s => s.trim()).filter(Boolean).map(Number);
    } catch {
        return [];
    }
}

// Owner hwnd of a window (0 = unowned). WinAppDriver rejects OWNED windows
// as appTopLevelWindow ("X is not a top level window handle") only after
// appium has burned its full WAD-spawn + retry budget — ~16s per attempt
// (confirmed 2026-07-09: the "폴더 열기" dialog, owned by the VSCode main
// window, cost 16226ms before failing). One cheap PS call up front lets
// getWindowSession skip the doomed attempt entirely. Returns 0 on any
// error so callers fall through to the normal attempt-then-blacklist path.
function _sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

function _windowOwner(hwndNum) {
    try {
        const out = execSync(
            `powershell -NoProfile -File "${join(__dirname, 'osWindowRect.ps1')}" -hwnd ${hwndNum} -ownerOnly`,
            { stdio: 'pipe', timeout: 15000 }
        ).toString().trim();
        return Number(out) || 0;
    } catch {
        return 0;
    }
}

function _resolveWinRect(frag) {
    if (!frag) return null;
    const hwnd = _hwndCache[frag];
    try {
        const args = hwnd ? `-hwnd ${hwnd}` : `-titleLike "${frag}"`;
        const out = execSync(
            `powershell -NoProfile -File "${join(__dirname, 'osWindowRect.ps1')}" ${args}`,
            { stdio: 'pipe', timeout: 15000 }
        ).toString().trim();
        const m = out.match(/(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)/);
        if (m) return { left: +m[1], top: +m[2], width: +m[3], height: +m[4] };
        if (hwnd) delete _hwndCache[frag]; // tracked window closed — next call re-searches by title
    } catch (e) {
        _failures.push('winRect');
        console.warn('[winRect] failed:', String(e.message || e).substring(0, 100));
    }
    return null;
}

// Force a newly-launched window to the exact geometry it was recorded at.
// Recorded rel-offsets are only valid if the window is the same SIZE as
// during recording, not just position — a freshly-launched window (often
// maximized) reflows its UI at a different size, pointing rel offsets at
// the wrong elements. Soft-fails: a move/resize failure doesn't abort the
// suite, but it does invalidate the cached rect so callers re-scan live.
function normalizeWindow(frag, left, top, width, height) {
    const hwnd = _hwndCache[frag];
    try {
        const target = hwnd ? `-hwnd ${hwnd}` : `-titleLike "${frag}"`;
        execSync(
            `powershell -NoProfile -File "${join(__dirname, 'osMoveWindow.ps1')}" ${target} -left ${left} -top ${top} -width ${width} -height ${height}`,
            { stdio: 'pipe', timeout: 15000 }
        );
    } catch (e) {
        _failures.push('moveWindow');
        console.warn('[moveWindow] failed:', String(e.message || e).substring(0, 100));
    }
}

// Bring a dialog (or, if hwnd is unknown, anything matching titleLike) to
// the foreground — same OS-level foreground-lock bypass as SIMPLE_HEADER's
// osActivate, but hwnd-first since _ensureDialog always already has one.
function osActivate(titleLike, hwnd) {
    try {
        const args = hwnd ? `-hwnd ${hwnd}` : `-titleLike "${titleLike}"`;
        execSync(
            `powershell -NoProfile -File "${join(__dirname, 'osActivate.ps1')}" ${args}`,
            { stdio: 'pipe', timeout: 15000 }
        );
    } catch (e) {
        console.warn('[osActivate] failed:', String(e.message || e).substring(0, 100));
    }
}

// Launch a fresh app window before replay starts (session mode only), so the
// suite targets a known-clean window instead of whatever happens to already
// be open. Single-instance apps (e.g. VS Code with -n) don't spawn a new OS
// process at all — they message the already-running instance to open a new
// window — so a NEW hwnd can appear even when no NEW process does. We snapshot
// hwnds matching titleFrag BEFORE spawning and diff against the post-spawn
// set to identify that new window unambiguously, then cache it in _hwndCache
// so every later _resolveWinRect/normalizeWindow call targets that hwnd
// directly instead of re-matching by (possibly ambiguous) title.
async function launchApp(exePath, args, titleFrag, rect) {
    if (!exePath) return;
    // agent.py is_aumid()와 동일 판정, 대칭 유지 — "PackageFamilyName!AppId"는
    // 파일 경로가 아니라 explorer shell:AppsFolder로 활성화해야 한다.
    // spawn(exePath,...)로 직접 넘기면 파일 경로로 오인해 비동기 ENOENT로
    // 실패하는데, 이 실패는 이 catch 밖(다음 tick)에서 터져 try/catch에
    // 잡히지 않고 _failures에도 안 찍힌 채 20초 타임아웃만 나는 문제가 있었다.
    const isAumid = /!/.test(exePath) && !/[\/]/.test(exePath);
    const baseline = new Set(_listWindowHwnds(titleFrag));
    // A content-dependent recorded title (e.g. Notepad's "*d - 메모장" — the
    // dirty-flag/filename prefix only exists once text has been typed) never
    // matches the fresh, clean window this launch creates ("제목 없음 - 메모장"),
    // so the frag-diff below never fires and every later hwnd lookup falls
    // through to a Root scan (confirmed 2026-07-08). Also snapshot/match on
    // the stable tail token after the last " - " (app name, e.g. "메모장") as
    // a fallback identity. No-op when titleFrag has no " - " (FDM's "Free
    // Download Manager", VSCode's winFrag) since tailFrag === titleFrag then.
    const tailFrag = (titleFrag || '').split(' - ').pop() || titleFrag;
    const baselineTail = tailFrag !== titleFrag ? new Set(_listWindowHwnds(tailFrag)) : null;
    // cwd 명시 (2026-07-17) — 안 주면 spawn()이 이 재생 스크립트를 실행한
    // Node 프로세스의 CWD를 그대로 물려받는다. 파일 탐색기류 앱(FileZilla
    // 로컬 패널 등)은 시작 폴더를 그 CWD로 삼는 경우가 있어, 어느 디렉터리에서
    // node로 이 파일을 실행했는지에 따라 재생 결과가 달라지는 비결정성이
    // 생긴다(실측: generated-wdio/FileZilla에서 실행하니 로컬 패널이 그
    // 프로젝트 폴더에서 열려 녹화가 가정한 ".."/"C:" 같은 최상위 항목이
    // 하나도 안 보임 — 앱이 스스로 기억하는 상태가 아니라 순수 프로세스
    // 상속 문제로 확인됨, filezilla.xml에 해당 경로 없음). 홈 디렉터리로
    // 고정해 실행 위치와 무관하게 항상 같은 곳에서 시작하게 한다.
    const launchCwd = homedir();
    try {
        if (isAumid) {
            spawn('explorer.exe', ['shell:AppsFolder\\' + exePath], { detached: true, stdio: 'ignore', cwd: launchCwd }).unref();
        } else {
            spawn(exePath, args, { detached: true, stdio: 'ignore', cwd: launchCwd }).unref();
        }
    } catch (e) {
        _failures.push('launch');
        console.warn('[launch] failed:', String(e.message || e).substring(0, 100));
        return;
    }
    const deadline = Date.now() + 20000;
    let poll = 0;
    while (Date.now() < deadline) {
        poll++;
        const matched = _listWindowHwnds(titleFrag);
        if (titleFrag && !_hwndCache[titleFrag]) {
            const fresh = matched.find(h => !baseline.has(h));
            if (fresh) {
                _hwndCache[titleFrag] = fresh;
                console.log(`[launch] tracking new window hwnd=${fresh}`);
            } else if (baselineTail) {
                const freshTail = _listWindowHwnds(tailFrag).find(h => !baselineTail.has(h));
                if (freshTail) {
                    _hwndCache[titleFrag] = freshTail;
                    console.log(`[launch] adopted new window hwnd=${freshTail} via tail fragment "${tailFrag}" (recorded title "${titleFrag}" not present at launch)`);
                }
            }
        }
        // A matched window with width/height 0 is a not-yet-rendered
        // placeholder (Electron/UWP frame created before content loads,
        // same hwnd, resized later) — treat it as "not found yet" and keep
        // polling instead of normalizing/replaying against a window that
        // isn't really there, which sent every later osClick to whatever
        // was actually on screen underneath (e.g. the desktop).
        const liveRect = _resolveWinRect(titleFrag);
        // DIAGNOSTIC (temporary): trace why [launch] window-detection times
        // out — remove once root cause of the Claude Desktop timeout is found.
        console.log(`[launch-diag] poll=${poll} titleFrag=${JSON.stringify(titleFrag)} baseline=[${[...baseline]}] matched=[${matched}] hwndCache=${_hwndCache[titleFrag] ?? 'none'} liveRect=${JSON.stringify(liveRect)}`);
        if (liveRect && liveRect.width > 0 && liveRect.height > 0) {
            if (rect) {
                normalizeWindow(titleFrag, rect.left, rect.top, rect.width, rect.height);
                const normalized = _resolveWinRect(titleFrag);
                console.log('[launch] window normalized to', normalized);
            }
            return;
        }
        await new Promise(r => setTimeout(r, 1000));
    }
    _failures.push('launch');
    console.warn('[launch] window not detected within timeout');
}

// OS 키 주입(SendKeys) — 좌표 실행이 아닌 키보드 폴백. _typeScoped가
// 거부되는 컨트롤(예: RichEditD2DPT) 및 Electron 포커스 입력용.
function osType(text) {
    try {
        const b64 = Buffer.from(text, 'utf8').toString('base64');
        execSync(
            `powershell -NoProfile -File "${join(__dirname, 'osType.ps1')}" -b64 "${b64}"`,
            { stdio: 'pipe', timeout: 15000 }
        );
    } catch (e) {
        _failures.push('osType');
        console.warn('[osType] failed:', String(e.message || e).substring(0, 100));
    }
}

// Fail-and-Recover popup dismissal (v2) — only called from _step() below,
// after a step has already failed, so the happy path pays zero cost.
// Prefers the tracked hwnd for the main app window (_hwndCache[_mainTitleFrag],
// set by launchApp) for deterministic owner-PID scoping; falls back to a
// title-substring match when no hwnd was tracked (e.g. app already running).
// Every hwnd the replay itself is driving (main window + dialogs tracked in
// _hwndCache) is passed as -exclude — a "recovery" that closes the very
// dialog the failed step is about to retry against guarantees the retry
// fails too (confirmed 2026-07-09: dismisser closed the "폴더 열기" flow's
// window, then the retry's Root scan found nothing and the run stalled).
function osDismissPopup() {
    try {
        const hwnd = _hwndCache[_mainTitleFrag];
        let args = hwnd ? `-hwnd ${hwnd}` : (_mainTitleFrag ? `-titleLike "${_mainTitleFrag}"` : '');
        const tracked = [...new Set(Object.values(_hwndCache))].filter(Boolean);
        if (tracked.length) args += ` -exclude "${tracked.join(',')}"`;
        const out = execSync(
            `powershell -NoProfile -File "${join(__dirname, 'osDismissPopup.ps1')}" ${args}`,
            { stdio: 'pipe', timeout: 15000 }
        ).toString().trim();
        if (out.startsWith('DISMISSED')) { console.log('[popup]', out); return true; }
        return false;
    } catch (e) {
        console.warn('[osDismissPopup] failed:', String(e.message || e).substring(0, 100));
        return false;
    }
}

// ESC fallback — see OS_ESCAPE_PS1. Called only when osDismissPopup() found
// no known dismiss button (rename edit-box, open menu, etc).
function osEscape() {
    try {
        execSync(
            `powershell -NoProfile -File "${join(__dirname, 'osEscape.ps1')}"`,
            { stdio: 'pipe', timeout: 15000 }
        );
        return true;
    } catch (e) {
        console.warn('[osEscape] failed:', String(e.message || e).substring(0, 100));
        return false;
    }
}

// Wraps a single replay step: on the happy path (no exception, no new
// _failures entry) this costs nothing extra. On failure, scans for and
// dismisses a known-shape popup that didn't exist at recording time (e.g.
// FDM's "file already exists"), then retries the step ONCE. If no dismiss
// button was found (e.g. an inline rename edit-box left open by a mistimed
// double-click), falls back to osActivate + ESC to back out of whatever
// modal input state grabbed focus, then retries once. If that still fails,
// the original failure/exception stands untouched (no false PASSED).
async function _step(label, fn) {
    console.log('[STEP] ' + label);
    const before = _failures.length;
    let err = null;
    try { await fn(); } catch (e) { err = e; }
    if (!err && _failures.length === before) return;
    const dismissed = osDismissPopup();
    if (dismissed) {
        _warnings.push('popup-dismissed:' + label);
    } else if (_escWouldHarm(label)) {
        _warnings.push('esc-skipped:' + label);
    } else {
        // 2026-07-24 parity fix: SIMPLE_HEADER got the foreground guard on
        // 2026-07-14 (RC-C) but this copy kept the unconditional
        // osActivate('')+ESC — the very pattern that closed PuTTY every time.
        // Only ESC when a DIFFERENT top-level window (a real popup) holds the
        // foreground; our own main window has nothing to dismiss.
        const mainHwnd = _hwndCache[_mainTitleFrag];
        const fg = osForegroundHwnd();
        if (mainHwnd && fg === mainHwnd) {
            _warnings.push('esc-skipped-main-foreground:' + label);
        } else {
            osEscape();
            _warnings.push('esc-recovery:' + label);
        }
    }
    _failures.length = before;
    await fn();
}

// Windows in this recording:
//   [W1] "FileZilla" (main)
//   [W2] "사이트 관리자" (opened during recording)
//   [W3] "새 북마크" (opened during recording)

class FileZillaPageById {

    // ════════════════════════════════════════════════════════════
    // [W1] FileZilla (main window)
    // ════════════════════════════════════════════════════════════
    async click1() {
        osExpandCollapse(_hwndCache[_mainTitleFrag], {"automationId":"","className":"","name":"파일(F)"}, null);
    }

    async click2() {
        osExpandCollapse(_hwndCache[_mainTitleFrag], {"automationId":"","className":"","name":"편집(E)"}, null);
    }

    async click3() {
        osExpandCollapse(_hwndCache[_mainTitleFrag], {"automationId":"","className":"","name":"파일(F)"}, "사이트 관리자(S)...\tCtrl+S");
    }


    // ════════════════════════════════════════════════════════════
    // [W2] 사이트 관리자 (new window)
    // ════════════════════════════════════════════════════════════
    async click4() {
        await _clickScoped('사이트 관리자', '~-31982');
    }

    async type5(value) {
        const ok = await _typeScopedOrCom('사이트 관리자', '~1', value);
        if (!ok) {
            console.warn('[type5] scoped sendKeys failed — falling back to OS-level typing');
            osActivate('사이트 관리자', _hwndCache['사이트 관리자']);
            osType(value);
        }
    }

    async click6() {
        await _clickScoped('사이트 관리자', '~-31983');
    }

    async type7(value) {
        const ok = await _typeScopedOrCom('사이트 관리자', '~1', value);
        if (!ok) {
            console.warn('[type7] scoped sendKeys failed — falling back to OS-level typing');
            osActivate('사이트 관리자', _hwndCache['사이트 관리자']);
            osType(value);
        }
    }

    async click8() {
        await _clickScoped('사이트 관리자', '~5101');
    }

    async click9() {
        osExpandCollapse(_hwndCache[_mainTitleFrag], {"automationId":"","className":"","name":"북마크(B)"}, "북마크 추가(A)...\tCtrl+B");
    }


    // ════════════════════════════════════════════════════════════
    // [W3] 새 북마크 (new window)
    // ════════════════════════════════════════════════════════════
    async click10() {
        await _clickScoped('새 북마크', '//CheckBox[@AutomationId="5999" and @Name="탐색 동기화 사용(S)"]');
    }

    async click11() {
        await _clickScoped('새 북마크', '//CheckBox[@AutomationId="5999" and @Name="디렉터리 비교(I)"]');
    }

    async click12() {
        await _clickScoped('새 북마크', '//CheckBox[@AutomationId="5999" and @Name="탐색 동기화 사용(S)"]', true);
    }

    async click13() {
        await _clickScoped('새 북마크', '~-31738');
    }

    async click14() {
        await _clickScoped('새 북마크', '//CheckBox[@AutomationId="5999" and @Name="디렉터리 비교(I)"]');
    }

    async click15() {
        await _clickScoped('새 북마크', '~5101');
    }
}

// Plain async entry point — replaces the old Jasmine describe/it wrapper
// (2026-07-17: standalone execution, no WDIO/Jasmine runner needed).
async function run() {
    // Everything — including Appium/session startup — runs inside this
    // try/finally, not just the replay steps: a failure in ensureAppium()/
    // _createSession() (e.g. a bad capability) must still kill any Appium
    // process this run spawned. Node does not reliably reap child processes
    // on Windows when the parent exits, so leaving startup outside the
    // finally risked leaking an orphaned Appium instance on every startup
    // failure (confirmed 2026-07-17 while verifying the standalone runner).
    try {
        _warmupPowerShell();

    _mainTitleFrag = "FileZilla";
    _dialogRects = {"FileZilla":{"left":510,"top":74,"width":1200,"height":947},"사이트 관리자":{"left":519,"top":228,"width":1182,"height":639},"새 북마크":{"left":890,"top":355,"width":440,"height":385}};
    await ensureAppium();
    _rootSid = await _createSession('Root');
    console.log(`[session] Root session ${_rootSid} ready`);
        await launchApp("C:\\Program Files\\FileZilla FTP Client\\filezilla.exe", [], "FileZilla", {"left":510,"top":74,"width":1200,"height":947});

        const page = new FileZillaPageById();

    // ════════════════════════════════════════════════════════════
    // [W1] FileZilla (main window)
    // ════════════════════════════════════════════════════════════
            await _step('1:expandCollapse 파일(F)', () => page.click1());
            await _step('2:expandCollapse 편집(E)', () => page.click2());
            await _step('3:expandCollapse 파일(F) -> 사이트 관리자(S)...\tCtrl+S', () => page.click3());

    // ════════════════════════════════════════════════════════════
    // [W2] 사이트 관리자 (new window)
    // ════════════════════════════════════════════════════════════
            await _step('switch to window: 사이트 관리자', async () => { await _switchWindow('사이트 관리자'); });
            await _step('4:click 새 폴더(F)', () => page.click4());
            await _step('5:type asdf\n', () => page.type5('asdf\n'));
            await _step('6:click 새 사이트(N)', () => page.click6());
            await _step('7:type asdf\n', () => page.type7('asdf\n'));
            await _step('8:click 취소', () => page.click8());
            await _step('9:expandCollapse 북마크(B) -> 북마크 추가(A)...\tCtrl+B', () => page.click9());

    // ════════════════════════════════════════════════════════════
    // [W3] 새 북마크 (new window)
    // ════════════════════════════════════════════════════════════
            await _step('switch to window: 새 북마크', async () => { await _switchWindow('새 북마크'); });
            await _step('10:click 탐색 동기화 사용(S)', () => page.click10());
            await _step('11:click 디렉터리 비교(I)', () => page.click11());
            await _step('12:doubleClick 탐색 동기화 사용(S)', () => page.click12());
            await _step('13:click 경로', () => page.click13());
            await _step('14:click 디렉터리 비교(I)', () => page.click14());
            await _step('15:click 취소', () => page.click15());
    } finally {

        for (const { sid } of Object.values(_sessionIds)) {
            if (sid === _rootSid) continue;
            try { await _appiumFetch(`/session/${sid}`, { method: 'DELETE' }, 5000); } catch {}
        }
        if (_rootSid) { try { await _appiumFetch(`/session/${_rootSid}`, { method: 'DELETE' }, 5000); } catch {} }
        _killSpawnedAppium();
    }
    if (_warnings.length) console.warn('[replay-warnings]', _warnings);
    if (_failures.length) { console.error('[FAIL]', _failures); process.exitCode = 1; }
    else console.log('[PASS] all steps completed');
}

run().catch(e => { console.error('[FATAL]', e); process.exitCode = 1; });
