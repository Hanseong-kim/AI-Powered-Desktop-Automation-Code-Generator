# CLAUDE.md — AI-Powered Desktop Automation Code Generator

Single entry point. Read this first every session.
Details & history → `C:\hansung\note\project\code-generator\dev-log.md`

---

## 1. Commands

```powershell
# Terminal 1 — Express bridge
cd server; node server.js                          # http://localhost:3002

# Terminal 2 — Capture agent (ADMINISTRATOR PowerShell)
cd agent; python agent.py
# Must print: "Administrator rights: YES"

# Terminal 3 — React UI
cd ui; npm start                                   # http://localhost:3000

# Run generated tests (Appium auto-starts, WinAppDriver at 4723 not needed separately)
cd generated-wdio
npx wdio run Calculator/wdio.conf.js               # 폴더 이름 = PascalCase 앱 이름
npx wdio run Notepad/wdio.conf.js

# Regression (no agent needed, server must be running)
python agent/mock_events.py                        # expect 115/115 checks
```

---

## 2. Architecture

```
React UI (3000) --HTTP--> Express (3002) --HTTP--> Python Agent (4444)
      ^                        |
      +------- SSE feed -------+
```

`/api/generate` is template-based (no LLM call) — no API key, no `.env`, no network
call to any AI provider. An earlier architecture generated wdio code via a Groq LLM
call (model swap, execution isolation, prompt patch, TPM-budget chunking into
`_part1`/`_part2` files) — that approach was fully replaced by the current
template-based generator; see §6 for why. The planning docs for that abandoned
approach (`Claude code task v2.md`, `Claude code task server token opt.md`) have
been merged into this file and deleted — nothing there still applies to the
current codebase.

Key files:
- `server/server.js` — `/api/generate` (template-based, no LLM); saves to `generated-wdio/<AppName>/`
- `agent/agent.py` — pynput hooks → raw_queue → worker thread (UIA/COM)
- `ui/src/components/ControlPanel.jsx` — presets: Calculator/Notepad/Paint/Registry Editor/Custom
- `generated-wdio/<AppName>/wdio.conf.js` — generated per app (run with `npx wdio run <AppName>/wdio.conf.js`)

Agent two-thread rule: pynput callbacks enqueue raw dicts only (never UIA).
All UIA/COM runs on the worker thread after `CoInitialize`.

---

## 3. Hard Rules (never break)

- **pynput callbacks**: enqueue + return immediately. No UIA/COM inside hooks.
- **File naming**: PascalCase app name → subfolder name. Server enforces this.
- **No Java, no Playwright**: test-runner 삭제됨, generated-playwright 경로 삭제됨.
  WebdriverIO (JS) 템플릿 생성만 지원.
- **Regression gates**: `python agent/mock_events.py` 115/115 checks
  (simple + session 모드, 좌표 호출 금지 게이트 포함).
- **Documentation honesty**: never write GUI-unverified work as "DONE" in docs.
- **`generated-wdio/*` 직접 편집 금지**: 매 Generate 호출마다 덮어써지는 산출물이므로
  수정은 항상 `server/server.js`/`agent/agent.py` 원본에서.
- **좌표 기반 실행 금지 (2026-07-10 스테이크홀더 지시)**: `osClick(x,y)`/`osClickRel` 등
  좌표 재생은 폴백으로도 사용 금지. 100% AutomationId/ClassName 기반 동적 XPath.
  유니크 ID가 없는 요소는 ID를 가진 이웃 anchor 요소 기준 relative XPath로 해결.
  **재생 경로는 2026-07-11 마이그레이션 완료** (코드 레벨 — GUI 재검증은 §4 Next
  actions). 캡처의 x/y는 dedupe/진단용으로만 유지되고 재생 코드에는 절대 안 나간다.
  셀렉터도 anchor도 없는 이벤트는 조용히 좌표로 폴백하는 대신 **명시적 FAIL 스텝**으로
  생성된다(거짓 PASSED 방지).
- **이벤트 스코프 = Click / Type / DoubleClick / Scroll 4종 (2026-07-10)**: Drag는
  요구사항 아님(07-07 구현분은 유지하되 확장/검증에 시간 쓰지 말 것).
- **Scroll은 UIA ScrollPattern (COM) 또는 InputSimulator로 (2026-07-10)**: PowerShell로
  화면 좌표에 물리 마우스 신호를 주입하는 방식(getRect 기반) 금지 — 픽셀 좌표 없이
  대상 컨테이너를 프로그래밍 방식으로 스크롤해야 함. **2026-07-11 교체 완료**:
  osScroll.ps1 = ScrollPattern 1차 + hwnd-scoped PostMessageW 휠 폴백(SendMessage
  금지 — PoC에서 charmap 크래시), Explorer 라이브 실측 통과(0→0.17).

---

## 4. Current Status  *(update every session)*

**Verified (GUI + npx wdio) — session 13:**

| App | Test | Result | Elapsed |
|-----|------|--------|---------|
| Calculator | `npx wdio run Calculator/wdio.conf.js` | **2 passed** | 46s (36s test) |
| VSCode (mock) | `npx wdio run VSCode/wdio.conf.js` | **1 passed** | 288s* |

