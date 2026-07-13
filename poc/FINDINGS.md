# PoC 결과 — 좌표 없는 UIA 자동화 기술 검증 (2026-07-10 실행, 07-12 ③ E2E 완주)

3건의 PoC를 이 머신에서 직접 실행해 검증. 스크립트는 이 폴더에 보존.
모두 **admin 권한 없이, WinAppDriver/Appium REST를 직접 호출**하는 standalone
PowerShell/UIA COM 스크립트 — `agent.py`/캡처 파이프라인과 무관하게 재현 가능.

## PoC ① — clean XPath 캡처 + 좌표 없는 클릭 (`dumpUia.ps1`)

```
powershell -File dumpUia.ps1 -ProcessName mmc -MaxDepth 6
```

**결과: 가능, 이미 부분 실증됨.**
- `services.msc`(비관리자로 열림) 대상 실행 → 실제 native Win32 컨트롤이 숫자
  AutomationId를 노출함을 라이브 확인: `SysListView32` id=`12786`,
  `SysTreeView32` id=`12785`, 다수 툴바 버튼. `//*[@AutomationId="12786"]` 같은
  XPath가 바로 사용 가능.
- 이 저장소의 `generated-wdio/Calculator/CalculatorTestById.js`가 이미 이
  경로(`el.click()` on AutomationId-XPath)로 **GUI 통과 검증됨**(세션 13) —
  신규 구현이 아니라 기존 검증된 메커니즘의 재확인.
- **중요 caveat (regedit로 발견)**: `regedit.exe`는 매니페스트상 관리자 권한으로
  실행됨. 비관리자 스크립트로 최상위 창의 ClassName/AutomationId는 읽히지만
  (`SysTreeView32` id=1 확인), **자식 노드(TreeItem)는 UIPI(무결성 수준 차단)에
  막혀 전혀 안 보임** — `Stop-Process`도 "Access is denied"로 실패해 프로세스가
  관리자 권한임을 재확인. 이는 CLAUDE.md §5 "Agent not Admin" 트랩과 정확히
  일치 — **도구 결함이 아니라 대상 앱의 권한 요구사항 문제**. 스테이크홀더가
  이미 native-only + 비관리자 앱(FileZilla/PuTTY/7-Zip) 목록으로 스코프를
  좁혀준 것과 정합적.

## PoC ② — 픽셀 계산 없는 스크롤 (`uiaScroll.ps1`, `uiaScrollExplorer.ps1`, `verifyScroll2.ps1`)

```
powershell -File uiaScrollExplorer.ps1 -TitleSubstring System32
```

**결과: 가능, 실측으로 검증 완료.**
- Explorer 창(`UIItemsView`, 최신 DirectUI ListView)에서 `ScrollPattern.Scroll()`
  호출 → `VerticalScrollPercent` 0 → 0.374로 실측 변화 확인. **`SetCursorPos` 등
  픽셀 API 호출 0회.**
- **Caveat**: 레거시 컨트롤(`services.msc`의 `SysListView32`, `charmap`의
  `CharGridWClass`)은 UIA `ScrollPattern`을 노출하지 **않음** — MSAA 시대
  컨트롤이 흔히 그러함. 이 경우를 위해 hwnd-scoped `WM_MOUSEWHEEL`
  (`SendMessageW`) 폴백을 구현·테스트 — 좌표/`SetCursorPos` 없이 특정 hwnd에만
  전달되므로 요구사항(외부 PowerShell의 화면 좌표 물리 신호 주입 금지)을 준수.
  **단, 이 폴백 테스트 중 `charmap.exe`가 원인 불명으로 종료됨** — `SendMessageW`
  (동기 전송)이 일부 레거시 창 프로시저와 충돌할 수 있음을 시사. **권장:
  프로덕션 폴백은 `PostMessageW`(비동기)로 전환**할 것 — 별도로 `WM_CLOSE`를
  `PostMessage`로 보낸 테스트는 문제없이 동작함.
- **결론**: ScrollPattern을 1차로 시도하고, 미지원 시에만 hwnd-scoped
  PostMessage 휠 폴백 — 이 2단 전략이 "픽셀 좌표 없는 스크롤" 요구사항을
  만족시키는 실증된 경로.

## PoC ③ — HWND 세그먼팅 + 다중창 (`poc3_hwndSegment.ps1`)

**결과: 조건부 가능 — 기존 아키텍처(신규 scoped 세션)가 정답, "단일 세션 내
switchToWindow" 대안은 WinAppDriver에서 지원 안 됨(실측 확인).**
- WinAppDriver REST로 `mspaint.exe`(비관리자, UWP 패키지)의 hwnd에 scoped
  세션 생성 → `GET /session/{id}/window_handles` 실행.
- **실측 결과**: 데스크톱에 VSCode·Chrome·Notepad·regedit 등 다른 최상위 창이
  10개 이상 열려 있는 상태에서도 `window_handles`는 **세션이 생성된 hwnd
  단 1개만** 반환(`["0x000A0C98"]`). 즉 WinAppDriver의 `window_handles`는
  브라우저의 "탭 목록"처럼 데스크톱 전역이 아니라 **세션 생성 시점의 그 창에
  고정**됨 — 새 창(다이얼로그)이 열려도 그 세션의 `window_handles`로는 안 잡힘.
