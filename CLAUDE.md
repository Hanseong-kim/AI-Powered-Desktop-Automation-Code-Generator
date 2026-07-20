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

# Run generated tests — standalone, self-starts Appium (2026-07-17: no more
# wdio.conf.js/npx wdio run needed; that path is now a legacy artifact only)
cd generated-wdio/Calculator
node CalculatorTestById.js                         # 폴더 이름 = PascalCase 앱 이름
node NotepadTestById.js                             # (from generated-wdio/Notepad)

# Regression (no agent needed, server must be running)
python agent/mock_events.py                        # expect 197/197 checks
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
- **Regression gates**: `python agent/mock_events.py` 197/197 checks
  (simple + session 모드, 좌표 호출 금지 게이트 포함, 2026-07-17부터 standalone
  실행/window banner 게이트 포함).
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

**2026-07-13: PuTTY 첫 GUI 검증 실패 진단 + 4건 수정 (코드 레벨 완료, GUI 미검증)**
Next actions #2(native 앱 e2e)의 첫 실행 — `npx wdio run Putty/wdio.conf.js`
0 passed/2 failed. 캡처 로그+생성 코드 대조로 근본 원인 4건 규명·수정, 상세 →
`daily/2026-07-13.md`.
- **hit-test가 클릭 지점 밖 요소를 채택**: 트리 indent 여백 클릭(pt가 채택된
  요소 rect 밖)이 물리적으로는 no-op이었는데, 재생의 `el.click()`은 항상 요소
  중심을 클릭해 녹화에 없던 패널 전환을 유발 → 이후 스텝 미발견. `agent.py
  _inspect()`에 pt∈rect 검증 추가 — 밖이면 light-dismiss와 동일 경로(재시도 →
  실패 시 명시적 no-selector FAIL)로 처리.
- **ESC 복구가 앱 자체를 종료**: PuTTY Configuration처럼 다이얼로그 기반 메인
  창에서 ESC=Cancel=앱 종료 — Fail-and-Recover의 ESC 폴백이 무팝업 상황에서
  앱을 죽여 이후 전 스텝이 no-such-window로 캐스케이드. `_step()`(SIMPLE_HEADER)에
  ESC 후 `_appHwnd` 생존 확인 추가 — 죽었으면 재시도 없이
  `esc-recovery-closed-app:<label>` 실패로 즉시 throw.
- **숫자 AutomationId 전면 거부가 native 컨트롤과 충돌**: PuTTY 체크박스/버튼의
  안정적 Win32 리소스 ID(1049/1009 등)까지 걷어내던 것을 `SLOT_INDEX_CONTROL_TYPES
  = {ListItem, TreeItem, DataItem}`일 때로 한정(`wdioSelectorById`/`ByClass`,
  agent.py `anchor_path()` 동일 기준).
- **캡처 agent가 DPI-unaware**(125% 스케일에서 pynput/cursor delta 상시 발생):
  시작 시 `SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)` 격상.
- 회귀 게이트 115→120: `mock_events.py`에 native Win32 다이얼로그 시나리오
  (`MockNative`) 추가 — 숫자 id Button→`~1049` 신뢰, 숫자 id TreeItem→여전히
  Name 스킵, `esc-recovery-closed-app:` 존재 확인. **120/120 통과.**
- **미검증(GUI 필요)**: agent 재시작(관리자) → PuTTY 재녹화 → Generate →
  `npx wdio run PuTTY/wdio.conf.js` 2 passed 확인. Calculator/Notepad 재실행으로
  숫자-id 조건부 허용이 기존 XAML 경로를 안 건드리는지 회귀 확인.

**2026-07-13 (2차): ExpandCollapsePattern 재생 지원 — ComboBox/메뉴/트리 (코드 레벨 완료, GUI 미검증)**
위 4건 재검증 중 PuTTY에 새 실패(STEP 11 "SOCKS 5" 미발견) 발견 + 사용자 요청으로
FileZilla 로그 교차 분석 → 같은 근본 원인으로 수렴: ComboBox 드롭다운/메뉴바
MenuItem/트리 +- 토글은 일반 `el.click()`(InvokePattern)만으로 재현 안 됨.
`poc/diag_expandcollapse.py`(신규, standalone COM UIA)로 사전 실측 후 구현 —
상세 → `daily/2026-07-13.md`.
- **실측**: PuTTY ComboBox는 `ExpandCollapsePattern` 지원 + `Expand()` 후 항목이
  세션 스코프에서도 발견됨(단순 케이스). FileZilla 메뉴바 MenuItem은 `Expand()`
  자체는 성공해도 하위 항목이 원래 서브트리가 아니라 **새로 뜨는 별도 최상위
  팝업 창**(`#32768`, 네이티브 `TrackPopupMenu`)에만 존재 — WinAppDriver 세션은
  이 새 창을 못 봄(PoC ③ 세션 고정 제약과 동일 부류).
- **agent.py**: `has_expand_collapse()`로 클릭 요소에 `expandCollapse` 태깅
  (`_emit()`의 이벤트 필드 화이트리스트에도 추가 필요 — 빠뜨리면 서버로 조용히
  안 감, 실제로 빠뜨렸다가 잡음). `tree_item_at_row()`로 pt-밖-rect 폴백이
  전체 Tree 대신 클릭 y가 걸치는 TreeItem 자체를 채택하도록 정밀도 개선.
  `_foreground_is_target()`에 PID 즉시 자가-치유 추가(`_target_pids()` 공유
  헬퍼, UWP CoreWindow 지연-등록과 같은 자리에 일반 Win32 창도 커버 — 예방적).
- **server.js**: 신규 `osExpandCollapse.ps1`(osScroll.ps1과 동일 관례) — 대상
  `Expand()` 후 itemName 있으면 (a) 같은 창 서브트리 → (b) `EnumWindows` 차분으로
  잡은 새 팝업 창 서브트리 순으로 찾아 `Invoke()`, WinAppDriver REST 안 거침.
  `mergeExpandCollapseClicks()`가 캡처된 "열기+항목" 클릭 2건을 `dedupeDoubleClicks()`
  와 같은 codegen-time merge 패턴으로 `osExpandCollapse()` 호출 하나로 병합
  (ComboBox/MenuItem만 대상, TreeItem 토글은 itemName=null 단독 호출).
  `wdioSelectorById`의 Name-fallback에도 `ByClass`와 동일한 ControlType 태그
  제약 추가(FileZilla 조사에서 발견된 비대칭 수정).
- 회귀 게이트 120→124: MockNative 시나리오에 병합/미병합/스텝-개수 체크 4건
  추가. **124/124 통과.**