*VSCode 288s is mock-only: native dialog not open → Root scan × 4 = ~240s timeout overhead.
In real use (dialog open), Root scan finds hwnd in one query (~5-30s), then cached for remaining steps.

**Architecture change (session 13):**
- `needsSessionSwitching(events)`: single-window non-Electron → `appium:app: exePath` (simple); multi-window or Electron → `appium:app: 'Root'` + `getWindowSession` + `getCenter` + `osClick`
- `afterAll()` (not `after()`) — Jasmine does not define `after()`, caused SKIPPED (0 its registered)
- Electron events bypass `getWindowSession` — use captured `osClick(x,y)` directly (hwnd=0 for web content)
- test-runner 폴더 삭제 (Java/TestNG 완전 제거)
- Calculator preset exePath → AUMID로 수정

**Since session 13 (commit `f268f4e`, 2026-07-06): FreeDM(Qt/QML) + ClaudeDesktop(Electron)
support added** — hwnd 기반 창 추적(`initAppHwnd`/`normalizeWindowSimple`), QML 셀렉터
비유일성 대응(`trustLiveSelector`), `osType`/`osClickRel` 등. 코드 레벨 검증(생성물 확인)은
됐지만 **`npx wdio run` 실제 GUI 통과 로그가 daily note에 없음** — 다음 세션에서 실행하며
`[hwnd]`/`[normalize]` 로그와 함께 결과를 daily note에 남길 것 (문서 정직성 원칙, §3).

**2026-07-07: 드래그 지원 + 재생 신뢰성 + 팝업 Fail-and-Recover v1 (코드 레벨만, GUI 미검증)**
사용자 보고 3건(FDM 텍스트 드래그 미캡처, VSCode 다중창 실패, 예기치 못한 팝업 처리)에 대응:
- **드래그**: `agent.py`가 press/release를 페어링해 `drag` 이벤트(시작/끝 좌표+rel) 캡처
  (기존엔 press만 기록해 드래그 개념 자체가 없었음). `server.js`에 `osDrag`/`osDragRel`
  재생 헬퍼(`OS_DRAG_PS1` — SetCursorPos 보간) + generateWdio drag 분기 추가.
- **다중창 신뢰성**: 모든 execSync PS1 타임아웃 5~8s → 15s 상향, `osClick` 1회 재시도,
  `_warmupPowerShell()`으로 PowerShell 콜드스타트를 beforeAll에서 흡수(VSCode 다중창
  `['osClick','osClick']` 타임아웃 실패의 직접 원인이었음). **좌표 폴백(녹화 좌표 사용)은
  이제 `_failures`로 잡혀 테스트가 FAIL함** — 이전엔 조용히 폴백해 엉뚱한 곳을 클릭해도
  PASS로 나올 수 있었음(거짓 PASSED 위험 제거). 세션 폴백(Root 세션 재사용)은 `_warnings`로만
  기록. `wdioSelectorByClass`에 controlType 태그 제한 추가(무거운/오매칭 XPath 완화).
- **팝업 Fail-and-Recover v1**: 모든 재생 스텝이 `_step()` 래퍼로 감싸짐 — 정상 경로는
  오버헤드 0, 스텝 실패 시에만 `osDismissPopup.ps1`으로 팝업 스캔(owner-PID 스코핑 +
  보수적 버튼 우선순위: 취소/아니요/닫기 먼저) 후 1회 재시도. `OS_MOVEWINDOW_PS1`에
  `IsZoomed` fast-path 추가(이미 최대화/정위치면 no-op — restore 깜빡임 제거).
- 회귀: `mock_events.py` 49/49 → 57/57 (drag 이벤트 1개 + `_warnings`/`osDrag`/`_step(` 체크 추가).
- **미검증(GUI 필요)**: FreeDM 실제 드래그 재녹화+재생, VSCode 다중창 재실행(osClick 타임아웃
  재현 여부), FDM 팝업 시나리오(파일 덮어쓰기) 재생 시 `popup-dismissed` 경고+재시도 통과 확인.

**2026-07-08: doubleClick dedupe + `_step()` ESC 복구 (코드 레벨만, GUI 미검증)**
사용자 보고: VSCode "폴더 열기" 다이얼로그 재생 중 폴더 진입 대신 이름 바꾸기(rename)
모드가 켜짐 — 테스트는 PASSED로 끝나 팝업 Fail-and-Recover v1이 발동조차 안 된
침묵한 의미론적 실패였음. 근본 원인: `agent.py`의 `_emit_click_from_press()`는 물리
더블클릭 1회를 `click`+`click`+`doubleClick` 3개 이벤트로 캡처하는데(반복 단일클릭
"9999" 보존을 위한 의도된 설계), 재생 시 각각이 별도 `_step()`이 되어 스텝 사이
간격이 생기고 탐색기 파일 목록에서 "클릭→대기→재클릭"은 곧 rename 제스처가 됨.
- **doubleClick dedupe**: `server.js`에 `dedupeDoubleClicks()` 추가 — codegen 시점에
  doubleClick 직전의 구성 click(같은 좌표 ±6px, 타임스탬프 ±1.5s 이내) 최대 2개를
  걷어냄. `agent.py`는 무변경(캡처는 full-fidelity 유지, 재시작 불필요).
