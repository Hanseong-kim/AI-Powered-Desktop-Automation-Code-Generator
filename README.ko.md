# AI-Powered Desktop Automation Code Generator

*[English](README.md) | 한국어*

> 이 문서는 [`README.md`](README.md)의 한글 번역본입니다. 코드, 파일 경로,
> 함수/변수명, CLI 플래그 등은 원문 그대로 유지합니다.

Windows 데스크톱 앱에서 사용자의 상호작용(클릭, 타이핑, 더블클릭, 스크롤)을
녹화해서, 그 세션을 그대로 재생하는 실행 가능한 **WebdriverIO(JavaScript)**
테스트 코드를 생성합니다 — 모든 요소는 **UI Automation 셀렉터
(AutomationId / ClassName / Name XPath)로만 지목**하며, **화면 좌표는
절대 쓰지 않습니다.**

- **범용**: `.exe`(또는 UWP AUMID) 아무거나 바로 대상으로 삼을 수 있습니다 —
  앱별 통합 작업이 필요 없습니다.
- **XPath 전용 재생**: 좌표는 어디서도 금지됩니다. 고유한 id/name이 없는
  요소는 앵커 기준 상대 XPath(`//*[@AutomationId="X"]/Button[3]`)로
  해결합니다. 쓸 수 있는 셀렉터가 없는 이벤트는 조용히 성능이 떨어지는 대신
  **명시적인 실패 스텝**으로 생성됩니다.
- **템플릿 기반 생성**: LLM 호출도, API 키도, 네트워크도 없습니다 — 녹화된
  이벤트 목록에서 코드를 직접 조립하며 1초도 안 걸립니다.
- **자가 복구형 재생**: 모든 스텝은 Fail-and-Recover 루틴으로 감싸져 있어서,
  예상치 못한 팝업("파일이 이미 존재합니다" 등)을 닫고 한 번 재시도한 뒤에야
  솔직하게 실패로 처리합니다.

## 검증된 대상

`node <AppName>TestById.js`로 GUI 확인 완료 — **WebdriverIO 테스트 러너도,
`wdio.conf.js`도, `describe`/`it`/`browser`도 필요 없는** 독립 실행 스크립트
입니다(왜 이게 중요한지는 아래 §3 참고):

| 앱 | 종류 | 비고 |
|---|---|---|
| Calculator | UWP | simple 모드 |
| Notepad | UWP | simple 모드 |
| PuTTY | 네이티브 Win32 다이얼로그 | 카테고리 트리 탐색, ComboBox 드롭다운(같은 창/별도 창 팝업 둘 다), 트리 +/- 토글, 프록시 라디오 버튼 |
| FileZilla | 네이티브 Win32, 멀티윈도우 | 폴더 트리 탐색, ExpandCollapsePattern을 통한 메뉴바 탐색, Site Manager 다이얼로그(별도 HWND 세션), 로컬 파일목록 더블클릭 탐색(`..`으로 상위 폴더 이동 포함) 및 빠른 폴더 이동 — 100개 이상 스텝짜리 녹화로 GUI 검증 완료(2026-08-10) |
| 7-Zip | 네이티브 Win32 | 파일 목록 탐색, 더블클릭으로 폴더 진입 |
| HeidiSQL | Delphi/VCL, 멀티윈도우 | owner-drawn ComboBoxEx 항목을 위치로 선택(네트워크 유형 콤보), 세션 관리자 ↔ 환경설정 창 간 흐름. 세션 목록 트리(`TVirtualStringTree`)는 UIA 자식을 하나도 노출하지 않아 자동화가 불가능함 — **Known Limitations** 참고. "더 보기" 오버플로 메뉴 항목은 위치 기반으로 캡처되지만 재생 쪽에서 아직 선택하지 못함(보류 중, 아래 참고) |
| TeamViewer | WebView2(Chromium), 단일 창 | 처음으로 검증된 Electron/Chromium 계열 대상 — ID/비밀번호 복사 버튼, 세션 코드 입력, "Join session", 설정 체크박스 2개("Windows와 함께 TeamViewer 시작" / "이 장치에 Easy Access 권한 부여" — 체크박스 글리프 자체가 아니라 **텍스트 라벨**을 클릭해야 함, 글리프는 이름 없는 wrapper 안에 있음), 그리고 네이티브 "빠른 연결 허용" 다이얼로그(이메일/비밀번호/취소)까지 전부 처음부터 끝까지 재생됨(`[PASS] all steps completed`). **agent, Express 브릿지가 띄우는 프로세스들, 생성된 테스트 자체가 전부 관리자(Administrator) 터미널에서 실행돼야 합니다** — TeamViewer가 상승된 권한으로 실행되고, Windows의 UIPI가 비상승 자동화 클라이언트가 창 껍데기 너머를 보는 걸 막기 때문입니다(아래 **WebView2 / Electron apps** 참고). 비상승 터미널에서 생성된 테스트를 실행하는 게 모든 스텝이 한꺼번에 실패하는 가장 흔한 원인입니다. |

UI에 있는 다른 프리셋(Paint, 레지스트리 편집기, IDM, VSCode, GitHub Desktop,
Free Download Manager, Claude Desktop)은 연결은 돼 있지만 아직 GUI로
end-to-end 검증되진 않았습니다. VSCode/GitHub Desktop/Claude Desktop은
TeamViewer와 같은 WebView2/Electron 계열이라 같은 방식으로 동작할 것으로
예상되지만, **실제로 검증된 건 TeamViewer뿐**입니다 — 다른 Electron 호스트가
"그냥 될 것"이라고 가정하기 전에 아래 **WebView2 / Electron apps**를
참고하세요.

