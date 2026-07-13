# PoC 제출 — 좌표 없는 UIA 데스크톱 자동화 기술 검증 3건

제출일: 2026-07-12 · 상세 실측 기록: [`FINDINGS.md`](FINDINGS.md)

요구된 3건 모두 이 저장소의 독립 실행 스크립트로 재현 가능하며, admin 권한
없이 동작합니다. 전 과정에서 `SetCursorPos`/`mouse_event`/픽셀 좌표 사용 0회.

---

## ① .exe 실행 → 요소 클릭 → AutomationId/ClassName 기반 clean XPath 캡처

**결론: 가능 — 스크립트 실측 + 제품 파이프라인 GUI 통과로 이중 증명.**

| 증거 | 내용 |
|---|---|
| `dumpUia.ps1` | `powershell -File poc/dumpUia.ps1 -ProcessName <이름>` — 실행 중인 앱의 UIA 트리를 덤프해 AutomationId/ClassName 노출을 확인. services.msc에서 native Win32 컨트롤의 숫자 AutomationId(`SysListView32` id=12786 등) 라이브 확인 |
| 제품 E2E (2026-07-12) | 녹화→생성→재생 전체 파이프라인이 실제 GUI에서 통과: **Calculator 2 passed, Notepad 2 passed** (`npx wdio run`). 캡처된 clean XPath 예시 — `//Button[@ClassName="Button" and @Name="새 탭 추가"]`, `//MenuItem[@ClassName="Microsoft.UI.Xaml.Controls.MenuBarItem" and @Name="보기"]`, `~num7Button`(AutomationId). 수동 보정 없이 그대로 재생에 사용됨 |

**Caveat**: 관리자 매니페스트 앱(regedit 등)은 비승격 프로세스에서 자식 노드가
UIPI에 차단됨 — 도구 결함이 아닌 대상 앱 속성이며, 캡처 agent를 관리자로
실행하는 현 운영 방식으로 해소됨.

---

## ② UIA/InputSimulator로 픽셀 계산 없이 native 요소 스크롤

**결론: 가능 — ScrollPattern 1차 + hwnd-scoped PostMessage 휠 폴백의 2단
전략으로 실측 완료.**

| 증거 | 내용 |
|---|---|
| `uiaScrollExplorer.ps1` | `powershell -File poc/uiaScrollExplorer.ps1 -TitleSubstring <창제목>` — Explorer 리스트에서 `ScrollPattern.Scroll()` 호출, `VerticalScrollPercent` **0 → 0.374** 실측 변화. 픽셀 API 0회 |
| 제품 반영 (osScroll.ps1) | 위 전략이 이미 프로덕션 재생 헬퍼로 구현됨 — 캡처 시점에 기록한 스크롤 컨테이너를 UIA로 재탐색 후 ScrollPattern 호출, 미지원 레거시 컨트롤만 `PostMessageW(WM_MOUSEWHEEL)` 폴백. Explorer 라이브 실측 통과(0→0.17) |

**Caveat(실측으로 발견)**: 레거시 컨트롤(MMC ListView, CharGrid)은 ScrollPattern
미노출 → 폴백 필요. 폴백은 반드시 **PostMessage(비동기)** — 동기 `SendMessageW`는
테스트 중 charmap.exe를 크래시시킴.

---

## ③ 보조 창/팝업 열기 → 고유 HWND 캡처 → 그 창 컨텍스트 안에서만 클릭 격리

**결론: 가능 — E2E 전 구간 실측 통과 (2026-07-12).**

| 증거 | 내용 |
|---|---|
| `poc3_dialog_e2e.py` | `python poc/poc3_dialog_e2e.py` — ① 탐색기에서 파일 항목을 UIA `SelectionItemPattern`으로 선택(요소 기반), ② 속성 다이얼로그 오픈, ③ **새 최상위 `#32770` HWND를 EnumWindows 차분으로 캡처**, ④ '취소' 버튼 쿼리가 **메인 창 서브트리에선 미발견, 캡처한 HWND 서브트리에서만 발견** → UIA Invoke로 클릭 → 다이얼로그 닫힘 확인 |
| `poc3_hwndSegment.ps1` | WinAppDriver 세션의 `window_handles`가 세션 생성 시점 hwnd 1개에 고정됨을 실측 — "새 창마다 새 scoped 세션" 방식(제품이 이미 채택)이 우회책이 아니라 WAD 제약상 필연적 설계임을 증명 |
| 제품 반영 | 캡처 측 HWND 세그먼팅(새 hwnd 감지 → 이벤트 격리)과 재생 측 창별 scoped 세션은 이미 `agent.py`/`server.js`에 구현·GUI 검증됨(다중창 시나리오, 2026-07-08) |

실측 출력(발췌):

```
[2] found ListItem 'FINDINGS' — SetFocus + SelectionItemPattern.Select() (no coords)
[4] NEW dialog hwnd=0x12d0b0a class=#32770 title='FINDINGS 속성'
[5a] '취소' scoped to the EXPLORER window subtree: not found — isolation holds
[5b] '취소' resolved INSIDE the captured dialog subtree — invoking via UIA
     InvokePattern (element click, no coords)
[5b] dialog closed after scoped click: YES
```

**Caveat(실측으로 발견, 대상 앱 선정에 반영)**: MMC(services.msc)류 레거시
앱은 ① 승격 실행(UIPI 차단), ② 가상 리스트뷰가 UIA 아이템 미노출로 자동화
부적합 — 권장 테스트 앱 목록(FileZilla/PuTTY/7-Zip: 비승격 + 표준 컨트롤)이
기술적으로도 올바른 선정임을 확인.

---

## 종합

| 요구 | 판정 | 핵심 증거 |
|---|---|---|
| ① clean XPath 캡처 | **가능** | 제품 GUI 통과(Calculator/Notepad) + dumpUia.ps1 |
| ② 픽셀 없는 스크롤 | **가능** | ScrollPattern 실측 0→0.374 + 프로덕션 osScroll.ps1 |
| ③ HWND 격리 | **가능** | poc3_dialog_e2e.py E2E 통과 + WAD 제약 실증 |

세 기술 모두 독립 스크립트로 증명됐고, ①②③ 전부 이미 본 제품 파이프라인에
반영되어 실제 GUI 테스트(Calculator/Notepad 2 passed)까지 통과한 상태입니다.