- **`_step()` ESC 복구 확장**: `osDismissPopup()`이 알려진 버튼을 못 찾으면(rename
  edit-box, 열린 메뉴처럼 버튼 없는 상태) `osActivate('') + osEscape()`(신규
  `osEscape.ps1`)로 1회 더 복구 시도 후 재시도. 두 헤더 템플릿(simple/session) 모두 반영.
- 회귀: `mock_events.py` 57/57 → 68/68 (구성 클릭 2개 삽입 + dedupe/ESC 복구/step count 체크 추가).
- **GUI 검증 완료 (2026-07-08)**: `npx wdio run VSCode/wdio.conf.js` 재실행 — rename 없이
  폴더 진입 확인, 서버 콘솔에 `[dedupe] merged 2 click(s) into doubleClick @(...)` 로그
  확인, STEP 11에서 Fail-and-Recover(`popup-dismissed`)도 실전 발동 확인.

**2026-07-08 (2차): 다이얼로그 파일목록 행 오클릭 수정 (row-name 캡처 + rel 우선순위 예외)**
위 dedupe 검증 중 새 버그 발견: rename은 해소됐지만 재생이 녹화 때와 **다른 폴더**로
진입. 원인: Explorer 리스트뷰 파일 행이 hit-test되면 UIA가 행 자체가 아니라 행 안의
in-place-rename edit 서로게이트(`automationId="System.ItemNameDisplay"`, `Name="이름"`
— 실제 폴더명이 아니라 컬럼 헤더 라벨)를 반환한다. 진짜 폴더명(`run`, `hansung`)은
부모 ListItem에 있는데 캡처가 거기까지 안 올라가 결국 순수 rel좌표(`osClickRel`)로만
재생됐고, "폴더 열기" 다이얼로그는 마지막 방문 폴더/스크롤 위치를 세션 간 기억하므로
녹화 시점 좌표가 재생 시점엔 다른 행을 가리킴.
- **`agent.py`**: `UIAInspector.element_at()`에 행 상승(row-climb) 추가 — hit-test 결과가
  generic 셀(`System.ItemNameDisplay` 또는 automationId/name 둘 다 공백)이면
  `ControlViewWalker.GetParentElement`로 최대 6단계 상위까지 걸어 올라가
  ListItem/TreeItem 조상을 찾아 그 Name(실제 폴더명)을 사용. **agent 재시작 필요**
  (관리자 권한 필요 — 사용자가 직접 재시작).
- **`server.js` dedupeDoubleClicks() 보강**: doubleClick 자신의 hit-test는 더블클릭
  두 번째 press 타이밍 특성상 자주 완전히 비어있음(확인됨 — 3개 doubleClick 전부
  automationId/name/className 없음). 병합 과정에서 걷어낸 구성 click 중 셀렉터가 있는
  것을 doubleClick에 승계.
- **`server.js` useSession 분기 우선순위 예외**: `ListItem`/`TreeItem` 컨트롤은 rel
  데이터가 있어도 이름 기반 라이브 조회(`getWindowSession`+`getCenter`)를 rel재생보다
  우선(`nativeScoped` 조건에 `isRowPicker` 예외 추가) — 다이얼로그 행 위치가 세션마다
  달라질 수 있어 rel좌표보다 신뢰도가 높음.
- **검증**: 합성 이벤트(실제 VSCode 로그의 정확한 패턴 재구성 — click(ListItem
  name='hansung')+click(빈)+doubleClick(빈))를 서버에 POST 후 generate → 생성된
  `click2()`가 `getWindowSession + getCenter('//*[@Name="hansung"]')`를 쓰고(osClickRel
  아님), `_step` 라벨이 `'2:doubleClick hansung'`으로 이름을 승계함을 확인. `mock_events.py`
  68/68 유지(회귀 없음 — Calculator는 simple 모드라 이 경로 자체가 미실행, 별도 커버리지
  갭으로 남음).
- **미검증(GUI 필요, agent 재시작 필수)**: 사용자가 관리자 터미널에서 `agent.py` 재시작 후
  VSCode 폴더 다이얼로그 재녹화 → Generate → `npx wdio run` 재생이 실제로 올바른 폴더로
  진입하는지 확인 필요.