프리셋 2개(`PowerShell ISE`, `Everything`)는 2026-08-05에 이 프로젝트가
아직 테스트하지 않은 프레임워크 커버리지(각각 WPF, WinForms — Win32/MFC
말고 나머지 두 대상 프레임워크)를 확인하려고 추가됐습니다.
`poc/probe_app_automatability.py`는 둘 다 Tier 1(지원됨)로 보고하지만,
아직 완전한 record→replay GUI 검증은 안 됐습니다.

## 아키텍처

```
React UI (3000) --HTTP--> Express (3002) --HTTP--> Python Agent (4444)
      ^                       |
      +---- SSE live feed ----+
```

세 프로세스가 협력합니다:

| 프로세스 | 역할 |
|---|---|
| **Python agent** (`agent/agent.py`) | 전역 마우스/키보드 훅(pynput) + Windows UI Automation(UIA/COM) 요소 검사. 훅은 원시 이벤트를 큐에 넣기만 하고 바로 반환합니다 — 모든 UIA 작업은 전용 워커 스레드에서 실행됩니다. |
| **Express 브릿지** (`server/server.js`) | 이벤트를 저장하고, 재생 아키텍처(단일 창 vs 멀티윈도우 세션 모드)를 결정하고, `/api/generate`를 통해 템플릿으로 테스트 코드를 생성합니다. |
| **React 대시보드** (`ui/`) | 실시간 이벤트 피드(SSE), 이벤트별 삭제, 앱 프리셋, Generate 버튼. |

## 사전 준비물

| 도구 | 버전 | 비고 |
|---|---|---|
| Python | 3.9+ | agent는 **반드시** 관리자 터미널에서 실행해야 합니다 |
| Node.js | 18+ | Express 브릿지, React UI, 생성된 테스트에 필요 |
| WinAppDriver | 1.2.1 | 설치 후 개발자 모드 활성화(설정 → 개인정보 및 보안 → 개발자용). 수동으로 띄울 필요 없음 — 생성된 각 스크립트 자체의 `ensureAppium()`이 Appium을 띄우고, 그게 WinAppDriver로 프록시합니다. |

> Java/Maven 없음, Playwright 없음, API 키 없음. 출력은 오직 WebdriverIO
> JavaScript뿐입니다.

---

## 1. 설치 및 실행

### 터미널 1 — Express 브릿지 (일반 터미널)

```powershell
cd server
npm install          # 최초 1회만
node server.js
# Listening on http://localhost:3002
```

### 터미널 2 — Python agent (관리자 PowerShell)

```powershell
cd agent
pip install -r requirements.txt   # 최초 1회만
python agent.py
# 반드시 "Administrator rights: YES"가 출력돼야 함
```

`NO`가 출력되면 터미널을 닫고 "관리자 권한으로 실행"으로 PowerShell을 다시
여세요 — 관리자 권한이 없으면 대부분의 앱에서 UIA 요소 속성
(`automationId`/`name`)이 **빈 값**으로 돌아오고, 생성된 테스트는 쓸모없는
스텝투성이가 됩니다.

> agent는 핫리로드가 없습니다 — `agent.py`를 수정했으면 재시작하세요.

### 터미널 3 — React UI (일반 터미널)

```powershell
cd ui
npm install          # 최초 1회만
npm run dev
# http://localhost:3000 열기
```

---

## 2. 세션 녹화하기

1. 프리셋 드롭다운(Calculator, Notepad, 레지스트리 편집기, …)에서
   **대상 앱을 선택**하거나, **Custom…**을 골라 직접 입력합니다:
   - **App Name** — PascalCase 출력 폴더명이 됩니다
     (예: `My App` → `generated-wdio/MyApp/`).
   - **Exe Path** — 실행 파일의 전체 경로
     (예: `C:\Program Files\7-Zip\7zFM.exe`).
     UWP 앱은 파일 경로 대신 `Package.Family.Name!App` 형태의 AUMID를
     씁니다 — agent가 `!`를 감지해서 `explorer shell:AppsFolder`로 자동
     실행합니다.
2. **Launch**를 클릭하면 대상 앱이 열리고 녹화가 시작됩니다. 첫 클릭 전에
   창이 완전히 렌더링될 때까지 기다리세요.
3. **앱과 상호작용하세요.** 지원하는 이벤트 범위: **Click, Type,
   Double-Click, Scroll**. (드래그와 우클릭은 진단용으로 캡처되지만
   scope-out 주석으로 생성될 뿐 재생되진 않습니다.)
   - **영문 입력만 지원** — IME/한중일 키 입력은 조용히 버려집니다.
   - 녹화 중 작업표시줄이나 다른 창을 클릭하지 마세요.
   - **메뉴를 너무 빠르게 연속으로 클릭하지 마세요**(1초 안에 메뉴 여러 개
     열기): 메뉴의 light-dismiss 오버레이가 요소 검사와 레이스를 일으킬 수
     있습니다. agent가 오버레이 아래 요소를 자동으로 다시 찾아내긴 하지만,
     여유 있는 페이스가 가장 깔끔한 캡처를 만듭니다.
4. **실시간 이벤트 피드**를 지켜보세요 — 각 행은 액션, 해결된 요소
   (automationId / name / className), 창 정보를 보여줍니다. 요소가 비어
   있는 행이 보이면 그 스텝은 명시적 FAIL 스텝으로 생성됩니다(좌표는 절대
   폴백으로 안 씀) — 그 상호작용을 다시 해보는 걸 고려하세요. 물리적인
   더블클릭은 항상 raw 이벤트 3개(click, click, doubleClick — 계산기에서
   "9999"를 입력하는 것 같은 진짜 반복 단일클릭을 보존하기 위함, `agent.py`
   참고)로 캡처되지만, **표시**는 두 클릭과 doubleClick이 더블클릭 시간창
   안에서 같은 대상을 가리키면 한 줄로 병합해서 보여줍니다(배지에 `×3`
   표시) — 그 행을 삭제하면 밑에 깔린 3개 이벤트가 전부 삭제됩니다.