- 실제 캡처(`recorded-events/PuTTY_..._16-36-39...json`) 재생성으로 부분
  교차검증 — item5(태그 제약)는 반영 확인, 단 이 캡처는 `expandCollapse` 캡처
  수정 **이전** 기록이라 메타데이터가 없어 ExpandCollapsePattern 경로 자체는
  미발동(예상된 한계 — 버그 아님).
- **미검증(GUI 필요, agent 재시작 필수)**: PuTTY Proxy-type 콤보 + Window 트리
  토글, FileZilla 파일(F) 메뉴 재녹화 → Generate → `npx wdio run` 2 passed 확인.

**2026-07-13 (3차): 심각한 회귀 발견·수정 — expandCollapse 태깅이 정상 클릭까지 가로챔**
사용자가 (2차) 구현 직후 FileZilla를 재녹화해 "아무 클릭도 수행 안 함" 보고 →
생성된 `FileZillaTestById.js`가 캡처된 클릭 9개 **전부**를 `osExpandCollapse(...,
null)`(펼치기 전용, 실제 클릭 없음)로 대체한 것 확인. 원인: `_inspect()`의
expandCollapse 태깅이 "이 요소가 ExpandCollapsePattern을 **지원하는지**"만
봤음 — TreeItem은 자식 있으면 거의 항상 이 패턴을 구현하고, Windows 파일 열기
다이얼로그 주소창 breadcrumb Edit 세그먼트도 화살표 드롭다운 때문에 지원해서,
정상적으로 잘 동작하던 폴더 탐색/breadcrumb 클릭까지 전부 가로챔(실제 캡처
JSON 확인: 전부 `pt`가 요소 자체 rect 안 — glyph 여백 아닌 정상 클릭이었음).
**수정**: 태깅을 `ComboBox`/`MenuItem`(항상)과 `TreeItem`의 glyph 폴백 경로
(pt가 rect 밖이라 행 단위로 재해석된 경우만)로 한정 — 그 외는 지원 여부와
무관하게 절대 태깅 안 함. `mock_events.py` 124/124 재확인(서버 로직은 처음부터
옳았음 — 버그는 agent.py 캡처 태깅에만 있었음). **agent 아직 재시작 안 됨,
다음 재녹화 전 필수.** 교훈: UIA 패턴 "지원 여부"는 근본적으로 과다 신호 —
진단으로 실증된 특정 ControlType/경로로만 좁혀야 안전.

**2026-07-13 (4차): 별개의 새 버그 — 첫 클릭이 엉뚱한 창의 요소로 캡처됨 (코드 레벨 완료, GUI 미검증)**
(3차) 수정 후 재녹화해도 "아무 액션 없이 즉시 실패" 재현 → 캡처 JSON 확인
결과 ExpandCollapsePattern과 무관한 별개 버그(`expandCollapse:false`) 확정.
창 발견 직후(~2초) 첫 클릭이 PuTTY에 없는 `name:"Calculator"` `Edit` 요소로
캡처됨 — rect가 PuTTY 창 범위 밖인데도 `windowTitle`은 `describe()`의
`GetForegroundWindow()` 폴백(요소 자체 hwnd=0일 때) 덕에 우연히 "PuTTY
Configuration"으로 찍혀 혼란 유발. Win32 창 필터(`_point_is_target`)는
정상 통과 — Win32 창 판정과 UIA `ElementFromPoint` hit-test가 서로 불일치.
**수정**: `UIAInspector.resolve_root_hwnd()`(조상 walk로 실제 소유 hwnd 탐색)
추가, `_inspect()`에서 이 hwnd가 `target_hwnds`/`_popup_hwnds` 밖이면
light-dismiss와 동일하게 선택자 버림. 상세 → `daily/2026-07-13.md`.
**GUI 검증 완료**: 재녹화에서 이 교차검증이 여러 번 실전 발동해 정확히
복구함(Calculator 창은 안 열려있었음 — 그 가설은 기각, 근본 원인은 여전히
미확정이지만 방어 로직이 매번 성공적으로 복구).