**2026-07-09: scoped 세션 생성 정체 수정 — 타임아웃 정렬 + 실패 hwnd 블랙리스트 (코드 레벨만, GUI 미검증)**
사용자 보고: 재생이 "폴더 열기" 다이얼로그가 뜬 뒤(STEP 5) 진행 안 됨 — scoped session
20s 타임아웃 → Root 스캔이 **같은 hwnd**를 재발견 → 같은 세션 생성을 재시도 → 정체.
근본 원인 3중(상세 → daily/2026-07-09.md): (1) appium-windows-driver는 scoped 세션마다
새 WinAppDriver.exe를 스폰(스폰+status폴링≤10s+WAD재시도≤20s)해 정상 경로도 20s를 넘을
수 있는데 클라이언트 `_appiumFetch`가 정확히 20s에 abort — 서버는 계속 진행해 고아
세션/WAD 누적, (2) Root-scan 폴백이 방금 실패한 hwnd로 `_createSession`을 반복,
(3) 생성 코드의 getCenter-실패 재시도가 캐시를 지워 사이클 반복. 07-08에 통과했던 건
child-hwnd가 즉시 거부돼 Root 재사용으로 폴백됐기 때문 — 7602435(진짜 top-level hwnd)가
오히려 WAD attach 행 경로를 열었음.
- `_createSession`: `'appium:createSessionTimeout': 15000` caps 추가 + 클라이언트 30s
  (서버 예산 ≈28s < 클라이언트 abort 30s → 레이스 제거, 진성 블록 시 30s 백스톱).
- `_scopedFailHwnds` 블랙리스트: 실패 hwnd는 두 경로 모두 재시도 금지 → 곧장 Root 세션
  재사용(rootElId 스코핑, 07-08 실증 경로). hwnd 키라 새 다이얼로그는 재시도 허용.
- hwnd 미발견 시 `_createSession('Root')`로 새 Root 세션 만들던 경로 제거 →
  `browser.sessionId` 즉시 재사용. `[session]` 로그에 소요 ms 추가.
- 검증: `node --check` + `mock_events.py` **68/68** + 오늘 캡처
  (`VSCode_2026-07-09T15-45-02-460Z.json`) restore→generate로 생성물에 반영 확인.
- **미검증(GUI 필요)**: `npx wdio run VSCode/wdio.conf.js` — STEP 5가 ≤~45s 내 폴백
  완료하고 STEP 6+(hansung→project→run→폴더 선택) 진행 확인. agent.py 무변경.