5. 끝나면 **Stop**을 클릭합니다.
6. 행에 마우스를 올리고 `×`를 클릭해서 **잘못된 행**(오클릭, 작업표시줄
   클릭)을 **삭제**합니다.
7. **Generate Code**를 클릭합니다. 파일은 `generated-wdio/<AppName>/` 밑에
   자동으로 저장되고, 토스트로 경로를 확인해줍니다.

녹화는 `recorded-events/`(git-ignored) 아래 JSON으로도 백업되며,
`POST /api/events/restore`로 다시 녹화하지 않고도 복원해서 재생성할 수
있습니다.

### 생성된 결과물

```
generated-wdio/<AppName>/
├── <AppName>TestById.js       # 셀렉터가 AutomationId(~id / XPath)를 우선 — 완전히
│                              #   독립적(self-contained, 아래 참고)
├── <AppName>TestByClass.js    # 셀렉터가 ClassName+Name XPath를 우선 — 이것도 독립적
├── package.json                # 자체 의존성 없음(../node_modules를 resolve); 존재
│                                #   이유는 `npm run test:byid` 편의성뿐
└── appium.log                  # 첫 실행 시 생성됨
```

두 테스트 파일은 같은 녹화에 대한 서로 다른 로케이터 전략입니다 — id가
불안정한 앱에서 `ById`가 실패하면 `ByClass`를 시도해보세요.

**생성된 `.js` 각각은 완전히 독립적(self-contained)입니다.** 헬퍼 스크립트
9개(`osScroll.py`, `osScopedInvoke.py`, `osExpandCollapse.py`, `osType.ps1`,
`osActivate.ps1`, `osWindowRect.ps1`, `osMoveWindow.ps1`,
`osDismissPopup.ps1`, `osEscape.ps1`)의 소스를 문자열 상수로 그대로
포함하고, 필요한 시점에 프로세스별 임시 디렉터리로 자체 압축 해제합니다 —
`.js` 파일 하나만 다른 머신/폴더로 복사해도 그대로 실행됩니다(유일하게
남는 외부 의존성은 공유 `../node_modules` Appium 설치뿐 — Node.js 자체가
설치돼 있어야 하는 것과 같은 수준).

사람이 확인하기 편하도록, `saveFiles()`는 그 9개 헬퍼의 평문 사본도
`generated-wdio/_debug-helpers/<AppName>/`에 씁니다 — **생성된 `.js`는
거기서 절대 읽지 않습니다**(항상 자기 안에 내장된 사본을 씁니다) — 그러니
이 폴더는 순전히 내장된 스크립트가 뭘 하는지 읽고 디버깅하기 위한 용도이고,
지워도 안전합니다.

| 헬퍼 | 목적 |
|---|---|
| `osScroll.py` | UIA ScrollPattern 스크롤(PostMessage 휠 폴백) |
| `osScopedInvoke.py` | 별도의 최상위 창으로 열린 항목을 클릭(네이티브 ComboBox 드롭다운 / 메뉴 팝업) — WinAppDriver 세션이 그걸 아예 못 보므로 대신 COM UIA로 직접 처리합니다. **CheckBox 컨트롤**의 클릭 경로이기도 하고(클릭이 에러 안 났다는 것뿐 아니라 `ToggleState`가 실제로 바뀌었는지 검증 — 아래 참고), **`isWebContent` 요소 전체**의 클릭 경로이기도 합니다(WinAppDriver의 관리형 UIA 클라이언트는 세션 상태와 무관하게 WebView2/Chromium이 호스팅하는 컨트롤을 아예 못 봄) |
| `osExpandCollapse.py` | ExpandCollapsePattern 재생(ComboBox 드롭다운, 메뉴바 항목, 트리 +/- 토글) — 일반 click()/InvokePattern으로는 이것들이 안 열립니다 |
| `osType.ps1` | 고집 센 편집 컨트롤을 위한 OS 레벨 SendKeys 폴백 |
| `osActivate.ps1` | 앱 창을 전면으로 가져오기 |
| `osWindowRect.ps1` | 창 기하 정보 읽기(hwnd 우선) |
| `osMoveWindow.ps1` | 녹화된 창 위치/크기 복원 |
| `osDismissPopup.ps1` | Fail-and-Recover: 예상치 못한 다이얼로그 닫기 |
| `osEscape.ps1` | Fail-and-Recover: 막힌 입력 상태에서 ESC로 빠져나오기 |

> 이 헬퍼들 중 어느 것도 좌표 주입을 하지 않습니다: 키보드 입력, 창 관리,
> 팝업 복구, 패턴 기반(Expand/Scroll/Invoke) 요소 상호작용을 다룰 뿐이고 —
> 항상 셀렉터 기반이지 화면 픽셀 기반이 아닙니다. `.py` 헬퍼들은
> `agent/agent.py`와 같은 스택인 **COM IUIAutomation(comtypes)**을 씁니다.
> `osScroll`/`osScopedInvoke`/`osExpandCollapse`의 예전 `.ps1` 버전은 .NET
> 관리형 UIA(`System.Windows.Automation`)를 썼는데, 이건 레거시 Win32
> 컨트롤(리스트 행, 툴바 버튼, `SysTreeView32` 트리 항목)을 못 봐서 정확히
> 그 이유로 교체됐습니다. 생성된 파일이나 `_debug-helpers/` 사본을 직접
> 편집하지 마세요 — 둘 다 Generate할 때마다 덮어써집니다; 대신
> `server/server.js`의 템플릿을 고치세요.