**2026-07-13 (5차): 네 번째 버그 — `osExpandCollapse.ps1`가 PuTTY의 ID 재사용으로 엉뚱한 요소를 찾음 (코드 레벨 완료, GUI 미검증)**
(4차) 검증 중 STEP 10("Proxy type:" 콤보박스)에서 `ExpandCollapsePattern not
supported on target`으로 실패 → ESC가 앱 종료. 원인: `OS_EXPANDCOLLAPSE_PS1`이
`automationId`를 단독 조건으로 먼저 탐색하는데, PuTTY는 카테고리 패널마다
숫자 ID를 재사용(실측: `id=1044`가 "None"/"Control-H" 라디오와 "Proxy
type:" 콤보 셋 다에 붙음) — `FindFirst`가 라디오 버튼을 먼저 찾아버려
패턴 없음으로 오탐. **수정(이 부분만, 사용자 요청대로 다른 정상 동작 미변경)**:
automationId/name/className 전부 AND로 묶은 조건을 먼저 시도, 실패 시 기존
필드별 단독 조건 폴백. `node --check` + PowerShell 파서 + `mock_events.py`
124/124 확인. agent.py 무변경이라 **재시작 불필요** — PuTTY Generate만
다시 하면 됨. **미검증(GUI 필요)**: `npx wdio run` STEP 10 통과 확인.

**2026-07-13 (6차): "DropDown 버튼" 트리거+검색 타이밍 갭 (코드 레벨 완료, GUI 부분검증)**
ISO-8859-1 재검증에서 여전히 실패 — `osScopedInvoke`는 정확히 발동됐지만
(`(cross-window)` 라벨 확인) 항목을 못 찾음: "DropDown" 버튼 클릭과 항목
검색이 별도 스텝(별도 PowerShell 프로세스)으로 쪼개져 그 사이 지연 동안
드롭다운이 자동으로 닫힘. **수정**: `mergeCrossWindowTriggerClicks()` 추가
— 메인 창 클릭 바로 다음이 창-교차 클릭이면 병합, `osScopedInvoke.ps1`에
`-triggerSelB64` 추가해 트리거 클릭→항목 검색을 같은 프로세스 실행 안에서
처리. 실제 캡처로 재생성해 병합 확인(`osScopedInvoke(_appHwnd, {ISO-8859-1},
{DropDown 트리거})`). `mock_events.py` 126/126. **알려진 남은 갭**: 콤보를
다시 열어 스크롤 후 다른 항목(UTF-8) 선택은 "클릭→스크롤→클릭"이라 이번
병합(클릭+클릭만) 대상 밖.

**2026-07-13 (7차): 근본 원인 확정 — `/api/start`의 events 초기화 레이스 컨디션 (수정 완료, 서버 재시작 완료)**
같은 날 세 번째 재녹화에도 `session_meta`가 캡처 파일에서 통째로 빠지는
현상의 진짜 원인 확정. `agent.py`의 `recorder.start()`는 워커 스레드를
비동기로 띄우고 즉시 반환해 `/start` HTTP 응답이 매우 빨리 돌아가는데,
`server.js`의 `POST /api/start`는 `await callAgent('/start', ...)` **이후에**
`events = []`를 실행 — 워커 스레드가 독립적으로 `_discover_target_windows()`
+ `_emit_session_meta()`(동기 POST)를 처리하므로, **창 발견이 충분히
빠르면**(오늘처럼 같은 앱을 반복 실행해 OS 캐시로 창이 매우 빨리 뜨는
경우) session_meta POST가 서버에 먼저 도착한 뒤 `events = []`가 그걸
지워버리는 레이스가 간헐적으로 실제 발생. **수정**: `events = []` +
`sessionBackupFile` 설정을 `callAgent()` 호출 **이전**으로 이동 — agent가
아무리 빨리 반응해도 그보다 먼저 초기화가 끝나 있음을 보장. agent.py
무변경(server.js만 수정, 재시작 완료). `mock_events.py` 126/126. **교훈**:
비동기 백그라운드 스레드가 관여하는 두 서비스 간 "요청-응답 이후 상태
초기화" 패턴은 스레드 타이밍에 따라 간헐적으로 깨질 수 있음 — 레이스가
애초에 발생할 수 없는 순서로 재배치할 것.

**2026-07-14: PuTTY 재녹화 후 새 증상 3건 수정 — 타이틀바 X 종료 / expandCollapse
managed-UIA 맹점 / ESC 앱종료 (코드 레벨 완료, GUI 미검증)**
07-13 RC1(녹화 미완)을 사용자 재녹화로 해소 후 여전히 0/2. 로그로 근본 원인 3건 확정,
전부 `server.js`에서 수정(agent.py 무변경 → 재시작 불필요). 상세 → `daily/2026-07-14.md`.
- **RC-A(ByClass가 스스로 X로 종료):** 콤보 재오픈 클릭(`automationId="DropDown"`
  `name="닫기"`)이 `//Button[@Name="닫기"]`로 생성 → 한국어 Windows 타이틀바 Close(X)
  버튼(UIA `Button` name="닫기")에 매칭돼 앱 종료. **Fix**: open+scroll+select를
  `osScopedInvoke(item,trigger)` 하나로 병합(중간 스크롤 드롭, 07-13 6차 갭 해소) +
  DropDown 화살표는 항상 `~DropDown`로 생성(가드).
- **RC-B(ById STEP 11 "Window" expandCollapse 실패):** `osExpandCollapse.ps1`이 .NET
  managed UIA라 레거시 `SysTreeView32` TreeItem 못 봄(`poc/FINDINGS.md:118-129`).
  **Fix**: `osExpandCollapse.py`(comtypes COM UIA, `ExpandCollapsePatternId=10005`)로
  재작성 — osScopedInvoke.py/osScroll.py와 스택 일원화. OBSOLETE_FILES에 .ps1 추가.
- **RC-C(양쪽, 녹화보다 빨리 종료):** `_step()` ESC 복구의 `osActivate('')+osEscape()`가
  메인 다이얼로그를 foreground로 올려 ESC=Cancel=종료. **Fix**: `osActivate('')` 제거 +
  `osForegroundHwnd()`로 foreground==_appHwnd면 ESC 스킵, 다른 창(실제 팝업)이
  foreground일 때만 ESC.
- 회귀 게이트 126→**148** (병합-across-scroll/DropDown 가드/osExpandCollapse.py COM/
  ESC-foreground 스킵 체크 추가). 생성 JS 6종 node --check + 생성 py 3종 py_compile +
  `-EncodedCommand` 실측 통과.
- **미검증(GUI, agent 재시작 불필요)**: PuTTY 재-Generate → `npx wdio run` 2 passed
  (ByClass 타이틀바-X 없음, "Window" COM expandCollapse 성공, UTF-8 도달, esc-recovery-
  closed-app 없음, `[cross-window-merge] ... (dropped intervening scroll)` 로그) +
  Calculator/Notepad 회귀 확인.

**2026-07-15: 7-Zip 첫 e2e — 독립적인 버그 5건 발견·수정 + 실제 재녹화로 GUI 검증**
7-Zip을 새 native 타겟으로 추가한 뒤 첫 녹화→재생에서 연쇄로 5개 버그 발견, 전부
실측(COM UIA/WinAppDriver 프로브, 레지스트리 확인)으로 확정 후 수정. 상세 →
`daily/2026-07-15.md`.
- **버그1 (agent.py)**: `_inspect()`의 "untracked window" 판정이 `_watch_windows()`의
  ~0.5s 폴링과 레이스 — 정상 팝업 요소를 드롭. `_foreground_is_target()`의 PID
  self-heal 패턴을 클릭 경로에도 적용. **GUI 검증됨** (`[inspect] PID self-heal ...`
  발동 확인, 2/2 PASSED).
- **버그2 (server.js)**: 세션 모드에서 메인 창과 다이얼로그가 리터럴로 같은
  타이틀("7-Zip")을 쓰면 `getWindowSession(title)`의 title-키 캐시가 둘을 구분 못 해
  다이얼로그의 죽은 세션을 메인 창 클릭에도 재사용 → click-not-found. Cross-window
  이벤트는 세션 모드에서도 hwnd 기반 `osScopedInvoke`를 타도록 `!useSession` 게이트
  제거 + SESSION_HEADER에 `osScopedInvoke` wrapper 추가.
- **버그3 (server.js)**: `dedupeDoubleClicks()`가 위치/타이밍만 보고 병합 —
  리스트가 클릭 위치 아래서 갱신되면(예: "컴퓨터"→"C:") 관련 없는 선행 클릭까지
  같은 픽셀이라는 이유로 삼켜버림. 병합 run 안 요소 이름(name) 불일치 시 중단하는
  가드 추가. **(2차, 실제 재녹화 검증 중 재발견)**: name만 추적하는 가드는
  automationId만 있고 name은 빈 더블클릭(리스트 컨테이너 자체, 예: "1001")엔
  무력 — targetName이 끝까지 안 채워져 뒤이은 무관한 실제 클릭("hansung" 폴더
  진입)까지 통과시켜 삼켜버림. `targetAid`(automationId)도 함께 추적, 후보가
  name/automationId 중 하나라도 값이 있으면 이미 확립된 식별자와 일치해야
  병합 허용하도록 강화(양쪽 다 완전히 빈 후보만 통과, 2026-07-08 원래 취지).
- **버그4 (agent.py + server.js)**: WinAppDriver의 `element/click`이 7-Zip 리스트
  행에서 조용히 무반응(실측: 직접 COM UIA Invoke는 즉시 목록 갱신, 완전히 동일
  요소에 WAD REST click은 클릭 전/후 상태 동일). 근본 원인은 `agent.py` 캡처 —
  리스트 행의 "Edit" 타입 서로게이트가 이미 올바른 Name을 갖고 있어 row-climb
  가드(`not aid and not name`)를 통과 못 해 실제 `ListItem`으로 안 올라감(VSCode의
  "이름 없는" 서로게이트, 2026-07-08, 의 반대 변종). `element_at()` 가드에
  `not aid and controlType==Edit` 케이스 추가 + `server.js`가 캡처된
  `controlType==='ListItem'` 클릭/더블클릭을 `osScopedInvoke`(직접 Invoke)로 재생.
- **부수 발견(코드 버그 아님)**: 7-Zip이 마지막 방문 폴더를
  `HKCU\Software\7-Zip\FM\PanelPath0`에 영속화 — 다음 실행이 "컴퓨터" 루트가
  아니라 그 폴더로 곧장 열려 캡처가 가정한 시작 상태와 어긋남. `KNOWN_APP_STATE_RESET`
  맵(`newWindowArgsFor`와 같은 앱별 특례 패턴) + `wdio.conf.js`의 `onWorkerStart`
  훅(스펙별 워커가 자기 앱을 띄우기 직전, **`onPrepare` 아님** — 처음 `onPrepare`로
  넣었다가 두 번째 스펙이 재오염된 상태로 실패하는 것으로 이 타이밍 버그를 발견)으로
  각 워커 launch 직전에 리셋.
- **검증 방법 (1단계)**: agent.py는 관리자 권한 필요해 이 세션에서 재시작 불가 —
  사용자의 원본 캡처를 파이썬으로 `controlType: 'Edit'→'ListItem'`(agent.py
  재시작 후 캡처했을 값) 패치한 사본으로 `/api/events/restore`→`/api/generate`→
  `npx wdio run` 반복 검증. **SevenzipTestById.js + SevenzipTestByClass.js
  둘 다 PASSED (2 passed, 2 total)**.
- **검증 방법 (2단계, 실제 재녹화)**: 사용자가 관리자 터미널에서 agent.py를
  직접 재시작하고 더 깊은 흐름(컴퓨터→C:→$Recycle.Bin→hansung→목록→west→목록,
  전부 더블클릭 포함)으로 재녹화 — 캡처 확인 결과 `controlType`이 실제로
  `'ListItem'`/`'List'`로 찍힘(버그4 agent.py 수정 라이브 확인). 1차 실행에서
  버그3(2차) 발견(STEP5 "click hansung" 소실), 수정 후 재실행:
  STEP1,2,4,5,6,7,8 전부 `osScopedInvoke`로 성공, **STEP3("click C:")만
  실패** — 1단계와 동일한 부류의 실제 중복 클릭(같은 자리 재클릭)이 이번
  재녹화에도 재현된 것으로 확인, 파이프라인 결함 아님. `mock_events.py`
  **148/148** 유지(버그3 2차 수정 반영 후 최종 확인).
- **정정(3차)**: STEP3 "click C:"를 "STEP2 진단이 틀렸다"고 재정정했다가
  그 재정정 자체가 틀렸음을 확인 — 근거로 쓴 JSON `"timestamp"`가 물리적
  클릭 시각이 아니라 `agent.py:1565`의 `time.time()`(워커 스레드 처리 시각).
  `agent.py:_emit_click_from_press()`(1430행) 추적 결과 더블클릭 페어링 자체가
  이미 위치+시간만으로 결정되며(대상 identity 무시) 원본 데이터에 이미 모호성이
  내재 — 재수정 계획(간격 500ms 축소 + earliest 채택)을 대입하면 오늘 오전
  고친 "click 컴퓨터 삼켜짐" 버그가 재발함을 확인해 **구현하지 않고 철회**.
  결론: 오전 배포된 dedupe 가드가 맞고, STEP3는 원래 진단대로 진짜 중복
  클릭(파이프라인 버그 아님). 상세 → `daily/2026-07-15.md`.
- **버그6 (server.js, 실측 확정·수정 완료)**: 위 재검토 중 `osScopedInvoke`의
  창-교차 (b) 폴백이 완전히 무관한 실제 창(사용자의 파일 탐색기
  `explorer.exe`/`CabinetWClass`, VS Code `Code.exe`)을 클릭하고 "성공"으로
  잘못 보고하는 것을 실측으로 확정 — 거짓 PASSED + 사용자 창에 실제 부작용.
  `OS_SCOPEDINVOKE_PY`의 (b) 단계를 `GetWindowThreadProcessId`로 메인 창과
  같은 PID인 창만 검사하도록 한정(PuTTY/FileZilla 같은 원래 의도된 케이스는
  영향 없음). 재검증: `click hansung`/`click west`가 이제 정직하게
  `target not found`로 실패(수정 전 위장 성공 제거), `컴퓨터`/`C:`/
  `$Recycle.Bin`은 `invoked under main window subtree` 유지. `mock_events.py`
  **148/148**(회귀 없음).
- **미검증**: `hansung`/`west`가 메인 창 서브트리에서도 못 찾는 근본 원인
  (가상화 리스트 `SysListView32`/`LVS_OWNERDATA` 가설, `ItemContainerPattern.
  FindItemByProperty` 등 검토 필요) + 더블클릭 사이 UI 안정 대기를 두고
  재녹화한 최종 `npx wdio run` **2 passed** 확인. Calculator/Notepad/PuTTY/
  FileZilla 회귀 재확인도 안 됨(오늘 수정은 전부 가산적 게이트라 예상상
  안전하나 저장 캡처 없어 재생성 불가).

**2026-07-16: 리뷰 피드백(Hamza) 대응 — 멀티윈도우 세그먼팅 구현 (코드 레벨 완료, GUI 미검증)**
2026-07-16 리뷰 허들 피드백 3건 중 코드로 대응 가능한 멀티윈도우 관련 2건에 착수.
CLAUDE.md에 2026-07-10부터 "설계는 됐지만 구현 안 됨"으로 남아있던 "다중창 =
HWND 세그먼팅"을 실제로 구현. 근본 원인이 처음 가정(캡처 드롭)과 실제로는
다른 곳에 있었음이 테스트 작성 중 드러남 — 상세 아래.
- **agent.py**: `_emit()`에 `event["newWindowSegment"]` 신호 추가
  (`_last_emitted_hwnd_hex` 인스턴스 상태로 직전 이벤트와 `rootHwndHex` 비교) —
  codegen이 title 비교가 아니라 hwnd 기반의 확실한 세그먼트 경계를 받는다.
  필드 없는 구버전 캡처는 server.js가 hwnd-diff 폴백을 그대로 쓴다.
- **server.js `_switchWindow()` 추가**: 세그먼트 경계마다 `_sessionIds[title]`/
  `_hwndCache[title]`를 무효화하고 `getWindowSession()`을 강제 재조회한다.
  다이얼로그가 닫히고 같은 리터럴 타이틀의 메인 창으로 돌아왔을 때(예:
  7-Zip) 죽은 세션/hwnd를 계속 재사용하던 2026-07-15 "버그2"를 cross-window-
  trigger 경로뿐 아니라 일반 `getWindowSession` 경로에서도 고친다. 녹화 시점
  hwnd 값은 재생 시 재사용 불가(창마다 매번 새로 배정)하므로 복합 키가 아니라
  "경계 통과 시 강제 재조회" 방식을 택함.
- **생성 JS에 명시적 `switch to window:` 스텝 삽입**: codegen 이벤트 순회
  루프에서 세그먼트 경계 감지 시 `_step('switch to window: ...', ...)`을
  별도 스텝으로 삽입 — 기존엔 `getWindowSession()` 내부에서만 암묵적으로
  전환돼 리플레이 로그에 안 보이던 것을, Hamza가 요구한 "window1/window2
  액션 구분 리스트업"이 실제로 보이도록 표면화.
- **근본 원인 정정(테스트 작성 중 발견)**: 타이틀 충돌 회귀 케이스를 만들어
  검증하던 중, 위 3개 수정을 다 반영해도 여전히 전환이 0회로 나오는 현상
  발견 — `needsSessionSwitching()`이 `roots.size!==1`일 때도 `titles.size>1`을
  추가로 요구해, **서로 다른 두 창이 리터럴로 동일한 타이틀을 쓰면
  세션 모드 자체가 발동 안 함**(창 전환 로직이 통째로 미실행)이 진짜 근본
  원인이었음을 확인. `getWindowSession`의 캐시 키 문제는 실재하지만 이보다
  하위 증상이었음. `needsSessionSwitching()`을 rootHwndHex를 title보다 우선하는
  ground truth로 수정(`roots.size>1`이면 title 무관하게 즉시 세션 모드,
  `roots.size===1`이면 title 무관하게 즉시 simple 모드, rootHwndHex가 아예
  없는 구버전 캡처만 title 비교로 폴백).
- **회귀 게이트 148→157**: `mock_events.py` `SESSION_EVENTS`에 왕복 재방문
  케이스(hwnd A1B2→C3D4→A1B2) 추가 + 신규 `MockCollision` 시나리오(동일
  리터럴 타이틀 "7-Zip", 다른 hwnd E1E1→F2F2→E1E1) 추가. `_switchWindow()`
  존재, 세그먼트 경계마다 정확한 횟수의 전환 스텝 생성, 타이틀 충돌에도
  전환이 억제되지 않음을 확인하는 체크 9건 추가. **157/157 통과**
  (`node --check server/server.js`, `python -m py_compile agent/agent.py`,
  실제 서버 기동 후 `python agent/mock_events.py` 재확인 — 서버 재시작 필요,
  최초 1회는 편집 전 뜬 stale 프로세스에 요청이 가 실패했다가 재시작 후 통과).
- **PowerShell/Python 헬퍼 파일 필요성(리뷰 피드백 1번)**: 코드 변경 없음 —
  기존 `poc/FINDINGS.md`/`poc/SUBMISSION.md`/§5 UIA Stack Fragmentation에
  이미 실증된 근거(WinAppDriver REST에 스크롤 API 없음, 세션이 생성 시점
  hwnd 1개 고정이라 새 팝업 창을 못 봄, managed UIA가 레거시 Win32 컨트롤을
  못 봄) + 인라인화(`-EncodedCommand`) 가능성 조사 결과(파라미터 있는 스크립트는
  매 호출 재인코딩 필요 + 생성 파일마다 수백 줄 중복 + 디버깅 시 파일 없어
  에러 스택 불투명 → `osForegroundHwnd()`가 파라미터 없는 6줄짜리에만
  안전하게 적용된 이유)를 정리해 서면 응답 근거로 삼음. 응답 자체(Slack 전송)는
  사용자 몫.
- **GUI 검증 결과 (같은 날 후속 세션)**: FileZilla로 실제 재생 검증 완료 —
  agent.py 재시작/재녹화 없이도(이미 녹화된 이벤트로 재생성만) `switch to window:`
  스텝과 화면2 클릭/메뉴 선택이 전부 정상 동작 확인. 상세 → 아래
  "2026-07-16 (2차)" 항목(버그 A/B/C/D).

**2026-07-16 (2차): FileZilla 멀티윈도우 재생 GUI 검증 통과 — 버그 A/B/C/D 전부 실전 확인**
위 세션의 코드 레벨 완료 사항을 실제 FileZilla GUI 재생(`npx wdio run FileZilla/wdio.conf.js`)으로
검증하는 과정에서 추가로 버그 3건을 더 발견·수정했고, 최종적으로 **FileZillaTestById.js +
FileZillaTestByClass.js 둘 다 2회 연속 PASSED**로 GUI 검증을 마쳤다.
- **버그 A (당일 만든 기능의 버그)**: `switch to window` 스텝을 hwnd 경계마다 무조건
  삽입하던 것이, `osScopedInvoke`(hwnd 직접 COM, 세션 불필요) 경로에도 걸려 실측 20초씩
  낭비(찾지도 못하고 버려짐). `getWindowSession()`을 실제로 호출하는 두 분기(session-mode
  type, plain click)에만 삽입하도록 스코프 축소. (커밋 `0fe6823`)
- **버그 B (근본 원인, 기존 버그)**: `mergeExpandCollapseClicks()`는 useSession 여부와
  무관하게 항상 "메뉴 열기+항목 클릭"을 병합하지만, 그 병합 결과를 재생하는 분기가
  `!useSession`으로 막혀 있어 **session 모드(FileZilla 등 진짜 멀티윈도우 앱)에서는
  메뉴 항목 선택 자체가 통째로 스킵**됐다(원인: 그 분기가 SIMPLE_HEADER 전용 변수
  `_appHwnd`를 하드코딩해서 session 모드에 걸면 ReferenceError). Site Manager 다이얼로그가
  재생 중 한 번도 실제로 열리지 않았던 진짜 원인. `!useSession` 게이트 제거 +
  cross-window/ListItem 분기와 동일한 `_hwndCache[_mainTitleFrag]` 패턴 재사용. (커밋 `9355f63`)
- **버그 C (기존 버그)**: `OS_SCOPEDINVOKE_PY`가 `TreeScope_Descendants`만 써서, 캡처된
  클릭 타겟이 창 자기 자신(예: Site Manager 다이얼로그의 `className="#32770"` 루트)인
  경우 구조적으로 못 찾음(Descendants는 자식만 검색, 루트 제외가 UIA 표준 동작).
  `TreeScope_Subtree`(Element+Children+Descendants)로 교체. (커밋 `9355f63`)
- **버그 D (버그 B 수정의 부작용, 같은 날 발견)**: 버그 B 수정으로 session 모드에서도
  `osExpandCollapse()`를 호출하게 만들었는데, **그 JS wrapper 함수 자체가 SESSION_HEADER
  템플릿에 정의돼 있지 않았음**(SIMPLE_HEADER에만 있었음, `osScopedInvoke`/`osScrollEl`은
  둘 다 있는데 이것만 빠짐) — 실제 GUI 재생에서 `ReferenceError: osExpandCollapse is not
  defined`로 즉시 확인. SESSION_HEADER에도 동일 wrapper 추가로 해결. (커밋 `2ebbe3d`)
- **회귀 게이트 157 → 163**: 타이틀 충돌/왕복 세그먼트 케이스(157) + session 모드
  expandCollapse 호출부/정의 존재 체크 2건 추가(161, 163). "정의가 실제로 있는지"까지
  체크하게 만든 게 버그 D를 다음부터는 코드 레벨에서 잡아낼 수 있게 하는 핵심 — 이번엔
  "호출부만 있는지" 체크가 놓친 것을 실제 GUI 실행에서 뒤늦게 발견했다(교훈: 코드생성
  프로젝트에서 "함수 정의 존재 여부"는 "호출부 존재 여부"와 별개로 반드시 검증해야 함).
- **실전 재생 로그 확인 사항**: `[osExpandCollapse] invoked '사이트 관리자(S)...'`,
  `[osScopedInvoke] invoked under main window subtree`(cross-window 클릭 2건),
  `[STEP] switch to window: FileZilla`(불필요한 지연 없이 1회만 발동) — 전부 의도대로
  동작. `FileZillaTestById.js`/`FileZillaTestByClass.js` 각각 2회 연속 **1 passing**.
- **주의(디버깅 과정에서 확인한 운영상 함정)**: `server.js`를 고쳐도 이미 생성된
  `generated-wdio/<App>/*.js`는 자동으로 안 바뀐다(정적 산출물) — 서버 프로세스
  재시작(hot reload 없음) + UI에서 Generate 재실행이 반드시 필요하다. 이번 세션에서
  "재녹화해야 하나?"라는 질문이 나왔을 정도로 헷갈리기 쉬운 지점 — 재녹화가 아니라
  재생성(Generate)만 필요했다.
- **미검증**: "2차 화면 요소 캡처 자체가 누락되는지"(리뷰 피드백 3번, `_watch_windows()`
  타이밍/PID 범위 가설)는 이번 FileZilla 재생에서 재현되지 않아 별도로 확정 못함 —
  이번 재생에서는 모든 스텝이 캡처 문제 없이 통과했으므로, 다음에 재현되는 앱이
  나오면 그때 로그 기반으로 진행.

**2026-07-17: 스테이크홀더 반박 메일 대응 — setup-dependency 갭(①) + 멀티윈도우
코드 구조화 갭(③) 해소, ①은 실제 GUI로 완전 검증, ③은 코드/회귀-테스트 레벨
검증 (FileZilla 실전 재확인은 미완)**

07-10 서면 피드백 3건 중 ②(시각적 실시간 리플레이)는 이미 완료로 확인됐던 상태에서,
남은 두 건에 착수. 상세 → `daily/2026-07-17.md`.
- **① Setup dependency 해소 (`node <file>.js` 단독 실행)**: `server.js`에
  `STANDALONE_PREAMBLE`(공유 `ensureAppium()` + 순수 Appium-REST 헬퍼)을 신설해
  `SIMPLE_HEADER`/`SESSION_HEADER` 양쪽에 병합, Jasmine `describe/it` 래퍼를
  일반 `async function run()`으로 교체, WDIO `browser` 전역 참조를 전부
  제거(`_clickBySid`/`_typeScoped`가 대신 raw Appium REST로 클릭/타이핑).
  `saveFiles`가 앱별 `package.json`을 추가로 저장하고, `/api/generate`의
  `runCommand` 응답이 `npx wdio run ...` 대신 `node <Base>TestById.js`를
  반환한다. `wdio.conf.js`는 레거시 산출물로만 계속 생성.
  **GUI 검증 완료**: 실제 `calc.exe`로 `/api/generate`(exePath 포함) →
  `node CalcRealTestById.js` 단독 실행 — Appium 자체 기동, 실제 앱 세션 생성,
  "Five" 클릭, `[PASS] all steps completed`로 정상 종료 확인. 종료 후
  `netstat`/`tasklist`로 Appium/Calculator 프로세스 잔존 없음 확인(정상 정리).
- **검증 중 발견한 독립 버그 2건(둘 다 수정 완료)**:
  1) 설치된 Appium 3.5.2가 `--allow-insecure winappdriver`(콜론 없는 bare
     이름)를 `Error: The full feature name must include ... '*' wildcard ...`로
     거부 — `*:winappdriver` 형식이 필요함을 실측 확인. 이 버그는 기존
     `wdio.conf.js`의 `appium.args`에도 동일하게 있던 잠재 버그였음(이번에
     실제로 Appium을 직접 스폰해보기 전까진 아무도 마주치지 않았을 뿐) —
     두 위치 모두 수정.
  2) `run()`에서 `ensureAppium()`/`_createSession()` 호출이 `try/finally`
     바깥에 있어, 세션 생성 실패 시(예: 빈 capability) 스폰한 Appium
     프로세스가 정리되지 않고 남을 수 있었음(Windows는 부모 종료 시 자식
     프로세스를 자동으로 안 죽임) — 시작 단계 전체를 `try` 안으로 이동.