**2026-07-09 (2차): 팝업 복구 오폭 수정 + owned-dialog 16s 생략 + 행 조회 폴링 (코드 레벨만, GUI 미검증)**
16:08 GUI 실행에서 1차 수정은 동작했으나(30s 백스톱·블랙리스트 정상), 새 문제 확인:
STEP 6 `getCenter('hansung')`가 zero-wait 2연속 no-such-element → coord-fallback →
`_step()` 팝업 복구 발동 → **사용자가 Claude Code를 쓰던 VSCode 창을 닫아버림**.
원인: osDismissPopup v1 후보 = "같은 PID의 모든 최상위 창"(VSCode는 단일 프로세스라
사용자 창 포함) + 선호 버튼 `닫기`가 한국어 타이틀바 X 버튼 Name과 동일. 수정 3건
(모두 server.js 템플릿, agent 무변경, 상세 → daily/2026-07-09.md 2차):
- **osDismissPopup v2**: `-exclude`(_hwndCache 전체 — 재생이 조종 중인 창 보호) +
  후보는 dialog-shaped만(같은 PID면 #32770 또는 owned; unowned 메인 창 구조적 탈락;
  타 프로세스 #32770은 owner PID==target일 때만; target 미해석 시 무동작).
- **owned 창 scoped 세션 사전 생략**: `osWindowRect.ps1 -ownerOnly` + `_windowOwner()` —
  WAD가 owned dialog를 항상 거부하는데 16s 태우던 것을 PS 1회(~0.5s)로 대체.
- **`getCenterWithWait()`**: 행 조회 8s 조건 폴링(+중간 1회 캐시 무효화), 다이얼로그
  미발견 시 즉시 실패, 최종 실패 시 `[getCenter-diag]`로 가시 행 Name 덤프(계측).
- 회귀: `mock_events.py` 68/68 → **71/71** (ps1 디스크 재검증 3건 추가).

**2026-07-10: 스테이크홀더 피드백 수신 — 방향 전환 (서면 2건, 코드 무변경)**
기술 가이드라인 + 미해결 지적사항 재전달. 요지 (§3 Hard Rules와 §6에도 반영됨):
- **좌표 실행 전면 금지**: rel좌표 폴백("안전망") 포함 어디에도 금지. 창 이동/리사이즈/
  해상도 변경 시 깨지기 때문. 유니크 ID 없으면 이웃 anchor 기반 relative XPath로.
  → 기존 §6의 "Electron 콘텐츠는 osClick 폴백 허용" 방침 **폐기됨**.
- **스코프 확정: Click / Type / DoubleClick / Scroll 4종**. Drag 불필요 (07-07 구현분
  스코프 아웃 — FreeDM 드래그 GUI 검증 백로그도 함께 드랍).
- **Scroll 재생 방식 교체**: PowerShell 물리 신호 주입 금지 → UIA ScrollPattern (COM)
  또는 InputSimulator로 대상 컨테이너를 픽셀 좌표 없이 직접 스크롤.
- **다중창 = HWND 세그먼팅**: 새 HWND 감지 즉시 이후 이벤트를 그 창 세그먼트로 격리
  캡처, 생성 JS는 세그먼트 XPath 실행 전에 HWND 기반 window-switch 명령을 자동 삽입.
  (현 `getWindowSession`/hwnd 추적 방향과 일치 — 단 좌표 폴백 제거가 전제.)
- **여전히 미해결로 지적된 4건** (JS 코드 생성만 done 인정): ① generic .exe 지원(임의
  exe 경로로 녹화+생성 동작), ② XPath 구성 정확성(`//*[@AutomationId="btnLogin"]` 형태,
  수동 보정 없이 바로 사용 가능해야), ③ 시각적 실시간 재생(현재 "앱만 켜지고 동작 안
  함"으로 인식됨), ④ 다중창/다이얼로그/팝업 추적.
- **테스트 대상 전환: native 앱만** — Electron/Chromium UI(VSCode류) 당분간 제외.
  권장 앱: FileZilla(Site Manager 로그인+파일브라우저), PuTTY(접속 다이얼로그),
  TeamViewer(로그인+대시보드), SSMS(서버 로그인+멀티패널), 7-Zip/WinRAR(폼 밀집).
  다양한 앱(로그인/대시보드/폼/팝업)으로 generic함을 스스로 검증할 것.
- **PoC 3건을 본개발 전에 제출**: ① exe 실행→요소 클릭→AutomationId/ClassName 기반
  clean XPath 캡처, ② 픽셀 계산 없이 UIA/InputSimulator로 native 요소 스크롤,
  ③ 보조 창/팝업 열기→HWND 캡처→이후 클릭을 그 창 컨텍스트로 격리 녹화.

**2026-07-10 (2차): PoC 3건 실행 완료 — 기술 타당성 실증 (standalone 스크립트, `poc/` 폴더,
코드 무변경)**
admin 권한 없이 WinAppDriver REST + UIA COM을 직접 호출하는 재현 가능한 PowerShell
스크립트로 3건 모두 실행. 상세 → `poc/FINDINGS.md`.
- **PoC ① (XPath 클릭)**: 가능 — 이미 부분 실증됨. `services.msc`에서 실제 native
  컨트롤이 숫자 AutomationId 노출 확인(`SysListView32` id=12786 등). Calculator가
  이미 이 경로로 GUI 통과 검증됨(세션 13, 신규 아님). **caveat**: `regedit.exe`는
  매니페스트상 관리자 권한 실행 — 비관리자 스크립트는 최상위 창 속성은 읽히지만
  자식 노드는 UIPI에 막힘(`Stop-Process`도 Access denied로 실증) → §5 "Agent not
  Admin" 트랩과 정확히 일치, 도구 결함 아닌 대상 앱 속성.
- **PoC ② (ScrollPattern)**: 가능 — 실측 완료. Explorer `UIItemsView`에서
  `ScrollPattern.Scroll()` 호출 → `VerticalScrollPercent` 0→0.374 변화 확인,
  픽셀 API 호출 0회. **caveat**: 레거시 컨트롤(MMC ListView, CharGridWClass)은
  ScrollPattern 미지원 — hwnd-scoped `WM_MOUSEWHEEL` 폴백 필요. 폴백 테스트 중
  `SendMessageW`(동기)가 `charmap.exe`를 비정상 종료시킴 → **프로덕션 폴백은
  `PostMessageW`(비동기)로 구현할 것** (WM_CLOSE는 PostMessage로 문제없이 동작 확인).
- **PoC ③ (HWND 세그먼팅)**: 조건부 가능 — "단일 세션 내 switchToWindow" 최적화
  가설은 **기각**. WAD REST로 실측: 데스크톱에 다른 최상위 창이 10개 이상 열려
  있어도 scoped 세션의 `window_handles`는 세션 생성 시점 hwnd 단 1개만 반환.
  즉 브라우저처럼 데스크톱 전역이 아니라 세션에 고정 → **현재 프로젝트의 "신규
  hwnd마다 새 scoped 세션 생성 + owned 창은 Root 세션 폴백"(07-09 구현)이 우회책이
  아니라 WAD의 실제 제약에 따른 필연적 설계였음을 확인**. 다이얼로그 실제 오픈까지의
  end-to-end(Paint WinUI3 메뉴가 InvokePattern으로 안 열림)는 **미완주** — 다음
  세션에서 전통적 Win32 메뉴 앱(PuTTY 등)으로 재시도.
- 부수 발견(스테이크홀더 무관, 안전 기록): 관리자 권한 regedit 프로세스가 비관리자
  세션에서 종료 불가한 채 남음(사용자가 직접 닫아야 함). Win11 Notepad는 단일
  인스턴스로 **사용자의 실제 미저장 탭**을 물고 있음을 발견 — 읽기 전용 조회만
  수행, 추가 입력 없음(안전 확인됨). 향후 Notepad는 자동화 대상에서 피할 것.

**2026-07-11: XPath-only 마이그레이션 — 좌표 재생 전면 제거 (코드 레벨 완료, GUI 미검증)**
2026-07-10 지시(§3 Hard Rules)를 파이프라인 전체에 반영. 상세 →
`daily/2026-07-11.md`, 계획 문서 → `docs/superpowers/plans/2026-07-11-xpath-only-migration.md`.
- **server.js 재생 템플릿**: `osClick`/`osClickRel`/`osDrag`/`osDragRel`/`osScrollRel`/
  `getCenterSimple`/`getCenter(WithWait)` 전부 삭제. simple 모드 클릭 = `browser.$(sel)` +
  `el.click()`(UIA Invoke, 폴백 없음 — 실패는 `_step()` Fail-and-Recover 후 FAIL),
  session 모드 클릭 = `_findScoped`(폴링 유지) + `_clickScoped`(REST element/click,
  doubleClick은 요소 클릭 2회). 셀렉터/anchor 없는 이벤트는 명시적 `_failures.push`
  스텝으로 생성. `osClick.ps1`/`osDrag.ps1` 생성 중단. drag/rightClick은 scope-out
  주석으로만 출력(캡처는 유지 — §3 이벤트 스코프 4종).
- **osScroll.ps1 전면 교체**: `-hwnd -selB64(컨테이너 셀렉터 JSON) -delta` →
  UIA로 컨테이너 재탐색 후 ScrollPattern.Scroll() 1차, 미지원 시 컨테이너 hwnd에
  PostMessageW(WM_MOUSEWHEEL) 폴백. SetCursorPos/mouse_event 완전 제거.
  **Explorer 라이브 실측 통과** (VerticalScrollPercent 0→0.17, delta=-3 방향 일치).
- **agent.py 캡처 확장** (재시작 필요 — 관리자 터미널): ① `anchor_path()` — 유니크
  id/name 없는 요소는 안정적 AutomationId 조상까지 상승하며 `/Tag[i]` 경로 축적 →
  `element.anchorId`/`anchorPath`로 emit, codegen이 `//*[@AutomationId="X"]/Button[3]`
  형태로 사용(형제 60개 초과 시 포기 — 가상화 리스트 인덱스 불안정). ② `scroll_container()`
  — 스크롤 시 IsScrollPatternAvailable(30034) 조상을 찾아 `scrollTarget`으로 emit.
  light-dismiss 오버레이는 anchor 제외(전체 창 덮는 요소라 무의미).
- **회귀 게이트 71→113**: 좌표 호출 5종 금지 체크(ById/ByClass 각각), anchor XPath
  렌더링, scope-out, osScroll.ps1 내용(ScrollPattern+PostMessageW, SetCursorPos 부재),
  osClick/osDrag.ps1 미생성 + **session-mode 시나리오 신설**(MockMulti 2창 —
  `_clickScoped`/`_typeScoped`/`_scrollHwnd`/`launchApp` 검증, §4-7 백로그 해소).
  생성 4파일 ESM 구문 검사 + osScroll.ps1 PS 파서 검사 통과.
- **트레이드오프(알려진 것)**: QML(FreeDM)의 "Invoke가 MouseArea에 안 닿는" 트랩(§5)은
  XPath-only에서 다시 노출될 수 있음 — 스테이크홀더가 native 앱으로 스코프를 좁혔으므로
  수용. Electron 콘텐츠(셀렉터 없음)는 no-selector FAIL 스텝으로 정직하게 실패.

**2026-07-12: XPath-only GUI 검증 통과 + light-dismiss 캡처 레이스 수정 + 잔재 정리 (GUI 검증 완료)**
- **GUI 재검증 완료**: Calculator **2 passed**, Notepad **2 passed** (`npx wdio run`,
  2026-07-12) — XPath-only 재생 경로 실증. 07-11 Next actions #1 해소.
- **light-dismiss 캡처 레이스 수정 (agent.py, GUI 검증됨)**: Notepad 1차 실행은
  STEP 6·8이 `no-selector` FAIL — 녹화 말미의 메뉴바 3연클릭(파일→편집→보기)이
  워커 스레드 hit-test 지연 중 열린 메뉴의 XAML light-dismiss 오버레이에 가려져
  셀렉터가 소실된 것(3건 중 2건 소실 실측). 기존 코드는 오버레이 감지 시 셀렉터를
  버렸는데, 이제 `element_under_overlay()`가 포그라운드 최상위 창 서브트리에서
  오버레이만 스킵하며 같은 좌표를 재탐색(`_deepen(skip_overlay=True)`) — id/name
  있는 요소를 찾으면 채택, 실패 시에만 기존대로 no-selector FAIL 스텝. 재녹화 후
  Notepad 2 passed로 GUI 검증 완료. 성공 시 `[inspect] resolved under light-dismiss`
  로그.
- **XPath-only 잔재 정리**: ① 스코프 아웃 폴더 삭제 — `generated-wdio/FreeDM/`,
  `generated-wdio/ClaudeDesktop/`(마이그레이션 이전 생성물, 금지된 osClick/getCenter
  호출 잔존; VSCode/는 새 템플릿 재생성본이라 유지). ② 유지 폴더의 stale
  `osClick.ps1`/`osDrag.ps1` 삭제. ③ `saveFiles()`에 `OBSOLETE_FILES` 자동 삭제 —
  재생성 시 구식 좌표 헬퍼가 폴더에 남아있으면 지움(재발 방지). ④ MockMulti/는
  회귀 게이트 산출물이라 .gitignore 추가. 현행 생성 ps1 7종(osScroll/osType/
  osActivate/osWindowRect/osMoveWindow/osDismissPopup/osEscape)은 전부 실사용 확인
  — 키보드/창관리/복구용이며 좌표 주입 아님.
- **회귀 게이트 113→115**: 더미 stale 헬퍼를 심고 generate 후 디스크에서 사라졌는지
  확인하는 체크 2건 추가(자동 삭제 로직 커버). 115/115 통과.

**2026-07-12 (2차): PoC ③ E2E 완주 + 제출 문서 (`poc/SUBMISSION.md`)**
- `poc/poc3_dialog_e2e.py` (comtypes COM UIA — agent와 동일 스택): Explorer 파일
  항목 SelectionItemPattern 선택 → 속성 다이얼로그 → 새 #32770 hwnd EnumWindows
  차분 캡처 → '취소'가 메인 창 서브트리에선 미발견/다이얼로그 서브트리에서만
  발견 → UIA Invoke → 닫힘 확인. 픽셀 API 0회. **PoC 3건 모두 증명 완료.**
- 배제 실측 2건(FINDINGS.md 기록): services.msc는 이 머신에서 승격 실행(UIPI
  차단) + 가상 SysListView32는 UIA 아이템 미노출 → MMC류 자동화 부적합.
  .NET managed UIA는 레거시 컨트롤 내부 미노출 → COM 스택 선택 타당성 재확인.
- 제출물: `poc/SUBMISSION.md` — 요구 3건 ↔ 스크립트/명령/실측 결과 매핑.
- README.md 전면 재작성(XPath-only 현행 기준) 같은 날 커밋(`c5cfd3d`).

**Next actions (2026-07-12 이후):**
1. **anchor/scrollTarget 실캡처 확인**: id 없는 요소 클릭 시 `[inspect] anchor XPath ...`
   로그, 스크롤 시 `[scroll] container ...` 로그가 실캡처에서 뜨는지 확인
   (07-12 재녹화에서는 해당 케이스 미발생 — light-dismiss 경로만 검증됨).
2. FileZilla/PuTTY/7-Zip 등 native 앱으로 녹화→생성→`npx wdio run` end-to-end 검증
   (generic 지원 + XPath 정확성 + 시각적 재생 + 다중창을 한 번에 커버).
   PuTTY/7-Zip은 현재 미설치 — 설치 필요 (2026-07-12 확인).
3. Check `INPUT_CONTROL_TYPES` if typing in new apps is silently dropped
   (current set: `{"Edit", "Document", "ComboBox"}`).
4. session 모드 doubleClick = 요소 클릭 2회 근사 — 탐색기류 행에서 rename 오발 여부
   GUI로 확인 필요(문제 시 WAD legacy moveto/doubleclick 엔드포인트 검토 — 단 좌표
   금지 원칙과의 정합 검토 선행).

*(보류됨 — 07-10 피드백으로 우선순위 강등: VSCode GUI 재검증 3종, FreeDM 드래그
재녹화, FreeDM/ClaudeDesktop 실행 기록. Electron 제외 지시 + Drag 스코프 아웃.)*

**Risk:** If a text area's `controlType` is not in `INPUT_CONTROL_TYPES`, typing is filtered.
Verify on each new app type.

---

## 5. Known Traps

- **UWP apps** (Win11 Calculator, Paint, Notepad): `setApp(exePath)` fails → use
  appTopLevelWindow. Clicks/focus capture unreliable. Demo on Win32 (regedit, classic Notepad).
- **By.name exact-match**: UWP window titles change on load. Use XPath `contains(@Name,...)` fallback.
- **Agent not Admin**: element `automationId`/`name` come back empty. Always verify startup log.
- **WordPad**: removed from Win11 24H2. Do not add back as preset.
- **VSCode/Electron content**: Chromium renderer exposes no UIA tree → clicks on
  VSCode's own menus/editor UI fall back to absolute `osClick(x,y)` (breaks if window
  moves). Native dialogs (e.g. "Open Folder") are safe — those use live UIA re-query.
  Untested fix candidate: launch with `--force-renderer-accessibility`.
  **2026-07-10 스테이크홀더 지시로 Electron/Chromium UI는 테스트 대상에서 당분간 제외**
  — native 앱(FileZilla/PuTTY/7-Zip 등)에 집중 (§4, §6).
- **QML/Qt controls (FreeDM)**: `el.click()` (UIA Invoke) can succeed with no error yet
  never reach the real MouseArea. Also `el.getRect()` is unreliable on WinAppDriver —
  use `getLocation()+getSize()`. **2026-07-11 note**: 좌표 폴백 제거로 이 트랩이 다시
  노출될 수 있음 — 스테이크홀더의 native-only 스코프(§4/§6)에 따라 QML 앱은 당분간
  대상 아님. `trustLiveSelector`/`osClickRel`은 마이그레이션에서 삭제됨.
- **`agent.py` requires restart after edits** — no hot reload; changes only take effect
  on next `python agent/agent.py` run.
- Full history → `dev-log.md` | Issues & fixes → `troubleshooting.md`

---

## 6. Design Rationale & Original Requirements

Consolidated from stakeholder feedback (`Feedback.md`, now deleted — merged here;
**updated 2026-07-10** with written technical guidelines) and the abandoned
Groq-based architecture plan (`Claude code task v2.md`, now deleted).
These are the constraints/decisions behind current behavior, not new work items —
except the 2026-07-10 items, which ARE active work (see §4 Next actions).

**Product scope (stakeholder requirements):**
- **Generic app support** — the tool must work with any desktop `.exe` the user
  points it at (e.g. IDM), not just a fixed list of native apps. Users provide any
  exe path and recording + code generation must work. Reflected by the "Custom"
  preset in `ControlPanel.jsx`; **still flagged as unmet on 2026-07-10** — prove it
  by testing varied apps (login screens, dashboards, forms, popups) yourself.
- **Desktop only, no web-based execution** — this is a Windows UIA/WinAppDriver
  automation tool, not a browser automation tool.
- **JavaScript output only** — generated tests must be complete, runnable JS
  (WebdriverIO). See §3 Hard Rules ("No Java, no Playwright"). **The only item
  the stakeholder acknowledged as done (2026-07-10).**
- **XPath-only targeting — coordinates forbidden everywhere (2026-07-10, supersedes
  the earlier "coordinates as accepted fallback" position)** — every clicked/typed
  element must resolve via AutomationId/ClassName to a valid, directly usable XPath
  (e.g. `//*[@AutomationId="btnLogin"]`) with no manual correction. If an element
  lacks a unique ID, generate a relative XPath from a neighboring anchor element
  that has one — never fall back to screen pixels. Rationale: coordinates break on
  window move/resize/resolution change.
- **Event scope: Click, Type, Double-Click, Scroll only (2026-07-10)** — mouse
  dragging is explicitly NOT required.
- **Scroll via UIA ScrollPattern or InputSimulator (2026-07-10)** — no external
  PowerShell scripts injecting physical mouse signals at screen coordinates
  (lag + instability). Scroll the targeted container programmatically, no pixels.
- **Multi-window handling via HWND tracking (2026-07-10, refined)** — every native
  window/popup has a unique OS-assigned HWND. The recorder must listen for new
  window events, capture the new HWND immediately, and group all subsequent
  clicks/typing/scrolls strictly under that window's segment. Generated JS must
  automatically insert a window-switching command (HWND-based) before executing
  that segment's XPaths. Current `getWindowSession`/hwnd tracking (§2, §4) is
  directionally aligned but must shed its coordinate fallbacks.
- **Visual real-time replay is required** — running the generated script must
  visibly perform the recorded clicks/typing/navigation step-by-step, not just
  launch the app and stop. **Still flagged as missing on 2026-07-10** ("running
  the script only launches the app").
- **Popups must be handled dynamically, not assumed away** — do not require the
  user to reset the environment to a pristine pre-recording state before every
  run (e.g. FDM's "file already exists" dialog after a prior download). Since
  these popups still expose classes/automation IDs, they should be detected and
  dismissed programmatically. Addressed by the popup Fail-and-Recover mechanism
  (`osDismissPopup.ps1`, `_step()` wrapper — see §4); GUI verification of real
  popup scenarios is still outstanding (§4 Next actions).
- **Test against native apps only for now (2026-07-10)** — avoid Electron/Chromium
  UI (VSCode-style apps); they don't expose AutomationId/ClassName the same way.
  Suggested targets: FileZilla (Site Manager login + file-browser dashboard),
  PuTTY (connection dialog), TeamViewer (login + dashboard), SSMS (server login +
  multi-panel dashboard), 7-Zip/WinRAR (dense forms: dropdowns/checkboxes/fields).
  This supersedes the earlier "test `--force-renderer-accessibility` on Electron"
  suggestion for now (still listed in §5 as a future candidate).
- **PoC before full development (2026-07-10)** — deliver: ① launch an .exe, click
  an element, capture a clean AutomationId/ClassName XPath; ② scroll a native UI
  element via UIA/InputSimulator without calculating screen pixels; ③ open a
  secondary window/popup, capture its HWND, and record subsequent clicks isolated
  within that window's context.

**Abandoned approach (historical, do not resurrect without re-checking against
the current template-based generator):** the original plan generated wdio code
by calling a Groq LLM (`GROQ_MODEL` constant, `WDIO_SYSTEM_PROMPT`), with a
model swap (`llama-3.3-70b-versatile` → `openai/gpt-oss-120b`) to work around
an upcoming Groq deprecation, plus TPM-budget-based event chunking
(`chunkEventsByBudget`) to keep single requests under the free-tier token
limit. This whole path — model choice, reasoning-effort tuning, chunking,
per-chunk part files — no longer exists; `/api/generate` does not call any LLM.

---

## Assignment grading weights

capture 25% · element inspection 20% · generated code quality 25% ·
architecture/live feed 15% · reliability 10% · docs/demo 5%