---

## 3. 생성된 테스트 실행하기

생성된 `*TestById.js` / `*TestByClass.js` 각각은 **독립 실행형 Node.js
스크립트**입니다 — `describe`/`it`/`browser`/`expect`를 안 쓰고,
`wdio.conf.js`를 안 읽고, 테스트 러너가 필요 없습니다:

```powershell
cd generated-wdio
npm install          # 최초 1회만 — 생성된 모든 앱이 공유하는 의존성
cd <AppName>
node <AppName>TestById.js
# 예: cd Calculator && node CalculatorTestById.js
```

각 앱 폴더 자체의 `package.json`은 자체 의존성이 없습니다(Node가 디렉터리
트리를 거슬러 올라가며 `node_modules`를 resolve합니다) — 존재하는 이유는
`node --run test:byid` 편의성과 사람이 읽을 수 있는 설명 때문일 뿐, 앱
폴더 안에서 `npm install`을 따로 실행할 필요는 없습니다.

스크립트 자체가 Appium(`ensureAppium()`)을 띄우고, WinAppDriver 세션을
만들고, 녹화된 모든 스텝을 재생한 뒤, 실패 시 0이 아닌 `process.exitCode`로
종료합니다 — 별도 Appium 터미널도, `@wdio/appium-service`도, 맞춰줘야 할
WDIO 설정도 없습니다. 재생은 **눈에 보입니다**: 앱이 실행되고, 창이 녹화된
위치로 다시 옮겨지고, 각 스텝이 실제 UI를 순서대로 클릭/타이핑/스크롤하며
`[STEP] n:action label`로 출력됩니다.

> `wdio.conf.js`는 이제 아예 생성되지 않습니다(2026-07-21) — 이 실행
> 경로에서 애초에 읽힌 적이 없었기 때문에, `npx wdio run`은 단순히 지원이
> 안 되는 게 아니라 애초에 실행할 대상 자체가 없습니다.

대체 로케이터 전략도 같은 방식으로 실행합니다:

```powershell
node <AppName>TestByClass.js
```

### 테스트가 PASS/FAIL을 판정하는 방법

- 모든 스텝이 `[STEP] n:action label`로 로그됩니다.
- 주입 실패, 해결 불가능한 셀렉터, 창 관리 에러는 `_failures` 배열에
  쌓입니다; 끝에 이 배열이 비어있지 않으면 `[FAIL]`을 로그하고
  `process.exitCode = 1`을 설정합니다 — **조용히 깨진 스텝도 실행 전체를
  실패로 만들며**(프로세스 자체의 종료 코드도), 거짓 PASS는 없습니다.
  stdout에 `[PASS] all steps completed`가 뜨면 깨끗한 실행입니다.
- 복구 가능한 사건(팝업이 닫히고 스텝이 재시도돼서 성공)은 `_warnings`에
  기록되고 출력되지만 테스트를 실패시키진 않습니다.

### 재생 아키텍처(생성 시점에 자동으로 선택됨)

| 모드 | 언제 | 재생 방식 |
|---|---|---|
| **Simple** | 단일 창 네이티브 앱 | `appium:app = exePath`; 클릭은 raw Appium REST(`element` + `element/click`, UIA Invoke)로 |
| **Session** | 멀티윈도우 흐름 또는 Electron 계열 앱 | `appium:app = 'Root'`; 새 HWND마다 자기만의 스코프된 WinAppDriver 세션이 생김; 클릭/타이핑은 **그 창의 세션 안에서** XPath를 해결함(`_clickScoped`/`_typeScoped`), 매 HWND 경계마다 명시적인 `switch to window: ...` 스텝이 로그됨 |

스크롤은 절대 픽셀을 쓰지 않습니다: 녹화된 스크롤 컨테이너를 UIA로 다시
찾아서 `ScrollPattern.Scroll()`로 스크롤하고, ScrollPattern이 없는 레거시
컨트롤은 hwnd 스코프의 `WM_MOUSEWHEEL`을 `PostMessageW`로 보냅니다.

**네이티브 ComboBox/메뉴 팝업**(Win32 드롭다운, 메뉴바 항목)은 앱의 메인
창 안이 아니라 **별도의 최상위 창**으로 렌더링되는 경우가 많습니다 —
생성될 때 스코프된 일반 WinAppDriver 세션은 그 팝업을 아예 못 봅니다.
codegen 시점의 메커니즘 두 가지가 이걸 처리합니다(둘 다 WinAppDriver
세션을 건너뛰고 COM UIA로 직접 재생):

- **`osExpandCollapse`** — `ExpandCollapsePattern`을 노출하는 컨트롤용
  (ComboBox, 메뉴바 `MenuItem`, 트리 `+`/`-` 토글): 컨트롤을 펼친 뒤,
  먼저 메인 창에서, 그다음 새로 나타난 아무 최상위 창에서든(네이티브
  `TrackPopupMenu` 스타일 팝업) 대상 항목을 찾습니다.
- **`osScopedInvoke`** — ExpandCollapsePattern이 없는 평범한 `Button`이
  자기만의 최상위 창으로 렌더링되는 드롭다운 목록을 여는 경우: 트리거
  클릭과 뒤이은 항목 검색이 **하나의 프로세스 안에서** 실행되므로,
  항목을 찾기 전에 드롭다운이 자동으로 닫혀버리는 틈이 없습니다.