- **③ 멀티윈도우 코드 구조화**: `rootHwndHex`/`newWindowSegment` 기반으로
  세그먼트를 사전 계산해, 생성 코드 상단에 `// Windows in this recording:
  [W1].../[W2]...` 범례를 추가하고, 페이지 오브젝트 클래스와 테스트 본문
  양쪽에 `[Wn]` 섹션 배너를 삽입(순수 코멘트, 런타임 비용 0 — 기존
  `_switchWindow()` 호출 최적화와는 독립적으로 유지). "새 화면의 요소가
  코드에서 그 화면 아래 묶여 보여야 한다"는 요구를 충족.
  **검증 수준: 코드/회귀-테스트만** — `MockMulti`(합성 3-세그먼트 시나리오)
  생성물에서 범례+배너가 정확히 렌더링됨을 확인했으나, 실제 FileZilla
  재녹화로 재확인하지는 않음(아래 Next actions).
- **회귀 게이트 163 → 177**: standalone 래퍼(`describe(`/`browser.` 부재,
  `run()`/`ensureAppium` 존재, `process.exitCode` 기반 pass/fail) 체크,
  `package.json`/`runCommand` 체크, `[W1]/[W2]/[W3]` 범례+배너 체크 등 약
  15건 추가. `python agent/mock_events.py` **177/177 통과**.