- 이는 **현재 프로젝트가 이미 쓰고 있는 아키텍처(신규 hwnd마다 새 scoped 세션
  생성, 또는 owned 창은 Root 세션으로 폴백 — CLAUDE.md 07-09 항목)가 우회책이
  아니라 WinAppDriver의 실제 제약에 따른 필연적 설계였음을 확인**시켜 줌.
  "한 세션 안에서 switchToWindow로 더 빠르게" 라는 최적화 가설은 기각.
- **캡처 측 HWND 세그먼팅 자체**(새 hwnd 감지 → 이벤트 그룹 분리)는 이미
  `agent.py`/`server.js`에 구현·GUI 검증됨(VSCode, 07-08) — 이번 PoC는 재생
  측 메커니즘의 한계를 추가로 규명한 것.
- 다이얼로그를 실제로 열어 hwnd 전환까지 end-to-end로 트리거하는 부분은 Paint의
  WinUI3 메뉴가 `InvokePattern`으로 안 열려(`ExpandCollapsePattern` 등 별도
  처리 필요) 이번 세션에서 완주하지 못함 — **→ 2026-07-12 완주 완료, 아래 섹션.**

## PoC ③ E2E 완주 (2026-07-12 추가, `poc3_dialog_e2e.py`)

```
python poc/poc3_dialog_e2e.py     # admin 불필요, comtypes만 필요
```

**결과: 성공 — 보조 창 열기 → 고유 HWND 캡처 → 그 창 컨텍스트 격리 클릭까지
전 구간 통과.** 실측 출력:

```
[1] explorer hwnd=0x1209fc class=CabinetWClass title='poc - 파일 탐색기'
[2] found ListItem 'FINDINGS' — SetFocus + SelectionItemPattern.Select() (no coords)
[3] opening Properties via Alt+Enter (keyboard — no coords)
[4] NEW dialog hwnd=0x12d0b0a class=#32770 title='FINDINGS 속성'
[5a] '취소' scoped to the EXPLORER window subtree: not found — isolation holds
[5b] '취소' resolved INSIDE the captured dialog subtree — invoking via UIA
     InvokePattern (element click, no coords)
[5b] dialog closed after scoped click: YES
```

- 대상: Explorer(항상 비승격, 현대적 UIA — PoC ②와 동일 앱). 파일 항목을
  **UIA `SelectionItemPattern`으로 선택**(요소 기반), Alt+Enter로 속성
  다이얼로그 오픈, `EnumWindows` 차분으로 **새 `#32770` hwnd 캡처**, '취소'
  버튼을 **캡처한 hwnd의 UIA 서브트리에만 스코프**해 발견·Invoke(요소 클릭).
  같은 쿼리를 메인 창 서브트리에 스코프하면 미발견 — 격리 실증.
  `SetCursorPos`/`mouse_event`/픽셀 좌표 0회.
- 스택: **COM IUIAutomation(comtypes) — 프로덕션 `agent.py`와 동일**. .NET
  `System.Windows.Automation`(managed UIA)로 먼저 시도했으나 MSAA 프록시가
  약해 레거시 컨트롤 내부(리스트 행, 툴바 버튼)를 전혀 노출하지 못함을 실측
  — managed 버전 스크립트는 폐기, COM이 정답(제품 스택 선택의 타당성 재확인).
- **1차 대상이었던 `services.msc`(MMC)를 배제한 사유 (실측 2건)**:
  ① 이 머신에서 `highestAvailable` 매니페스트로 **승격 실행**됨 → 비승격
  스크립트의 UIA 자식 조회·키 주입이 UIPI에 전부 차단(PoC ①의 regedit 트랩과
  동일 — `Stop-Process`도 Access denied). ② 승격을 떠나 가상(LVS_OWNERDATA)
  `SysListView32`는 UIA 행 아이템 자체를 노출하지 않음(.NET/COM 공통).
  → 레거시 MMC류는 자동화 대상으로 부적합; 스테이크홀더 권장 목록
  (FileZilla/PuTTY/7-Zip — 모두 비승격 + 표준 컨트롤)과 정합적.

## 부작용 / 안전 관련 메모 (스테이크홀더 무관, 내부 기록용)

- `regedit.exe`가 관리자 권한으로 남아 있음(PID는 세션마다 다름) — 비관리자
  세션에서 종료 불가(Access denied). 사용자가 직접 닫아야 함.
- Notepad(Win11, 단일 인스턴스)가 사용자의 **실제 미저장 탭**(다른 프로젝트
  파일 등)을 물고 있는 것을 발견 — 이번 PoC에서는 읽기 전용 UIA 조회만
  수행하고 그 프로세스에 어떤 입력도 보내지 않음(안전 확인). 향후 Notepad를
  자동화 대상으로 쓸 경우, 탭 병합 때문에 "내 탭"과 "사용자 탭"을 구분하기
  어려우므로 **피하는 것을 권장**.