녹화는 이런 컨트롤을 여는/선택하는 클릭(들)을 별개 이벤트로 캡처합니다;
`server/server.js`가 codegen 시점에 그것들을 하나의 호출로 합쳐서, 열기→
검색 사이에 스텝 경계가 안 생기게 합니다.

### 체크박스 클릭은 값까지 검증합니다, 에러 여부만 확인하는 게 아니라

일반 WinAppDriver `element/click()`은 클릭 호출이 예외 없이 반환되는 순간
성공을 보고합니다 — 체크박스의 `ToggleState`가 실제로 바뀌었는지는 전혀
확인하지 않습니다. 이건 진짜 거짓-PASS 위험입니다(TeamViewer의 WebView2
토글에서 실측): `Legacy.Select()`/`Invoke()` 폴백이 체크박스를 건드리지도
않은 채 "성공"할 수 있습니다. 모든 `CheckBox` 클릭(같은 창이든 다른
창이든)은 대신 `osScopedInvoke`를 거치는데, 이건 클릭 전후로
`ToggleState`를 읽어서 실제로 바뀌었을 때만 성공을 보고합니다 — 시각적
클릭만으로 반영이 안 되면 직접 `TogglePattern.Toggle()` 호출로 폴백합니다.

### WebView2 / Electron 앱

WebView2/Electron 호스트 창에 WinAppDriver 세션을 성공적으로 붙인다고 해서
그 안의 콘텐츠에 접근 가능하다는 뜻은 **아닙니다** — 내장된 Chromium
렌더러는 무슨 수를 써도 WinAppDriver의 관리형 UIA 클라이언트에겐
보이지 않습니다. TeamViewer 15(WebView2)에서 처음부터 끝까지 검증했고,
이 부류의 앱을 제대로 재생시키려면 별도 수정 3가지가 필요했는데, 전부
`element.isWebContent`(요소가 속한 창이 내장 Chromium 자식을 호스팅할 때
`agent/agent.py`가 설정)를 기준으로 삼습니다:

1. **클릭** — `isWebContent` 요소는 일반 WinAppDriver `element/click`
   대신 `osScopedInvoke`(COM UIA)를 거칩니다 — `agent.py`의 캡처 자체가
   이 요소들을 보기 위해 쓰는 것과 같은 메커니즘입니다.
2. **셀렉터** — `isWebContent` 요소는 `className`을 아예 안 씁니다. 웹
   프레임워크의 `className`은 날것의 DOM `class` 속성이라 — hover/active/
   disabled 상태 클래스까지 포함한 Tailwind 스타일 유틸리티 토큰 수십 개
   짜리인데, 그 문자열에 대한 정확 일치 AND 조건은 캡처와 재생 사이에
   토큰 하나만 달라도 깨집니다. `name`만 씁니다(실전에서 거의 모든
   인터랙티브 웹 요소에 존재하고 안정적입니다).
3. **타이핑** — `isWebContent` 필드에 타이핑할 때는 WinAppDriver의
   `element/value`(`ValuePattern.SetValue`)를 아예 건너뛰고 항상 진짜
   OS 레벨 키 주입(`osType`, `SendInput` 기반)으로 폴백합니다. `SetValue`
   는 에러 없이 성공을 보고할 수 있는데도 React 스타일 앱은 진짜 키보드
   이벤트를 전혀 못 받아서 필드가 빈 채로 남습니다 — TeamViewer의 세션
   코드 필드에서 직접 실측됨.
4. **권한 레벨이 일치해야 합니다.** Chromium은 접근성 트리를 지연 활성화
   하고, Windows의 UIPI(사용자 인터페이스 권한 격리)는 **낮은** 권한의
   자동화 클라이언트가 **높은** 권한 창 안의 어떤 콘텐츠도 못 보게
   막습니다 — 타이밍 문제가 아니라서, 아무리 기다리거나 워밍업 클릭을
   보내도 우회가 안 됩니다(둘 다 시도하고 실측함 —
   `poc/diag_teamviewer_a11y_wakeup.py` /
   `poc/diag_teamviewer_real_click_wakeup.py` 참고). TeamViewer는 상승된
   권한으로 실행되므로, 대상 앱이 상승돼 있을 땐 **agent, Express
   브릿지가 띄우는 Appium/WinAppDriver 프로세스들, 생성된 테스트 스크립트
   전부 관리자 터미널에서 실행돼야 합니다** — 일반 터미널은 아무리
   기다리거나 클릭을 많이 보내도 창 껍데기(요소 1~2개)만 봅니다.

이건 Chromium 호스트 하나(TeamViewer)에 대해서만 검증됐습니다. 다른
Electron/WebView2 앱도 같은 방식으로 동작할 것으로 예상되지만 테스트되진
않았습니다 — 새로운 앱을 "그냥 될 것"이라 가정하기 전에
`poc/probe_app_automatability.py --exe ...`로 먼저 확인해보세요.

---

## 4. 회귀 테스트 (agent 불필요, 관리자 권한 불필요, GUI 불필요)

```powershell
# 터미널 1
cd server; node server.js

# 터미널 2
python agent/mock_events.py
# 기대값: NNN/NNN checks passed (새 버그가 회귀 커버리지에 추가될 때마다
# 개수가 늘어남 — 출력된 총합을 확인하고, 숫자를 하드코딩하지 마세요)
```