- **미검증(중요)**: 이번 세션의 실제 GUI 검증은 Calculator(simple 모드,
  단일 창)만 수행 — FileZilla 같은 session 모드(멀티윈도우) 앱을
  `node <Base>TestById.js`로 실제 재생해 (a) 새 standalone 실행 경로 자체가
  session 모드에서도 동작하는지, (b) `[Wn]` 배너가 실제 캡처에서도 올바르게
  나오는지는 아직 실측 안 됨.

**2026-07-17 (2차): FileZilla session 모드 실전 재생 디버깅 — 버그 7건 발견,
5건 수정 완료(2건 실전 반복 검증), 2건 미확정. 상세 → `daily/2026-07-17.md`
(사용자 요청으로 발견 순서·원인·수정·검증 상태 전부 기록됨)**

바로 위 "미검증" 항목(FileZilla session 모드 standalone 실전 재생)에 착수해
`systematic-debugging`으로 진행. 발견 순서대로:
- **버그 A(수정 완료)**: `mergeExpandCollapseClicks()`가 콤보박스 3연클릭
  (열릴 때까지 재클릭)을 트리거 자기 자신과 잘못 병합
  (`expandCollapse 배경색(B): -> 배경색(B):`). 연속 재클릭을 먼저 몰아서
  스킵하도록 수정, TDD로 회귀 테스트 추가(177→182).