`mock_events.py`는 살아있는 서버에 합성 녹화를 POST합니다 — 단일 창 앱,
멀티윈도우(세션 모드) 앱, 숫자 AutomationId 처리/ExpandCollapse 병합/
크로스윈도우 scoped invoke를 다루는 네이티브 Win32 다이얼로그 시나리오까지
포함해서 — 모든 경로에 대해 코드를 생성하고 그 출력을 검사합니다: XPath
전용 불변조건(`osClick(`/`osDrag(`/`osClickRel(`이 어디에도 없음),
anchor-XPath 렌더링, 더블클릭 중복제거, Fail-and-Recover 배선(ESC 복구가
앱 자신의 전면 메인 창에는 절대 안 걸리는 것 포함), 헬퍼 파일 내용
(ScrollPattern 존재, `SetCursorPos` 부재, 관리형 UIA 대신 COM `comtypes`
사용), 그리고 이전 버전이 남긴 낡은 좌표/관리형-UIA 헬퍼가 재생성 시
출력 폴더에서 제거되는지까지. 이 게이트가 생성하는 목업 앱 2개
(`generated-wdio/MockMulti/`, `generated-wdio/MockNative/`)는 실제
녹화가 아니라 회귀 게이트용 픽스처입니다 — git-ignored이고 지워도
안전합니다; 게이트를 다시 돌리면 재생성됩니다.

### `type` 스텝의 거짓 PASS 잡아내기

`mock_events.py`는 *생성된 코드*를 검사하지, 실제 재생 중 화면에서 진짜
무슨 일이 일어났는지는 안 봅니다. `agent/verify_replay.py`가 타이핑에
대해서만큼은 이 공백을 메꿉니다: 생성된 `<App>TestById.js`를 정상적으로
실행하면서, 그와 동시에 각 `type` 스텝의 대상 필드의 **살아있는 UIA
값**을 COM으로 독립적으로 다시 읽어서(`agent.py`/`osScopedInvoke.py`와
같은 스택) 녹화가 기대한 값과 비교합니다. 이게 존재하는 이유: WinAppDriver
의 `element/value`는 필드가 실제로는 전혀 채워지지 않았는데도 성공을
보고할 수 있기 때문입니다(FileZilla의 Site Manager에서 실측,
2026-08-06) — 재생 스크립트 자체의 종료 코드가 "성공"이라고 해서 키
입력이 실제로 반영됐다는 증거는 아닙니다.

```powershell
python agent/verify_replay.py --app FileZilla
python agent/verify_replay.py --app FileZilla --strategy byclass
```

---

## 문제 해결

| 증상 | 유력한 원인 | 해결 |
|---|---|---|
| Express에서 `Agent unreachable` | Python agent가 안 떠 있음 | 관리자 터미널에서 `python agent.py` 실행 |
| `Administrator rights: NO` | agent가 관리자 권한이 아님 | PowerShell을 관리자 권한으로 다시 열기 |
| 캡처된 요소의 `automationId`/`name`이 비어있음 | agent가 관리자 권한이 아니거나, 그 앱이 정말로 아무것도 노출하지 않음(제약사항 참고) | agent를 관리자로 재시작; agent 로그에서 `[inspect] anchor XPath ...` 줄 확인 |
| `n:click:no-selector`로 테스트 실패 | 그 이벤트가 셀렉터도 앵커도 없이 캡처됨(좌표는 금지) | 그 이벤트 행을 삭제하고 더 차분한 페이스로 그 상호작용을 다시 녹화 |
| 4723번 포트에서 `Connection refused` | Appium이 안 떠 있음 | 스크립트 자체의 `ensureAppium()`이 처리함; 콘솔에서 `[appium] starting Appium...` 확인 |
| `SessionNotCreatedException` | 잘못된 exe 경로 / AUMID | Launch에 넘긴 경로 확인 |
| 재생 시 `NoSuchElementException` | 로케이터 불일치 또는 타이밍 | `ById` 대신 `ByClass` 스펙 시도; 앱의 UI 상태가 녹화와 일치하는지 확인 |
| 재생이 뭔가를 클릭했더니 낯선 다이얼로그가 뜨고, `popup-dismissed` 경고와 함께 테스트는 그래도 통과 | 의도대로 동작 중 — Fail-and-Recover가 닫고 재시도함 | 할 거 없음; 궁금하면 `_warnings` 출력 확인 |
| 녹화된 행에 작업표시줄/IDE 클릭이 포함됨 | 녹화 중 대상 밖을 클릭함 | Generate 전에 그 행들 삭제 |
| `UnicodeEncodeError cp949` | Windows 터미널 인코딩 | Python 스크립트 실행 전 `chcp 65001` |

---

## 알려진 제약사항

- **Electron/Chromium 앱은 지원됩니다(검증됨: TeamViewer/WebView2), 스코프
  밖이 아닙니다** — 이게 왜 필요했는지 세 가지 수정과 권한 요구사항은 위
  **WebView2 / Electron apps** 참고. 이건 이 앱 부류를 한 번의 낡은
  측정만으로 자동화 불가능하다고 단정했던 이전(2026-07-31) 판정을
  뒤집은 것입니다; 재측정이 그 판정을 뒤집은 근거입니다 — 앱 부류를 나쁜
  측정 하나로 "불가능"이라고 재단하고 싶어질 때는 `CLAUDE.md` §4에서
  전체 경위를 확인하세요.
- **단일 인스턴스 앱은 `launchApp()`을 깨뜨립니다.** 이 함수는 녹화 시작
  전 베이스라인에 *없던* 창만 인식하므로, 이미 실행 중인 앱(TeamViewer,
  Win11 메모장)에 대해 녹화를 시작하면 절대 나타나지 않을 "새" 창을
  기다리다 타임아웃됩니다. 먼저 앱을 완전히 종료하세요.
- **HeidiSQL의 "더 보기" 오버플로 메뉴 항목은 캡처되지만 아직
  재생되지 않습니다.** 트리거가 VCL `SplitButton`인데, 그
  `ExpandCollapsePattern`이 재생 시점엔 절대 조회가 안 됩니다(COM
  `GetCurrentPattern`이 항상 예외를 던짐) — 재생의 예외 처리기가 이
  경우 "ExpandCollapsePattern이 없다 = 이건 사실 메뉴가 아니라 평범한
  커맨드 버튼이었다"고 가정하고, 캡처된 항목 인덱스를 참고하는 대신
  트리거를 한 번 더 호출합니다. 캡처 쪽은 동작합니다(위치 기반, owner-drawn
  콤보와 같은 패턴, 재시도 예산 수정 후 ~90% 성공률) — 재생 쪽
  `osExpandCollapse.py`만 "그냥 트리거를 다시 클릭"으로 폴백하기 전에
  캡처된 `itemIndex`를 먼저 확인하도록 고치면 됩니다.
- **Qt/QML 앱**은 실제 `MouseArea`가 전혀 안 걸려도 UIA Invoke를 에러
  없이 받아들일 수 있고, AutomationId가 유일하지 않은 경우가 많습니다 —
  현재 스코프 밖입니다.
- **UWP 특이사항**: 창 제목이 로딩 중 바뀝니다(`contains(@Name,...)`
  매칭이 대부분 처리함); Win11 메모장은 사용자의 저장 안 된 탭을 물고
  있는 단일 인스턴스 앱이라 — 다른 데모 대상을 쓰는 게 낫습니다.
- **관리자 매니페스트 대상**(예: `regedit.exe`): 비상승 agent는 최상위
  창은 볼 수 있지만 UIPI가 자식 요소 검사를 막습니다 — agent를 상승된
  권한으로 실행하세요(어차피 필수입니다).
- **타이핑 캡처**는 컨트롤 타입으로 필터링됩니다
  (`{"Edit", "Document", "ComboBox"}`). 새로운 앱 타입에 대한 타이핑이
  조용히 버려진다면, `agent/agent.py`의 `INPUT_CONTROL_TYPES`에 그 대상
  컨트롤 타입을 추가해야 합니다.
- **숫자 AutomationId는 클래식 Win32 다이얼로그에서 유일하지 않을 수
  있습니다.** 일부 앱(예: PuTTY의 카테고리 패널)은 서로 다른 패널에서
  같은 숫자 리소스 ID를 재사용합니다. 셀렉터 해결은 잘못된 same-id
  컨트롤에 매칭되는 걸 피하려고, 단일 필드로 폴백하기 전에 캡처된 모든
  필드(id + name + className)를 AND로 먼저 시도합니다.
- **한국어 Windows 타이틀바의 닫기(X) 버튼은 이름이 "닫기"인 UIA
  `Button`입니다** — Win32 ComboBox 드롭다운 화살표도 가질 수 있는 것과
  같은 접근성 이름입니다. 드롭다운 화살표 요소는 항상 자신의
  AutomationId(`~DropDown`)로 해결되지, 맨 `//Button[@Name="닫기"]`로는
  절대 해결되지 않으므로, 창 껍데기(닫기 버튼)와 실수로 매칭될 일이
  없습니다.
- **다이얼로그의 타이틀바를 클릭하는 건 캡처는 되지만 그 자체로는 재생되지
  않습니다** — 최상위 창 자체를 클릭한 것과 마찬가지로, 시도되는 대신
  통째로 드롭됩니다(FAIL 스텝조차 생성 안 됨). `TitleBar` 요소는 OS가
  그리는 창 틀이라 세션 간 안정적인 UIA 정체성이 없기 때문입니다(거기서
  만든 Name 기반 셀렉터는 재생 시 항상 실패합니다: `target not found` /
  `ElementFromHandle failed`, 2026-08-10 실측). **알려진 트레이드오프**:
  이 판단은 `controlType`만 보고 이뤄지기 때문에, 진짜 타이틀바
  더블클릭-최대화 동작도 같이 조용히 사라집니다. 타이틀바로 창을 끄는
  드래그는 별개의, 이미 스코프 밖인 경우입니다(드래그 이벤트는 진단용
  으로 캡처되지만 재생되진 않음 — 위 §2 참고).
- **.NET 관리형 UIA(`System.Windows.Automation`)는 레거시 Win32
  컨트롤(리스트 행, 툴바 버튼, `SysTreeView32` 트리 항목)을 못 봅니다** —
  그런 컨트롤에 도달해야 하는 모든 재생 헬퍼는 대신 COM
  `IUIAutomation`(comtypes)을 쓰며, `agent/agent.py`가 이미 쓰는 스택과
  동일합니다.
- **Delphi/VCL 앱**(HeidiSQL로 검증됨)은 진짜로 선언된 `AutomationId`가
  없는 컨트롤을 노출합니다 — 기본 Win32 UIA provider가 그 속성을 대신
  컨트롤 자신의 윈도우 핸들로 채우는데, 이 값은 실행할 때마다 새로
  할당됩니다. 이 id는 자동으로 거부되고 안정적인 `ClassName`/`Name`
  셀렉터가 대신 쓰이므로, 대부분의 버튼/탭/입력 필드는 잘 동작합니다.