- **사고**: 이 검증 과정에서 `mock_events.py` 실행이 **실제 사용자 FileZilla
  캡처 파일을 덮어씀** — `POST /api/events`가 전역 `sessionBackupFile`에
  매번 전체를 다시 쓰는데, 이 변수가 실제 세션과 테스트 세션 사이에
  공유돼서 발생. 사용자가 재녹화로 복구. **구조적 원인은 안 고침** — 다음
  세션 과제.
- **버그 B(수정 완료, 실전 검증)**: 창-경계 판정(`[Wn]` 배너 + 런타임
  `_switchWindow()`)이 `rootHwndHex`만 보는데, agent.py가 `windowTitle`보다
  `rootHwndHex`를 몇 이벤트 늦게 채워서(PID self-heal vs watcher 등록 시차)
  다이얼로그 진입 초반 이벤트들의 경계를 놓침 — `switch to window:` 스텝이
  한 번도 안 생김. `windowTitle` 변화도 신호로 추가, 회귀 182→189.
- **버그 C(수정 완료, 진단 스크립트+실전 반복 검증)**: 독립 진단
  (`server/_diag_rootscan.mjs`)으로 Appium Root 세션 REST 조회가 **쿼리
  내용/매치 여부와 무관하게 매번 15~20초 고정 비용**임을 실측 확정(빈 결과
  0.5초 미만이 아니라 15.6초). owned 다이얼로그(WAD가 scoped session 거부)는
  hwnd를 이미 알고 있으므로 Root-scan REST 폴백 대신 기존 COM 경로
  (`osScopedInvoke.py`, cross-window 클릭에 이미 쓰던 것)로 즉시 라우팅 —
  타이핑용 `osScopedType()`/`--text-b64` 신설. 실전에서 20초 타임아웃 완전히
  사라짐 반복 확인.