- **커스텀 렌더링(owner-drawn) 컨트롤** — HeidiSQL의 세션 목록
  `TVirtualStringTree`가 확인된 사례입니다 — 컨트롤 자체는 눈에 보이고
  내용도 채워져 있는데도 UI Automation에 항목을 *하나도* 노출하지
  않습니다(살아있는 UIA 트리에 대고 직접 검증함: `FindAll`/트리 순회
  전부 행이 하나도 없는 같은 노드 개수에 동의). 이건 권한이 없어서도,
  탐색이 너무 일찍 포기해서도 아닙니다 — 개별 행을 대표하는 UIA 요소를
  돌려줄 수 있는 코드가 시스템 어디에도 없습니다, 애초에 이 컨트롤
  클래스가 그 단위(행 단위)로는 UIA provider에 연결된 적이 없기
  때문입니다. 어떤 셀렉터도, 앵커도, COM 기반 검색도 개별 행에 도달할 수
  없습니다 — **그런 목록에 대한 클릭 녹화는 현재 불가능합니다.** 대신
  그 목록을 건드리지 않는 흐름을 녹화하세요(예: HeidiSQL의 "New" 버튼은
  세션을 만들고 자동으로 선택하므로, 그 밑의 탭들은 목록을 전혀 건드리지
  않고도 바로 클릭 가능해집니다).
  - "New"가 같은 트리 위에 여는 인라인 이름 편집 상자도 같은 문제를
    가집니다 — 이름을 확정하는 Enter(또는 다른 곳 클릭)는 예전엔 완전히
    빈 셀렉터로 캡처돼서 재생이 **항상** 실패했습니다(`selector has no
    usable fields`). 2026-08-10에 수정: `type` 이벤트에 쓸 수 있는 셀렉터
    필드가 전혀 없고 *동시에* 그 값이 단일 제어 키(`\n`/`\r`/`\t`/`\x1b`,
    즉 진짜 텍스트가 아니라 확정/취소용 키 입력)일 때만, codegen은 요소
    검색을 시도(해서 항상 실패)하는 대신 곧장 OS 레벨 SendKeys로 지금
    포커스에 그 키를 보냅니다. 진짜 텍스트 필드가 일시적으로 셀렉터를
    잃은 경우는 여전히 기존의 요소검색→폴백 경로를 그대로 타므로, 이게
    실제 텍스트를 무관한 창에 무지성으로 흘려보내는 일은 절대 없습니다.
- **드롭다운: 어려운 건 이름 붙이기지, 여는 게 아닙니다.** 2026-07-31에
  `poc/diag_dropdowns.py`로 실측 — HeidiSQL의 콤보가 항목을 노출하지
  않는다던 이전 주장을 정정합니다:
  - *PuTTY* — 콤보에 고유한 `AutomationId`가 있음; 펼치면 항목 5개가
    노출됨(앱 창 안에서, 그리고 별도의 최상위 `ComboLBox` 창에서도).
    **완전히 자동화 가능.**
  - *HeidiSQL* — 펼치면 항목이 **노출은 됩니다**(각각 5개, 18개). 실패
    하는 지점은 *어느* 콤보를 열지 식별하는 것입니다. 새 세션 패널의
    콤보 두 개 다 안정적이고 유일한 핸들을 안 줍니다: 하나는
    `AutomationId`가 사실 자신의 윈도우 핸들(`920954`, 실행마다 다름)
    이고, 다른 하나는 **AutomationId도 Name도 아예 없습니다**; 드롭다운
    화살표 둘 다 `AutomationId="DropDown"`을 공유하고, 그 `Name`은 열림
    상태에 따라 `열기`/`닫기`로 바뀝니다. 네트워크 유형 콤보는 `Pane`으로
    노출되는 `TComboBoxEx`인데 그 `Name`이 **현재 선택된 값**입니다
    (`MariaDB or MySQL (TCP/IP)`, `MySQL on RDS`, …) — 그러니 Name 기반
    셀렉터는 그 값이 이미 선택돼 있을 때만 매칭됩니다.
  - 적용된 완화책: 한 창 안에서 여러 후보가 `AutomationId="DropDown"`을
    공유하면, 해결 로직은 구조적으로 동일한 매칭들 중 어느 걸 고를지를
    녹화된 **창 안 상대 Y 위치**로 폴백합니다. 이건 매칭된 요소들 중
    *어느 것*을 대상으로 할지 고르는 것이지 — 좌표를 클릭하는 게
    아니므로 §3은 여전히 유지됩니다.
  - *Owner-drawn 드롭다운 항목* — HeidiSQL의 네트워크 유형 콤보는 Win32
    `ComboBoxEx`입니다: 두 컨트롤이 사각형 하나를 공유하고, 안쪽
    `ComboBox`(Name도 AutomationId도 없음)만 펼칠 수 있습니다. 그 안의
    항목 18개는 전부 개별적으로 invoke 가능하지만 **전부 이름이
    없어서**, 위치로만 지목할 수 있습니다. 2026-07-31부터 처리됨: 캡처가
    `comboItemIndex`/`comboItemCount`를 기록하고, 재생이 콤보를 펼쳐서
    N번째 항목을 invoke하며, 살아있는 목록 길이가 녹화된 것과 다르면
    아무것도 고르지 않고 거부합니다. 처음부터 끝까지 검증됨(값이
    `MariaDB or MySQL (TCP/IP)` → `MySQL on RDS`로 바뀜). 이 수정 전에는
    클릭이 캡처 시점에 버려지고 열린 목록 뒤에 있는 패널 클릭으로
    격하됐습니다.

## 프로젝트 구조

```
agent/          Python 캡처 agent + mock_events.py 회귀 게이트 +
                verify_replay.py (재생 후 살아있는 값 검증)
server/         Express 브릿지 + 템플릿 기반 코드 생성기
ui/             React 대시보드 (Vite)
generated-wdio/ 생성된 테스트 모음(앱별 폴더) + 공유 npm 의존성
recorded-events/  모든 녹화 세션의 JSON 백업 (git-ignored)
poc/            독립 PoC들 (PowerShell + Python COM UIA) — XPath 클릭,
                ScrollPattern, HWND 스코핑, ExpandCollapsePattern 진단
```