- **버그 C-1(수정 완료)**: 위 수정 자체의 버그 — `_scopedFailHwnds`
  블랙리스트(원래 "세션 생성 재시도 방지"용)가 owned 재감지 자체를 막아서,
  캐시 재조회 시 다시 느린 경로로 떨어짐. owned 여부 확인을 블랙리스트와
  무관하게 항상 먼저 하도록 재구성.
- **버그 C-2(수정 완료)**: 셀렉터 파서(`_parseSelectorToTarget`)가
  `//*[...]`(와일드카드)만 매칭하고 `//TreeItem[...]`(실제 ControlType
  태그) 형태를 못 잡아 일부 클릭만 여전히 느린 경로로 폴백. 정규식을
  태그 형태 전부 매칭하도록 확장.
- **버그 D(수정 완료, 실전 반복 검증)**: `launchApp()`의 `spawn()`이 `cwd`를
  안 정해줘서, `node <file>.js`를 실행한 위치(예:
  `generated-wdio/FileZilla`)를 FileZilla가 그대로 물려받아 로컬 패널이
  엉뚱한 폴더에서 열림 — 녹화가 가정한 "컴퓨터" 루트의 `..`/`C:`가 아예
  안 보여 STEP1부터 실패. FileZilla 자체 설정엔 이 경로가 안 남아있어서
  앱이 기억하는 상태가 아니라 순수 CWD 상속 문제로 확정(7-Zip류 "마지막
  폴더 기억" 문제와는 별개). `cwd: homedir()`로 고정.
- **버그 E(부분 수정, 미확정)**: COM 경로가 REST의 폴링(1초 간격 최대 8초)과
  달리 단발 시도라 "새 사이트(N)" 클릭 직후 렌더링되는 인라인 이름변경
  상자를 놓칠 수 있음(`_step()`의 ESC 기반 복구는 이름변경 상자에서 ESC가
  변경을 취소시켜 오히려 재시도를 방해). 최대 4회(즉시+300ms×3) 재시도
  루프를 `osScopedInvoke.py`에 추가했으나, 실전 재생에서 **동일 코드로 돌린
  두 번의 실행 결과가 달랐음**(한 번은 STEP4까지 전부 성공, 바로 다음 실행은
  STEP4부터 실패해 연쇄 실패). Site Manager 누적 데이터 때문인가 의심했으나
  `sitemanager.xml` 자체가 없어(전부 저장 전 취소돼 안 남음) 기각 — 근본
  원인 미확정, 반복 재현 관찰 필요.
- **스코프 밖(오늘 미착수)**: 숫자 automationId 재사용(`5999`가 호스트(H)/
  사용자(U) 필드 둘 다에, `5101`이 서로 다른 취소 버튼에 재사용 — PuTTY
  2026-07-13 5차와 같은 패턴), STEP7 "항목 선택(S):" SysTreeView32 힌트
  텍스트 문제.
- **회귀 게이트 177 → 189 → 197** (버그 A/B로 189, 이후 정리 커밋으로 197).
  `python agent/mock_events.py` **197/197 통과**.

**Next actions (2026-07-13 이후):**
1. **PuTTY GUI 재검증 (2026-07-14 RC-A/B/C 수정 반영)**: server.js 재시작 후
   PuTTY 재-Generate(재녹화 불필요 — 최신 캡처 restore 후 Generate) →
   실행 명령이 2026-07-17부터 `node PuTTYTestById.js`로 바뀜(`npx wdio run`
   아님 — §1 Commands 참고) — 이걸로 **2 passed 상당** 확인. 체크포인트: ByClass가
   타이틀바 X로 종료 안 함, "Window" expandCollapse가 `[osExpandCollapse] ...
   under main window subtree`(COM)로 성공, 병합 후 UTF-8 도달, 양쪽 spec에
   `esc-recovery-closed-app` 없음, 서버 로그 `[cross-window-merge] merged
   click+scroll+click ... (dropped intervening scroll)`. UTF-8 재오픈
   (클릭→스크롤→클릭)이 이번 병합으로 처리되는지가 핵심. FileZilla 메뉴는
   별도 백로그.
2. Calculator/Notepad `node <Base>TestById.js` 재실행(2026-07-17부터 `npx wdio run`
   대신 이 명령 — Calculator는 실제 `calc.exe`로 이미 검증됨, §4 2026-07-17) —
   07-13 숫자-id 완화 + Name-fallback 태그 추가가 기존
   GENERIC_AUTOMATION_IDS/XAML 경로에 회귀 없는지 Notepad로 확인.
3. FileZilla 호스트 필드 타이핑 재검증: 영문 텍스트 의도적 입력 후 `[keydrop]`
   로그 유무로 "입력 없이 Enter만" vs "게이트 문제" vs "IME 문제" 판정
   (daily/2026-07-13.md "세 번째 트랙" 참고).
4. **anchor/scrollTarget 실캡처 확인**: id 없는 요소 클릭 시 `[inspect] anchor XPath ...`
   로그, 스크롤 시 `[scroll] container ...` 로그가 실캡처에서 뜨는지 확인
   (07-12 재녹화에서는 해당 케이스 미발생 — light-dismiss 경로만 검증됨).
5. **7-Zip 재녹화 검증 (2026-07-15 이어서)**: agent.py 재시작(관리자) → 동일 흐름
   재녹화(컴퓨터→C: 더블클릭→$Recycle.Bin 더블클릭→파일 더블클릭, 더블클릭 직후
   같은 자리 재클릭 주의) → Generate → `node SevenzipTestById.js`(2026-07-17부터
   `npx wdio run` 아님) **2 passed 상당** 확인 + `[inspect] ... climbed`류 신호로 신규 row-climb 조건
   발동 확인. 코드 수정 자체는 수동 패치 캡처로 이미 2/2 검증됨(§4 2026-07-15).
   그 뒤 다른 native 앱(WinRAR 등)으로 확장.
6. Check `INPUT_CONTROL_TYPES` if typing in new apps is silently dropped
   (current set: `{"Edit", "Document", "ComboBox"}`).
7. session 모드 doubleClick = 요소 클릭 2회 근사 — 탐색기류 행에서 rename 오발 여부
   GUI로 확인 필요(문제 시 WAD legacy moveto/doubleclick 엔드포인트 검토 — 단 좌표
   금지 원칙과의 정합 검토 선행). SESSION_HEADER의 ExpandCollapsePattern 지원도
   이번 스코프 밖이라 함께 검토 필요.

8. ~~멀티윈도우 세그먼팅 GUI 검증~~ — **2026-07-16 완료**: FileZilla 실제 재생으로
   ①②(`switch to window:` 스텝 표시, 화면2 클릭/메뉴선택 반영) 확인 완료. ③(같은
   타이틀로 메인 창 복귀 후에도 계속 진행)은 FileZilla 케이스에선 미발생이라 아직
   실전 확인 안 됨 — 7-Zip류 타이틀 충돌 케이스로 재확인 필요. "2차 화면 요소
   캡처 누락"(리뷰 피드백 3번)도 이번 FileZilla 재생에선 재현되지 않아 원인
   미확정 상태 유지 — `_watch_windows()`는 여전히 추측성으로 고치지 않는다.
9. ~~FileZilla 실전 재확인 — standalone 실행 + `[Wn]` 배너~~ — **2026-07-17
   (2차) 완료**: session 모드 standalone 실행 자체는 정상 동작 확인(`[appium]
   starting Appium...` → `_rootSid` 세션 생성 → `switch to window:` 스텝
   발동 → 종료 후 프로세스 정리 확인). 그 과정에서 버그 7건 발견(5건 수정,
   2건 미확정) — 상세 → 위 "2026-07-17 (2차)" 항목 + `daily/2026-07-17.md`.
   PuTTY/7-Zip은 여전히 `node <Base>TestById.js`로 재확인 안 됨(항목 1, 5).
10. **버그 E 재현 관찰 (2026-07-17 (2차)에서 이어서, 최우선)**: FileZilla
    "새 사이트(N)" 클릭 직후 인라인 이름변경 상자 타이핑이 같은 코드로도
    실행마다 성공/실패가 갈림(COM 경로 재시도 루프 0.9초로도 가끔 부족).
    여러 번 연속 재생해 패턴 기록 — 재시도 시간을 늘리는 게 도움되는지,
    아니면 다른 근본 원인(이름변경 상자가 UIA에 다른 방식으로 노출되는
    케이스)을 찾아야 하는지 판단.
11. **숫자 automationId 재사용 (FileZilla Site Manager, 2026-07-17 (2차)
    발견)**: `5999`가 호스트(H)/사용자(U) 필드 둘 다에, `5101`이 서로 다른
    다이얼로그의 취소 버튼에 재사용됨 — PuTTY 2026-07-13 (5차)와 같은 패턴
    (automationId 단독 조건이 먼저 매칭돼버림). 같은 해법(automationId+
    name을 AND로 묶은 조건을 먼저 시도) 적용 가능한지 검토.
12. **`sessionBackupFile` 구조적 위험 (2026-07-17 (2차)에서 사고로 발견)**:
    `POST /api/events`가 전역 `sessionBackupFile`에 매번 전체를 다시 쓰는데,
    이 변수가 실제 사용자 녹화 세션과 `mock_events.py` 테스트 세션 사이에
    공유될 수 있어 **실제 캡처 파일이 테스트 데이터로 덮어써지는 사고가
    실제로 발생**했음(사용자가 재녹화로 복구). 재발 방지 필요 여부 결정 —
    예: 테스트 전용 엔드포인트 분리, 또는 "테스트 모드" 플래그로 실제 백업
    경로 덮어쓰기 차단.

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
