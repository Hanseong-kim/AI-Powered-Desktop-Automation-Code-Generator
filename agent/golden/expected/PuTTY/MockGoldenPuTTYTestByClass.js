import { execSync, spawn } from 'child_process';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { homedir, tmpdir } from 'os';
import { openSync, closeSync, mkdtempSync, writeFileSync, existsSync } from 'fs';

const __dirname = dirname(fileURLToPath(import.meta.url));

// 2026-07-27: this .js file is the whole deliverable — a user copying just
// this one file elsewhere (a different folder, a colleague's machine) must
// not need the os*.ps1/os*.py siblings this project also writes next to it
// for human inspection. Every os*.ps1/os*.py invocation below resolves its
// script through _helperFile(name) instead of join(__dirname, name): the
// actual script text is embedded right here as a string, written out to a
// per-process temp dir on first use and reused after that. saveFiles()
// still writes the standalone .ps1/.py copies alongside this file (so they
// stay inspectable/debuggable), but this file no longer depends on them
// being there.
async function _typeVerified(title, selector, text) {
    return await _typeScopedOrCom(title || '', selector, text);
}

const _H = {
    'osWindowRect.ps1': "param([string]$titleLike, [string]$hwnd, [switch]$listOnly, [switch]$ownerOnly, [string]$siblingOf)\nAdd-Type @\"\nusing System;\nusing System.Text;\nusing System.Collections.Generic;\nusing System.Runtime.InteropServices;\npublic class WinEnum {\n  public delegate bool EnumProc(IntPtr hWnd, IntPtr lParam);\n  [DllImport(\"user32.dll\")] public static extern bool EnumWindows(EnumProc proc, IntPtr lParam);\n  [DllImport(\"user32.dll\")] public static extern bool IsWindowVisible(IntPtr hWnd);\n  [DllImport(\"user32.dll\")] public static extern bool IsIconic(IntPtr hWnd);\n  [DllImport(\"user32.dll\")] public static extern int GetWindowTextLength(IntPtr hWnd);\n  [DllImport(\"user32.dll\", CharSet = CharSet.Auto)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder sb, int max);\n  [DllImport(\"user32.dll\")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT r);\n  [DllImport(\"user32.dll\")] public static extern IntPtr GetForegroundWindow();\n  [DllImport(\"user32.dll\")] public static extern IntPtr GetWindow(IntPtr hWnd, uint cmd);\n  [DllImport(\"user32.dll\")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);\n  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }\n  public static List<IntPtr> Find(string titleLike) {\n    var found = new List<IntPtr>();\n    EnumWindows((hWnd, lParam) => {\n      if (!IsWindowVisible(hWnd) || IsIconic(hWnd)) return true;\n      int len = GetWindowTextLength(hWnd);\n      if (len == 0) return true;\n      var sb = new StringBuilder(len + 1);\n      GetWindowText(hWnd, sb, sb.Capacity);\n      if (sb.ToString().Contains(titleLike)) found.Add(hWnd);\n      return true;\n    }, IntPtr.Zero);\n    return found;\n  }\n  // Sibling top-level windows of the same process as hwndOf, excluding\n  // hwndOf itself, restricted to ones with a real (non-zero) rect. Used to\n  // recover from WinAppDriver/OS binding the \"main\" window to a hidden 0x0\n  // helper window instead of the app's actual visible form (observed with\n  // HeidiSQL's Delphi/VCL 'TApplication' window).\n  public static List<IntPtr> FindSizedSiblings(IntPtr hwndOf) {\n    var found = new List<IntPtr>();\n    uint pid;\n    GetWindowThreadProcessId(hwndOf, out pid);\n    if (pid == 0) return found;\n    EnumWindows((hWnd, lParam) => {\n      if (hWnd == hwndOf || !IsWindowVisible(hWnd)) return true;\n      uint wpid;\n      GetWindowThreadProcessId(hWnd, out wpid);\n      if (wpid != pid) return true;\n      RECT r;\n      if (GetWindowRect(hWnd, out r) && (r.Right - r.Left) > 0 && (r.Bottom - r.Top) > 0) {\n        found.Add(hWnd);\n      }\n      return true;\n    }, IntPtr.Zero);\n    return found;\n  }\n}\n\"@ -ErrorAction SilentlyContinue\nif ($siblingOf) {\n  $sibs = [WinEnum]::FindSizedSiblings([IntPtr]([int64]$siblingOf))\n  if ($sibs.Count -gt 0) { Write-Output ([int64]$sibs[0]) }\n  exit\n}\n# -hwnd targets one specific window directly, bypassing title matching entirely.\n# Title matching alone is ambiguous whenever more than one window shares a\n# substring (e.g. every VS Code window's title ends in \"Visual Studio Code\") —\n# callers that already know their window's handle MUST use -hwnd so replay\n# never drifts onto an unrelated window (see launchApp's hwnd tracking).\nif ($hwnd) {\n  $h = [IntPtr]([int64]$hwnd)\n  if ($ownerOnly) {\n    # GW_OWNER=4 — nonzero means an owned (dialog-style) window, which\n    # WinAppDriver's appTopLevelWindow rejects outright (\"not a top level\n    # window handle\"), so callers skip the scoped-session attempt entirely.\n    Write-Output ([int64][WinEnum]::GetWindow($h, 4))\n    exit\n  }\n  $r = New-Object WinEnum+RECT\n  if ([WinEnum]::GetWindowRect($h, [ref]$r)) {\n    Write-Output (\"{0} {1} {2} {3}\" -f $r.Left, $r.Top, ($r.Right - $r.Left), ($r.Bottom - $r.Top))\n  }\n  exit\n}\n$matches = [WinEnum]::Find($titleLike)\nif ($listOnly) {\n  foreach ($h in $matches) { Write-Output ([int64]$h) }\n  exit\n}\nif ($matches.Count -gt 0) {\n  $fg = [WinEnum]::GetForegroundWindow()\n  $hWnd = $matches[0]\n  foreach ($h in $matches) { if ($h -eq $fg) { $hWnd = $h; break } }\n  $r = New-Object WinEnum+RECT\n  [WinEnum]::GetWindowRect($hWnd, [ref]$r) | Out-Null\n  Write-Output (\"{0} {1} {2} {3}\" -f $r.Left, $r.Top, ($r.Right - $r.Left), ($r.Bottom - $r.Top))\n}\n",
    'osMoveWindow.ps1': "param([string]$titleLike, [string]$hwnd, [int]$left, [int]$top, [int]$width, [int]$height)\nAdd-Type @\"\nusing System;\nusing System.Text;\nusing System.Collections.Generic;\nusing System.Runtime.InteropServices;\npublic class WinMove {\n  public delegate bool EnumProc(IntPtr hWnd, IntPtr lParam);\n  [DllImport(\"user32.dll\")] public static extern bool EnumWindows(EnumProc proc, IntPtr lParam);\n  [DllImport(\"user32.dll\")] public static extern bool IsWindowVisible(IntPtr hWnd);\n  [DllImport(\"user32.dll\")] public static extern bool IsIconic(IntPtr hWnd);\n  [DllImport(\"user32.dll\")] public static extern int GetWindowTextLength(IntPtr hWnd);\n  [DllImport(\"user32.dll\", CharSet = CharSet.Auto)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder sb, int max);\n  [DllImport(\"user32.dll\")] public static extern IntPtr GetForegroundWindow();\n  [DllImport(\"user32.dll\")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);\n  [DllImport(\"user32.dll\")] public static extern bool MoveWindow(IntPtr hWnd, int X, int Y, int nWidth, int nHeight, bool bRepaint);\n  [DllImport(\"user32.dll\")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT r);\n  [DllImport(\"user32.dll\")] public static extern bool IsZoomed(IntPtr hWnd);\n  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }\n  public static List<IntPtr> Find(string titleLike) {\n    var found = new List<IntPtr>();\n    EnumWindows((hWnd, lParam) => {\n      if (!IsWindowVisible(hWnd) || IsIconic(hWnd)) return true;\n      int len = GetWindowTextLength(hWnd);\n      if (len == 0) return true;\n      var sb = new StringBuilder(len + 1);\n      GetWindowText(hWnd, sb, sb.Capacity);\n      if (sb.ToString().Contains(titleLike)) found.Add(hWnd);\n      return true;\n    }, IntPtr.Zero);\n    return found;\n  }\n}\n\"@ -ErrorAction SilentlyContinue\n# -hwnd bypasses title matching — see OS_WINRECT_PS1 for why ambiguous title\n# substrings (e.g. any two VS Code windows) are unsafe to move/resize by.\nif ($hwnd) {\n  $hWnd = [IntPtr]([int64]$hwnd)\n} else {\n  $matches = [WinMove]::Find($titleLike)\n  $hWnd = [IntPtr]::Zero\n  if ($matches.Count -gt 0) {\n    $fg = [WinMove]::GetForegroundWindow()\n    $hWnd = $matches[0]\n    foreach ($h in $matches) { if ($h -eq $fg) { $hWnd = $h; break } }\n  }\n}\nif ($hWnd -ne [IntPtr]::Zero) {\n  # Idempotency fast-path: if the window is already at the target geometry\n  # (and not maximized), skip ShowWindow(RESTORE)+MoveWindow entirely — avoids\n  # a visible restore-then-resize flicker when replay finds the window already\n  # in the recorded position (e.g. the \"already maximized\" case reported\n  # 2026-07-07: recorded flow assumes a maximize step is needed, but the\n  # window is already there).\n  $already = New-Object WinMove+RECT\n  [WinMove]::GetWindowRect($hWnd, [ref]$already) | Out-Null\n  $sameW = [math]::Abs(($already.Right - $already.Left) - $width) -le 2\n  $sameH = [math]::Abs(($already.Bottom - $already.Top) - $height) -le 2\n  $sameL = [math]::Abs($already.Left - $left) -le 2\n  $sameT = [math]::Abs($already.Top - $top) -le 2\n  if (-not [WinMove]::IsZoomed($hWnd) -and $sameW -and $sameH -and $sameL -and $sameT) {\n    exit\n  }\n  [WinMove]::ShowWindow($hWnd, 9) | Out-Null\n  Start-Sleep -Milliseconds 300\n  $candW = $width\n  $candH = $height\n  for ($i = 0; $i -lt 3; $i++) {\n    [WinMove]::MoveWindow($hWnd, $left, $top, $candW, $candH, $true) | Out-Null\n    Start-Sleep -Milliseconds 300\n    $r = New-Object WinMove+RECT\n    [WinMove]::GetWindowRect($hWnd, [ref]$r) | Out-Null\n    $actualW = $r.Right - $r.Left\n    $actualH = $r.Bottom - $r.Top\n    if ([math]::Abs($actualW - $width) -le 2 -and [math]::Abs($actualH - $height) -le 2) { break }\n    if ($actualW -le 0 -or $actualH -le 0) { break }\n    $candW = [int]([math]::Round(($width * $candW) / [double]$actualW))\n    $candH = [int]([math]::Round(($height * $candH) / [double]$actualH))\n  }\n}\n",
    'osType.ps1': "param([string]$b64)\nAdd-Type -AssemblyName System.Windows.Forms\n$text = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($b64))\n$special = '+^%~(){}[]'\nStart-Sleep -Milliseconds 200\n[System.Windows.Forms.SendKeys]::SendWait(\"^a\")\nStart-Sleep -Milliseconds 30\nforeach ($ch in $text.ToCharArray()) {\n  if ($ch -eq \"`n\") { [System.Windows.Forms.SendKeys]::SendWait(\"{ENTER}\"); Start-Sleep -Milliseconds 15; continue }\n  if ($ch -eq \"`r\") { continue }\n  $s = [string]$ch\n  if ($special.IndexOf($ch) -ge 0) { $s = \"{$ch}\" }\n  [System.Windows.Forms.SendKeys]::SendWait($s)\n  Start-Sleep -Milliseconds 15\n}\n",
    'osEscape.ps1': "Add-Type -AssemblyName System.Windows.Forms\nStart-Sleep -Milliseconds 100\n[System.Windows.Forms.SendKeys]::SendWait(\"{ESC}\")\n",
    'osActivate.ps1': "param([string]$titleLike, [string]$hwnd)\nAdd-Type @\"\nusing System;\nusing System.Text;\nusing System.Collections.Generic;\nusing System.Runtime.InteropServices;\npublic class WinActivate {\n  public delegate bool EnumProc(IntPtr h, IntPtr l);\n  [DllImport(\"user32.dll\")] public static extern bool EnumWindows(EnumProc p, IntPtr l);\n  [DllImport(\"user32.dll\")] public static extern bool IsWindowVisible(IntPtr h);\n  [DllImport(\"user32.dll\")] public static extern int GetWindowTextLength(IntPtr h);\n  [DllImport(\"user32.dll\", CharSet=CharSet.Auto)] public static extern int GetWindowText(IntPtr h, StringBuilder sb, int m);\n  [DllImport(\"user32.dll\")] public static extern bool ShowWindow(IntPtr h, int n);\n  [DllImport(\"user32.dll\")] public static extern bool SetForegroundWindow(IntPtr h);\n  [DllImport(\"user32.dll\")] public static extern bool BringWindowToTop(IntPtr h);\n  [DllImport(\"user32.dll\")] public static extern IntPtr GetForegroundWindow();\n  [DllImport(\"user32.dll\")] public static extern uint GetWindowThreadProcessId(IntPtr h, IntPtr pid);\n  [DllImport(\"user32.dll\")] public static extern bool AttachThreadInput(uint a, uint b, bool f);\n  [DllImport(\"kernel32.dll\")] public static extern uint GetCurrentThreadId();\n  [DllImport(\"user32.dll\")] public static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint flags);\n  public static List<IntPtr> Find(string t) {\n    var f = new List<IntPtr>();\n    EnumWindows((h,l) => { if(!IsWindowVisible(h)) return true; int n=GetWindowTextLength(h); if(n==0) return true;\n      var sb=new StringBuilder(n+1); GetWindowText(h,sb,sb.Capacity); if(sb.ToString().Contains(t)) f.Add(h); return true; }, IntPtr.Zero);\n    return f;\n  }\n  public static void Force(IntPtr h) {\n    ShowWindow(h, 9); // SW_RESTORE\n    uint fg = GetWindowThreadProcessId(GetForegroundWindow(), IntPtr.Zero);\n    uint me = GetCurrentThreadId();\n    if (fg != me) AttachThreadInput(me, fg, true);\n    BringWindowToTop(h); SetForegroundWindow(h);\n    SetWindowPos(h, (IntPtr)(-1), 0,0,0,0, 0x0003); // TOPMOST, NOMOVE|NOSIZE\n    SetWindowPos(h, (IntPtr)(-2), 0,0,0,0, 0x0003); // NOTOPMOST\n    if (fg != me) AttachThreadInput(me, fg, false);\n  }\n}\n\"@ -ErrorAction SilentlyContinue\nif ($hwnd) {\n  [WinActivate]::Force([IntPtr]([int64]$hwnd)); Start-Sleep -Milliseconds 250\n} else {\n  $m = [WinActivate]::Find($titleLike)\n  if ($m.Count -gt 0) { [WinActivate]::Force($m[0]); Start-Sleep -Milliseconds 250 }\n}\n",
    'osDismissPopup.ps1': "param([string]$titleLike, [string]$hwnd, [string]$exclude)\ntry { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}\nAdd-Type -AssemblyName UIAutomationClient, UIAutomationTypes -ErrorAction SilentlyContinue\nAdd-Type @\"\nusing System;\nusing System.Text;\nusing System.Collections.Generic;\nusing System.Runtime.InteropServices;\npublic class PopupWin {\n  public delegate bool EnumProc(IntPtr hWnd, IntPtr lParam);\n  [DllImport(\"user32.dll\")] public static extern bool EnumWindows(EnumProc proc, IntPtr lParam);\n  [DllImport(\"user32.dll\")] public static extern bool IsWindowVisible(IntPtr hWnd);\n  [DllImport(\"user32.dll\")] public static extern bool IsWindow(IntPtr hWnd);\n  [DllImport(\"user32.dll\")] public static extern int GetWindowTextLength(IntPtr hWnd);\n  [DllImport(\"user32.dll\", CharSet = CharSet.Auto)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder sb, int max);\n  [DllImport(\"user32.dll\", CharSet = CharSet.Auto)] public static extern int GetClassName(IntPtr hWnd, StringBuilder sb, int max);\n  [DllImport(\"user32.dll\")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);\n  [DllImport(\"user32.dll\")] public static extern IntPtr SendMessage(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);\n  [DllImport(\"user32.dll\")] public static extern IntPtr GetWindow(IntPtr hWnd, uint cmd);\n  public static IntPtr OwnerOf(IntPtr h) { return GetWindow(h, 4); } // GW_OWNER\n  public static List<IntPtr> AllTop() {\n    var f = new List<IntPtr>();\n    EnumWindows((h, l) => { if (IsWindowVisible(h)) f.Add(h); return true; }, IntPtr.Zero);\n    return f;\n  }\n  public static string ClassOf(IntPtr h) {\n    var sb = new StringBuilder(256);\n    GetClassName(h, sb, sb.Capacity);\n    return sb.ToString();\n  }\n  public static string TitleOf(IntPtr h) {\n    int len = GetWindowTextLength(h);\n    if (len == 0) return \"\";\n    var sb = new StringBuilder(len + 1);\n    GetWindowText(h, sb, sb.Capacity);\n    return sb.ToString();\n  }\n  public static uint PidOf(IntPtr h) {\n    uint pid; GetWindowThreadProcessId(h, out pid); return pid;\n  }\n}\n\"@ -ErrorAction SilentlyContinue\n\n$mainHwnd = [IntPtr]::Zero\nif ($hwnd) { $mainHwnd = [IntPtr]([int64]$hwnd) }\nelseif ($titleLike) {\n  foreach ($h in [PopupWin]::AllTop()) {\n    if ([PopupWin]::TitleOf($h).Contains($titleLike)) { $mainHwnd = $h; break }\n  }\n}\n$targetPid = 0\nif ($mainHwnd -ne [IntPtr]::Zero) { $targetPid = [PopupWin]::PidOf($mainHwnd) }\n\n$excludeSet = New-Object 'System.Collections.Generic.HashSet[long]'\nif ($exclude) {\n  foreach ($tok in ($exclude -split ',')) {\n    $t = $tok.Trim()\n    if ($t) { [void]$excludeSet.Add([int64]$t) }\n  }\n}\n\n# Candidate = dialog-shaped window of the target process only:\n#  - same PID AND (#32770 class OR owned window)  → native/Electron/Qt dialogs\n#  - #32770 owned by a target-PID window          → dialog hosted out-of-process\n# Never: unowned main-class windows (a sibling VS Code window shares the PID\n# but is nobody's popup), excluded hwnds (windows the replay itself drives),\n# or windows of unrelated processes. If no target PID could be resolved at\n# all, dismiss nothing — guessing across the whole desktop is how an\n# unrelated app loses a dialog.\n$candidates = New-Object System.Collections.Generic.List[IntPtr]\nif ($targetPid -ne 0) {\n  foreach ($h in [PopupWin]::AllTop()) {\n    if ($h -eq $mainHwnd) { continue }\n    if ($excludeSet.Contains([int64]$h)) { continue }\n    $isDialogClass = ([PopupWin]::ClassOf($h) -eq '#32770')\n    $owner = [PopupWin]::OwnerOf($h)\n    $qualifies = $false\n    if ([PopupWin]::PidOf($h) -eq $targetPid) {\n      $qualifies = ($isDialogClass -or ($owner -ne [IntPtr]::Zero))\n    } elseif ($isDialogClass -and $owner -ne [IntPtr]::Zero -and [PopupWin]::PidOf($owner) -eq $targetPid) {\n      $qualifies = $true\n    }\n    if ($qualifies) { $candidates.Add($h) }\n  }\n}\n\n# Conservative order: no-side-effect dismissal first (Cancel/No/Close), only\n# reach for an affirmative (OK/Yes) if nothing safer matched — a wrong click\n# here should never silently accept a destructive action.\n$preferred = @('취소', '아니요', '닫기', 'Cancel', 'No', 'Close', '확인', 'OK', '예', 'Yes')\n\n$dismissed = $false\nforeach ($h in $candidates) {\n  if ($dismissed) { break }\n  try {\n    $el = [System.Windows.Automation.AutomationElement]::FromHandle($h)\n    if (-not $el) { continue }\n    $cond = New-Object System.Windows.Automation.PropertyCondition(\n      [System.Windows.Automation.AutomationElement]::ControlTypeProperty,\n      [System.Windows.Automation.ControlType]::Button)\n    $buttons = $el.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cond)\n    foreach ($btnName in $preferred) {\n      if ($dismissed) { break }\n      foreach ($b in $buttons) {\n        if ($b.Current.Name -ne $btnName) { continue }\n        $clicked = $false\n        try {\n          $inv = $b.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)\n          $inv.Invoke()\n          $clicked = $true\n        } catch {\n          try {\n            $legacy = $b.GetCurrentPattern([System.Windows.Automation.LegacyIAccessiblePattern]::Pattern)\n            $legacy.DoDefaultAction()\n            $clicked = $true\n          } catch {\n            # Owner-drawn / non-standard control — last resort: BM_CLICK via SendMessage.\n            try {\n              $bh = [IntPtr]$b.Current.NativeWindowHandle\n              if ($bh -ne [IntPtr]::Zero) { [PopupWin]::SendMessage($bh, 0x00F5, [IntPtr]::Zero, [IntPtr]::Zero) | Out-Null; $clicked = $true }\n            } catch {}\n          }\n        }\n        if ($clicked) {\n          $deadline = (Get-Date).AddSeconds(3)\n          while ((Get-Date) -lt $deadline -and [PopupWin]::IsWindow($h)) { Start-Sleep -Milliseconds 100 }\n          Write-Output \"DISMISSED|$btnName|$([PopupWin]::TitleOf($h))\"\n          $dismissed = $true\n        }\n        break\n      }\n    }\n  } catch {}\n}\nif (-not $dismissed) { Write-Output \"NONE\" }\n",
    'osScroll.py': "import sys, json, base64, argparse, ctypes\nfrom ctypes import wintypes\n\nif sys.stdout.encoding and sys.stdout.encoding.lower() not in (\"utf-8\", \"utf8\"):\n    sys.stdout.reconfigure(encoding=\"utf-8\", errors=\"replace\")\nif sys.stderr.encoding and sys.stderr.encoding.lower() not in (\"utf-8\", \"utf8\"):\n    sys.stderr.reconfigure(encoding=\"utf-8\", errors=\"replace\")\n\nimport comtypes\nimport comtypes.client\n\nUIA_NameProperty = 30005\nUIA_AutomationIdProperty = 30011\nUIA_ClassNameProperty = 30012\nUIA_ScrollPatternId = 10004\nTreeScope_Descendants = 4\nWM_MOUSEWHEEL = 0x020A\n# ScrollAmount enum (UIAutomationClient.h): LargeDecrement=0, SmallDecrement=1,\n# NoAmount=2, LargeIncrement=3, SmallIncrement=4.\nSCROLL_NO_AMOUNT = 2\nSCROLL_SMALL_DECREMENT = 1\nSCROLL_SMALL_INCREMENT = 4\n\nuser32 = ctypes.windll.user32\nuser32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]\nuser32.PostMessageW.restype = wintypes.BOOL\n\n\ndef find_target(uia, root, sel):\n    # 캡처 시점에 agent.py가 ScrollPattern 보유 조상으로 걸어 올라가 기록한\n    # 컨테이너 셀렉터 — PS1과 동일하게 automationId/className/name 순으로\n    # 단독 조건을 하나씩 시도(AND 아님).\n    if sel:\n        for prop, key in ((UIA_AutomationIdProperty, \"automationId\"),\n                           (UIA_ClassNameProperty, \"className\"),\n                           (UIA_NameProperty, \"name\")):\n            if sel.get(key):\n                try:\n                    cond = uia.CreatePropertyCondition(prop, sel[key])\n                    t = root.FindFirst(TreeScope_Descendants, cond)\n                    if t:\n                        return t\n                except Exception:\n                    pass\n    return root\n\n\ndef main():\n    ap = argparse.ArgumentParser()\n    ap.add_argument(\"--hwnd\", type=int, required=True)\n    ap.add_argument(\"--sel-b64\", default=\"\")\n    ap.add_argument(\"--delta\", type=int, required=True)\n    args = ap.parse_args()\n\n    if not args.hwnd:\n        print(\"osScroll: --hwnd is required\", file=sys.stderr)\n        sys.exit(2)\n\n    comtypes.CoInitialize()\n    mod = comtypes.client.GetModule(\"UIAutomationCore.dll\")\n    uia = comtypes.client.CreateObject(\n        \"{ff48dba4-60ef-4201-aa87-54103eef594e}\", interface=mod.IUIAutomation\n    )\n\n    try:\n        root = uia.ElementFromHandle(args.hwnd)\n    except Exception as e:\n        print(f\"osScroll: ElementFromHandle raised: {e}\", file=sys.stderr)\n        sys.exit(2)\n    if not root:\n        print(\"osScroll: ElementFromHandle failed\", file=sys.stderr)\n        sys.exit(2)\n\n    sel = None\n    if args.sel_b64:\n        try:\n            sel = json.loads(base64.b64decode(args.sel_b64).decode(\"utf-8\"))\n        except Exception:\n            sel = None\n    target = find_target(uia, root, sel)\n\n    # 1차: 대상(또는 가장 가까운 스크롤 가능 조상)의 ScrollPattern.\n    walker = uia.ControlViewWalker\n    cur = target\n    scroll = None\n    for _ in range(10):\n        if cur is None:\n            break\n        try:\n            p = cur.GetCurrentPattern(UIA_ScrollPatternId)\n            if p:\n                sp = p.QueryInterface(mod.IUIAutomationScrollPattern)\n                if sp.CurrentVerticallyScrollable:\n                    scroll = sp\n                    break\n        except Exception:\n            pass\n        try:\n            cur = walker.GetParentElement(cur)\n        except Exception:\n            break\n\n    if scroll:\n        try:\n            before = scroll.CurrentVerticalScrollPercent\n        except Exception:\n            before = None\n        # 휠 업(양수 delta) = 콘텐츠 위로 = SmallDecrement. 노치당 약 3줄.\n        direction = SCROLL_SMALL_DECREMENT if args.delta > 0 else SCROLL_SMALL_INCREMENT\n        n = abs(args.delta) * 3\n        # Scroll()은 PS1 원본에도 예외처리가 없던 자리 — 콤보 팝업이 스크롤\n        # 도중 상태를 바꾸면(자동 닫힘 등) 반복 호출 중 COM 예외를 던져 스크립트\n        # 전체가 죽고, 그게 _step()의 ESC 복구로 이어져 다이얼로그 기반 앱\n        # (ESC==Cancel)을 통째로 닫혀버리게 만드는 것을 실측(2026-07-14, PuTTY\n        # STEP 6). 1회라도 성공했으면 성공으로 보고하고, 중간에 실패하면 그\n        # 지점에서 멈추고 아래로 흘려보낸다 — 한 번도 못 돌렸으면(scrolled==0)\n        # ScrollPattern 자체가 못 미더운 것으로 보고 PostMessageW 폴백으로.\n        scrolled = 0\n        for _ in range(n):\n            try:\n                scroll.Scroll(SCROLL_NO_AMOUNT, direction)\n                scrolled += 1\n            except Exception as e:\n                print(f\"[osScroll] WARN Scroll() failed after {scrolled}/{n} notches: {e}\")\n                break\n        if scrolled > 0:\n            try:\n                after = scroll.CurrentVerticalScrollPercent\n            except Exception:\n                after = None\n            print(f\"[osScroll] ScrollPattern {before} -> {after} (delta={args.delta}, {scrolled}/{n} notches applied)\")\n            sys.exit(0)\n        print(\"[osScroll] WARN ScrollPattern found but Scroll() failed immediately — falling back to PostMessageW\")\n\n    # 2차: hwnd-scoped WM_MOUSEWHEEL (PostMessageW — 비동기, SendMessage 금지).\n    post_h = args.hwnd\n    cur = target\n    for _ in range(10):\n        if cur is None:\n            break\n        try:\n            nh = cur.CurrentNativeWindowHandle\n            if nh:\n                post_h = nh\n                break\n            cur = walker.GetParentElement(cur)\n        except Exception:\n            break\n\n    cx = cy = 0\n    try:\n        r = target.CurrentBoundingRectangle\n        cx = int(r.left + (r.right - r.left) / 2)\n        cy = int(r.top + (r.bottom - r.top) / 2)\n    except Exception:\n        pass\n\n    wparam = ((args.delta * 120) << 16) & 0xFFFFFFFFFFFFFFFF\n    lparam = ((cy & 0xFFFF) << 16) | (cx & 0xFFFF)\n    user32.PostMessageW(post_h, WM_MOUSEWHEEL, wparam, lparam)\n    print(f\"[osScroll] PostMessageW WM_MOUSEWHEEL hwnd={post_h} delta={args.delta} (ScrollPattern unavailable)\")\n\n\nif __name__ == \"__main__\":\n    main()\n",
    'osExpandCollapse.py': "import os, sys, json, base64, argparse, ctypes, time\nfrom ctypes import wintypes\n\nif sys.stdout.encoding and sys.stdout.encoding.lower() not in (\"utf-8\", \"utf8\"):\n    sys.stdout.reconfigure(encoding=\"utf-8\", errors=\"replace\")\nif sys.stderr.encoding and sys.stderr.encoding.lower() not in (\"utf-8\", \"utf8\"):\n    sys.stderr.reconfigure(encoding=\"utf-8\", errors=\"replace\")\n\nimport comtypes\nimport comtypes.client\n\nUIA_NameProperty = 30005\nUIA_AutomationIdProperty = 30011\nUIA_ClassNameProperty = 30012\nUIA_InvokePatternId = 10000\nUIA_ExpandCollapsePatternId = 10005\nUIA_SelectionItemPatternId = 10010\nUIA_LegacyIAccessiblePatternId = 10018\nUIA_SELECTIONFLAG_TAKESELECTION = 1\nUIA_ScrollPatternId = 10004\nUIA_ControlTypeProperty = 30003\nUIA_ListItem = 50007\nUIA_MenuItem = 50011\nTreeScope_Children = 2\nTreeScope_Descendants = 4\nTreeScope_Subtree = 7\nExpandCollapseState_Expanded = 1\n\nuser32 = ctypes.windll.user32\n\n# ── dynamic ClickablePoint + SendInput (2026-07-24) ─────────────────────────\n# 녹화된 좌표는 여기 어디에도 들어오지 않는다. 좌표는 매 실행마다 UIA가 방금\n# resolve한 요소로부터 계산해 즉시 소비하고 버린다 — 창이 이동/리사이즈되거나\n# 해상도가 바뀌어도 항상 새로 계산되므로 §3 금지의 원래 취지(저장된 좌표가\n# 재생 시점에 어긋나는 것)를 건드리지 않는다.\nSM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77\nSM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79\nMOUSEEVENTF_MOVE = 0x0001\nMOUSEEVENTF_LEFTDOWN = 0x0002\nMOUSEEVENTF_LEFTUP = 0x0004\nMOUSEEVENTF_VIRTUALDESK = 0x4000\nMOUSEEVENTF_ABSOLUTE = 0x8000\nINPUT_MOUSE = 0\n\nULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong\n\n\nclass MOUSEINPUT(ctypes.Structure):\n    _fields_ = [(\"dx\", wintypes.LONG), (\"dy\", wintypes.LONG),\n                (\"mouseData\", wintypes.DWORD), (\"dwFlags\", wintypes.DWORD),\n                (\"time\", wintypes.DWORD), (\"dwExtraInfo\", ULONG_PTR)]\n\n\nclass _INPUTUNION(ctypes.Union):\n    _fields_ = [(\"mi\", MOUSEINPUT)]\n\n\nclass INPUT(ctypes.Structure):\n    _anonymous_ = (\"u\",)\n    _fields_ = [(\"type\", wintypes.DWORD), (\"u\", _INPUTUNION)]\n\n\ndef force_foreground(hwnd):\n    \"\"\"AttachThreadInput 트릭으로 포그라운드 잠금을 우회해 hwnd를 실제 OS\n    포그라운드/활성 창으로 올린다 — SIMPLE_HEADER의 osActivate.ps1\n    (WinActivate.Force)과 같은 로직이지만, 별도 프로세스를 띄우고 그 프로세스가\n    끝나길 기다리는 대신 이 프로세스 안에서 곧바로 이어지는 Expand()/Invoke()\n    호출과 같은 프로세스 수명 안에서 실행된다.\n    2026-08-08 실측(FileZilla \"도움말(H)\" 메뉴, launchApp 직후 첫 액션):\n    launchApp()이 별도 PowerShell 프로세스(osActivate)로 창을 활성화했는데도\n    Expand()가 매번 state=0(펼쳐지지 않음), 새 팝업 0개로 실패했다 — PowerShell\n    프로세스가 활성화 직후 종료되면 Windows가 포그라운드를 그 부모(터미널)에게\n    돌려주는 것으로 보여, 별도 프로세스가 끝나는 순간 활성화 효과가 사라진다.\n    실제 화면 클릭(COM-SendInput)을 거치는 스텝들은 클릭 좌표 아래 창이 자연히\n    포그라운드가 되므로 이 레이스를 겪지 않는다 — Expand()처럼 시각적 동작이\n    없는 패턴 API만 문제다.\n    \"\"\"\n    if not hwnd:\n        return\n    try:\n        SW_RESTORE = 9\n        user32.ShowWindow(hwnd, SW_RESTORE)\n        fg = user32.GetForegroundWindow()\n        fg_tid = user32.GetWindowThreadProcessId(fg, None)\n        me_tid = ctypes.windll.kernel32.GetCurrentThreadId()\n        attached = False\n        if fg_tid and fg_tid != me_tid:\n            attached = bool(user32.AttachThreadInput(me_tid, fg_tid, True))\n        user32.BringWindowToTop(hwnd)\n        user32.SetForegroundWindow(hwnd)\n        user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0003)   # HWND_TOPMOST, NOMOVE|NOSIZE\n        user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0003)   # HWND_NOTOPMOST\n        if attached:\n            user32.AttachThreadInput(me_tid, fg_tid, False)\n        time.sleep(0.15)\n    except Exception:\n        pass\n\n\ndef enable_per_monitor_dpi():\n    # agent.py의 _enable_per_monitor_dpi_awareness()와 동일한 근거로 필수:\n    # 파이썬 프로세스는 기본 DPI-unaware라 125% 스케일 환경에서 UIA가 돌려주는\n    # rect/ClickablePoint가 가상화된 논리 좌표로 오는 반면 SendInput의 절대\n    # 좌표계는 물리 픽셀이다 — 격상하지 않으면 두 좌표계가 어긋나 엉뚱한\n    # 지점을 클릭한다. UIA 객체를 만들기 전에 호출해야 한다.\n    try:\n        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))\n        return\n    except Exception:\n        pass\n    try:\n        ctypes.windll.shcore.SetProcessDpiAwareness(2)\n    except Exception:\n        pass\n\n\ndef clickable_point(el):\n    \"\"\"UIA가 지금 이 순간 계산한 클릭 지점. 실패하면 None.\"\"\"\n    try:\n        res = el.GetClickablePoint()\n    except Exception:\n        res = None\n    # comtypes는 [out] 파라미터 2개(POINT, BOOL)를 튜플로 돌려주지만 바인딩\n    # 버전에 따라 POINT 하나만 오는 경우도 있어 양쪽을 모두 받아준다.\n    if res is not None:\n        pt, got = (res if isinstance(res, (tuple, list)) and len(res) == 2 else (res, 1))\n        if got and pt is not None:\n            try:\n                return int(pt.x), int(pt.y)\n            except Exception:\n                pass\n    try:\n        r = el.CurrentBoundingRectangle\n        if r.right > r.left and r.bottom > r.top:\n            return (r.left + r.right) // 2, (r.top + r.bottom) // 2\n    except Exception:\n        pass\n    return None\n\n\ndef listitem_label_point(uia, el):\n    \"\"\"리포트 뷰 행에서 실제로 항목을 여는 지점 — 이름(첫 컬럼) 셀의 중심.\n\n    실측 2026-08-04 (7-Zip SysListView32, poc 프로브):\n        행(ListItem) rect = (797, 267, 1405, 286)   중심 x=1101\n        이름 셀(Edit)  rect = (800, 267,  833, 286)  중심 x=816\n        중심(1101) 더블클릭 -> 제목/행 목록 전부 불변 (아무 일도 안 일어남)\n        이름 셀(816) 더블클릭 -> 즉시 진입, 행 1개에서 25개로\n\n    행 rect는 모든 컬럼을 가로지르므로 그 중심은 \"수정한 날짜/크기\" 컬럼의\n    빈 공간이다. 선택(단일 클릭)은 full-row select라 어디를 눌러도 되지만\n    활성화(더블클릭)는 이름 셀에서만 일어난다 — 그래서 단일 클릭 스텝은\n    멀쩡했고 폴더 진입만 조용히 실패했다.\n\n    좌표를 저장하지 않는다: 재생 시점에 살아 있는 자식 요소의 rect에서\n    계산한다(§3의 dynamic ClickablePoint 규칙 그대로). 자식이 없는 행이면\n    None을 돌려 기존 clickable_point() 동작을 그대로 쓴다.\n    \"\"\"\n    TS_CHILDREN = 2\n    UIA_ListItem = 50007\n    try:\n        if el.CurrentControlType != UIA_ListItem:\n            return None\n    except Exception:\n        return None\n    try:\n        arr = el.FindAll(TS_CHILDREN, uia.CreateTrueCondition())\n    except Exception:\n        return None\n    best = None\n    for i in range(arr.Length if arr else 0):\n        try:\n            r = arr.GetElement(i).CurrentBoundingRectangle\n        except Exception:\n            continue\n        if r.right <= r.left or r.bottom <= r.top:\n            continue\n        # 가장 왼쪽 셀 = 이름 컬럼. 아이콘+라벨이 여기 있다.\n        if best is None or r.left < best[0]:\n            best = (r.left, (r.left + r.right) // 2, (r.top + r.bottom) // 2)\n    return (best[1], best[2]) if best else None\n\n\ndef _same_or_descendant(uia, ancestor, el, max_up=6):\n    cur = el\n    try:\n        walker = uia.RawViewWalker\n    except Exception:\n        walker = None\n    for _ in range(max_up + 1):\n        try:\n            if uia.CompareElements(ancestor, cur):\n                return True\n        except Exception:\n            return False\n        if not walker:\n            return False\n        try:\n            cur = walker.GetParentElement(cur)\n        except Exception:\n            return False\n        if not cur:\n            return False\n    return False\n\n\ndef send_input_click(uia, el, tag, double=False):\n    \"\"\"UIA로 방금 찾은 요소를 실제 마우스 입력으로 클릭한다.\n\n    안전 검증을 하나라도 통과 못 하면 사유를 남기고 False — 호출자는 기존\n    프로그래매틱 Invoke()/Select() 체인으로 폴백한다(에러로 튕기는 것보다\n    비시각적으로라도 동작하는 게 낫다는 2026-07-24 지시).\n    \"\"\"\n    def bail(reason):\n        print(\"[COM-SendInput] fallback: \" + reason + \" - using programmatic Invoke/Select\", file=sys.stderr)\n        return False\n\n    if os.environ.get(\"QAFORGE_COM_CLICK\") == \"invoke\":\n        return bail(\"forced-programmatic (QAFORGE_COM_CLICK=invoke)\")\n    try:\n        if el.CurrentIsOffscreen:\n            return bail(\"offscreen\")\n    except Exception:\n        pass\n\n    # 라벨은 반드시 주입 **전에** 읽는다. 메뉴 항목/다이얼로그 버튼은 클릭\n    # 즉시 파괴돼 그 뒤의 프로퍼티 읽기가 실패하고, 로그가 '?'로 남아 추적이\n    # 불가능해진다(2026-07-24 FileZilla 실측: 메뉴 항목/예(Y)/취소 전부 '?',\n    # 살아남는 콤보 화살표만 '닫기'로 정상 출력).\n    try:\n        label = el.CurrentName or el.CurrentAutomationId or \"?\"\n    except Exception:\n        label = \"?\"\n\n    # 리포트 뷰 행은 rect 중심이 빈 컬럼이라 더블클릭이 안 먹는다 —\n    # listitem_label_point() 참고 (2026-08-04 실측).\n    pt = listitem_label_point(uia, el) or clickable_point(el)\n    if not pt:\n        return bail(\"no-clickable-point\")\n    x, y = pt\n    vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)\n    vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)\n    vw = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)\n    vh = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)\n    if vw <= 1 or vh <= 1 or not (vx <= x < vx + vw and vy <= y < vy + vh):\n        return bail(\"point-outside-virtual-screen (%d,%d)\" % (x, y))\n\n    # 그 지점이 정말 대상 요소의 것인지 두 겹으로 확인한다. 2026-07-15에\n    # osScopedInvoke가 완전히 남남인 사용자 창(탐색기/VS Code)을 클릭하고\n    # \"성공\"으로 보고한 사고, 2026-07-13에 클릭 지점이 요소 rect 밖이라\n    # 물리적으로 no-op이던 이벤트가 재생 때는 요소 중심을 클릭해 엉뚱한 패널을\n    # 연 트랩 — 둘 다 이 검증으로 구조적으로 막힌다.\n    winpt = wintypes.POINT(int(x), int(y))\n    try:\n        hwnd_at = user32.WindowFromPoint(winpt)\n        pid_at = wintypes.DWORD()\n        user32.GetWindowThreadProcessId(hwnd_at, ctypes.byref(pid_at))\n        if pid_at.value != el.CurrentProcessId:\n            return bail(\"covered-by-other-window (pid %d at point, target pid %d)\"\n                        % (pid_at.value, el.CurrentProcessId))\n    except Exception as e:\n        return bail(\"window-hit-test-failed (%s)\" % e)\n\n    try:\n        at_point = uia.ElementFromPoint(winpt)\n    except Exception as e:\n        return bail(\"element-from-point-failed (%s)\" % e)\n    if not at_point or not _same_or_descendant(uia, el, at_point):\n        return bail(\"point-resolves-elsewhere\")\n\n    nx = int(round((x - vx) * 65535.0 / (vw - 1)))\n    ny = int(round((y - vy) * 65535.0 / (vh - 1)))\n\n    def send(flags):\n        inp = INPUT(type=INPUT_MOUSE)\n        inp.mi = MOUSEINPUT(nx, ny, 0, flags, 0, 0)\n        return user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))\n\n    # 사람이 눈으로 따라갈 수 있도록 이동/누름/뗌 사이에 간격을 둔다(§6).\n    if not send(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK):\n        return bail(\"SendInput(move) rejected\")\n    time.sleep(0.04)\n    if not send(MOUSEEVENTF_LEFTDOWN | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK):\n        return bail(\"SendInput(down) rejected\")\n    time.sleep(0.04)\n    send(MOUSEEVENTF_LEFTUP | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK)\n\n    # 두 번째 누름/뗌 — 시스템 더블클릭 간격(기본 500ms) 안에 보내야 앱이\n    # 더블클릭으로 인식한다. 여기 간격 합은 ~90ms.\n    #\n    # 2026-08-04: 이 분기가 없어서 녹화된 더블클릭이 앱에는 단일 클릭으로\n    # 도달했다. 네이티브 리스트 행에서 단일 클릭은 \"선택\"이지 \"열기\"가 아니다.\n    # 원래 설계는 InvokePattern.Invoke()(=기본 동작=열기)에 기대고 있었는데\n    # (server.js ListItem 분기 주석, 2026-07-15 실측), 2026-07-24에\n    # send_input_click()이 invoke_item() 체인 맨 앞에 붙으면서\n    # \"if send_input_click(...): return True\"가 되어 그 Invoke()가 도달 불가능한\n    # 죽은 코드가 됐다. 결과: 7-Zip에서 \"3:doubleClick 컴퓨터\"가 성공으로\n    # 보고되지만 폴더는 안 열리고, \"4:doubleClick C:\"부터 전부 무너진다\n    # (2026-08-04 두 번 연속 동일 재현).\n    if double:\n        time.sleep(0.05)\n        send(MOUSEEVENTF_LEFTDOWN | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK)\n        time.sleep(0.04)\n        send(MOUSEEVENTF_LEFTUP | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK)\n\n    verb = \" double-clicked '\" if double else \" clicked '\"\n    print(\"[COM-SendInput] \" + tag + verb + label + \"' at (%d,%d)\" % (x, y))\n    time.sleep(0.05)\n    return True\n\n\n# ── TogglePattern (2026-07-31) ─────────────────────────────────────────────\n# 실측(TeamViewer 15.79 WebView2, poc/diag_teamviewer_a11y_wakeup.py [D]):\n# 체크박스 2종의 지원 패턴은 정확히 {Toggle, Legacy}뿐 — Invoke도\n# SelectionItem도 없다. 기존 클릭 체인(Invoke → SelectionItem → Legacy)에는\n# Toggle이 아예 없어서 앞의 둘이 연달아 예외로 떨어진 뒤\n# Legacy.Select(TAKESELECTION)이 예외 없이 통과하며 True를 반환했다. 그런데\n# 체크박스에서 \"셀렉션\"은 체크 상태를 바꾸지 않으므로, 재생은 성공으로\n# 보고되는데 화면에서는 아무 일도 일어나지 않는다 — 그 체크박스가 띄우는\n# 확인 다이얼로그도 당연히 안 뜬다(2026-07-31 클라이언트 피드백 증상 그대로).\n# Legacy 폴백보다 앞서 Toggle을 시도하되, ToggleState가 실제로 바뀐 경우에만\n# 성공으로 인정한다(거짓 PASS 방지 — 2026-07-13 3차 교훈과 같은 원칙).\nUIA_TogglePatternId = 10015\n\n\ndef toggle_item(mod, el, tag):\n    try:\n        tp = el.GetCurrentPattern(UIA_TogglePatternId).QueryInterface(\n            mod.IUIAutomationTogglePattern)\n    except Exception:\n        return False\n    try:\n        before = tp.CurrentToggleState\n        tp.Toggle()\n        time.sleep(0.05)\n        after = tp.CurrentToggleState\n    except Exception as e:\n        print(\"[\" + tag + \"] TogglePattern.Toggle() raised: %s\" % e, file=sys.stderr)\n        return False\n    if after != before:\n        print(\"[\" + tag + \"] TogglePattern.Toggle() %d -> %d\" % (before, after))\n        return True\n    print(\"[\" + tag + \"] TogglePattern.Toggle() left ToggleState unchanged (%d)\"\n          \" - falling through to the remaining patterns\" % before, file=sys.stderr)\n    return False\n\n\n# ── checkbox 값-변경 검증 (2026-08-04) ───────────────────────────────────────\n# 위 toggle_item()은 invoke_item()의 체인 맨 끝(Legacy 폴백보다 앞)에서만\n# 쓰인다 — invoke_item()이 맨 앞에서 시도하는 send_input_click()(실제 화면\n# 클릭)이 성공하면 그 자리에서 곧바로 True를 반환하므로(§6 \"재생은 시각적으로\n# 확인 가능해야 한다\"는 요구를 만족하는 기본 경로), 대다수 체크박스 클릭은\n# toggle_item()까지 내려가지도 않는다. 그 결과 \"클릭 자체는 에러 없이 끝났다\"만\n# 보고 실제로 체크 상태가 바뀌었는지는 아무도 확인하지 않는다 — TeamViewer\n# WebView2 토글에서 실측된 것과 같은 종류의 거짓 PASS 위험이 CheckBox\n# controlType 전반에 구조적으로 남아 있다(HeidiSQL/PuTTY의 TCheckBox·Button\n# 스타일 체크박스도 이 경로를 그대로 탄다 — 2026-08-04 점검 시점엔 아직 실제\n# 재생 실패로 드러난 적은 없지만, 다음에 체크박스가 있는 녹화를 재생하면 언제든\n# 조용히 터질 수 있는 잠재적 구멍). 시각적 클릭은 그대로 유지하면서(§6), 클릭\n# 전후 ToggleState를 비교해 실제로 바뀌었는지만 추가로 검증한다 — 안 바뀌었으면\n# toggle_item()의 직접 Toggle() 호출로 한 번 더 보정 시도하고, 그래도 안 바뀌면\n# 정직하게 실패로 보고한다(호출부가 exit code로 판단해 _failures에 기록).\ndef verified_toggle_click(uia, mod, el, tag=\"osScopedInvoke\", double=False):\n    try:\n        tp = el.GetCurrentPattern(UIA_TogglePatternId).QueryInterface(\n            mod.IUIAutomationTogglePattern)\n    except Exception:\n        # TogglePattern이 없다 = 이 컨트롤은 애초에 체크박스가 아니다(예:\n        # CheckBox로 잘못 태깅됐거나 캡처 시점 이후 컨트롤이 바뀐 경우) —\n        # 검증할 상태 자체가 없으므로 평범한 클릭으로 폴백한다. 없는 걸\n        # 있다고 우기며 거짓 실패를 만들지 않는다.\n        return invoke_item(uia, mod, el, double)\n    try:\n        before = tp.CurrentToggleState\n    except Exception:\n        return invoke_item(uia, mod, el, double)\n    if not invoke_item(uia, mod, el, double):\n        return False\n    time.sleep(0.05)\n    try:\n        after = tp.CurrentToggleState\n    except Exception as e:\n        print(f\"[{tag}] click succeeded but ToggleState could not be \"\n              f\"re-read afterward ({e}) — cannot verify, trusting the click\", file=sys.stderr)\n        return True\n    if after != before:\n        print(f\"[{tag}] checkbox toggled {before} -> {after} (verified)\")\n        return True\n    print(f\"[{tag}] click reported success but ToggleState stayed {before} \"\n          \"unchanged — retrying via TogglePattern.Toggle() directly\", file=sys.stderr)\n    return toggle_item(mod, el, tag)\n\n\ndef top_windows():\n    found = []\n\n    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)\n    def cb(hwnd, _):\n        if user32.IsWindowVisible(hwnd):\n            found.append(hwnd)\n        return True\n\n    user32.EnumWindows(cb, 0)\n    return found\n\n\n# 2026-07-24: WAD의 element/click은 오프스크린(스크롤로 안 보이는) 요소를\n# 자동으로 스크롤-인-뷰한 뒤 클릭한다(암묵적 동작) — 이 파일이 다루는 COM\n# 경로(WAD 세션이 못 보는 팝업/새 창의 좁은 예외)에는 그 자동 스크롤이 없어,\n# Expand() 이후 메뉴/드롭다운 항목이 화면 밖에 있으면 Invoke 전에 조상\n# ScrollPattern으로 끌어와야 한다. 좌표는 쓰지 않는다(§3).\ndef find_scrollable_ancestor(uia, el, max_up=8):\n    try:\n        walker = uia.RawViewWalker\n    except Exception:\n        return None\n    cur = el\n    for _ in range(max_up):\n        try:\n            cur.GetCurrentPattern(UIA_ScrollPatternId)\n            return cur\n        except Exception:\n            pass\n        try:\n            cur = walker.GetParentElement(cur)\n            if not cur:\n                return None\n        except Exception:\n            return None\n    return None\n\n\ndef ensure_visible(uia, mod, el):\n    try:\n        if not el.CurrentIsOffscreen:\n            return\n    except Exception:\n        return\n    print(\"[osExpandCollapse] target is offscreen — attempting scroll-into-view via ancestor ScrollPattern\", file=sys.stderr)\n    ancestor = find_scrollable_ancestor(uia, el)\n    if not ancestor:\n        return\n    try:\n        sp = ancestor.GetCurrentPattern(UIA_ScrollPatternId).QueryInterface(mod.IUIAutomationScrollPattern)\n        sp.SetScrollPercent(50.0, 50.0)\n        time.sleep(0.2)\n    except Exception:\n        pass\n\n\ndef field_conds(uia, sel):\n    conds = []\n    if sel.get(\"automationId\"):\n        conds.append(uia.CreatePropertyCondition(UIA_AutomationIdProperty, sel[\"automationId\"]))\n    if sel.get(\"name\"):\n        conds.append(uia.CreatePropertyCondition(UIA_NameProperty, sel[\"name\"]))\n    if sel.get(\"className\"):\n        conds.append(uia.CreatePropertyCondition(UIA_ClassNameProperty, sel[\"className\"]))\n    # 2026-08-10 (FileZilla \"C:\" 트리 vs 리스트 혼동 실측, osScopedInvoke.py의\n    # resolve_cond()와 같은 이유로 동시 적용 — 사용자 리뷰 지적): 같은 화면에\n    # 같은 Name의 owner-drawn 컨트롤이 둘 이상(TreeItem/ListItem) 있을 때\n    # FindFirst가 아무거나 먼저 걸리는 걸 막는 추가 AND 조건.\n    if sel.get(\"controlTypeId\") is not None:\n        conds.append(uia.CreatePropertyCondition(\n            UIA_ControlTypeProperty, int(sel[\"controlTypeId\"])))\n    return conds\n\n\ndef resolve_target(uia, root, sel):\n    # PuTTY류 다이얼로그는 카테고리 패널마다 숫자 AutomationId를 재사용한다\n    # (2026-07-13 실측: id=1044가 라디오 버튼과 \"Proxy type:\" 콤보에 동시에 붙음)\n    # — 있는 필드를 전부 AND로 묶은 조건을 먼저 시도해 모호성을 없애고, 그래도\n    # 못 찾으면 필드별 단독 조건으로 폴백.\n    conds = field_conds(uia, sel)\n    if not conds:\n        return None\n    if len(conds) > 1:\n        combined = conds[0]\n        for c in conds[1:]:\n            combined = uia.CreateAndCondition(combined, c)\n        try:\n            t = root.FindFirst(TreeScope_Descendants, combined)\n            if t:\n                return t\n        except Exception:\n            pass\n    for c in conds:\n        try:\n            t = root.FindFirst(TreeScope_Descendants, c)\n            if t:\n                return t\n        except Exception:\n            continue\n    return None\n\n\ndef invoke_item(uia, mod, el, double=False):\n    ensure_visible(uia, mod, el)\n    try:\n        el.SetFocus()\n    except Exception:\n        pass\n    # 시각적 재생 우선(2026-07-24, §6) — 성공하면 반드시 여기서 반환한다.\n    # 이어서 Invoke()까지 부르면 같은 동작이 두 번 실행된다.\n    # 2026-08-09: double=True면 두 번 누른다(OS_SCOPEDINVOKE_PY의 로컬\n    # invoke_item 재정의와 동일한 의미) — 이 공유 버전은 OS_EXPANDCOLLAPSE_PY의\n    # --ancestor-sel-b64 경로에서 그대로 쓰인다.\n    if send_input_click(uia, el, \"osExpandCollapse\", double):\n        return True\n    try:\n        el.GetCurrentPattern(UIA_InvokePatternId).QueryInterface(mod.IUIAutomationInvokePattern).Invoke()\n        return True\n    except Exception:\n        pass\n    if toggle_item(mod, el, \"osExpandCollapse\"):\n        return True\n    try:\n        el.GetCurrentPattern(UIA_SelectionItemPatternId).QueryInterface(mod.IUIAutomationSelectionItemPattern).Select()\n        return True\n    except Exception:\n        pass\n    try:\n        legacy = el.GetCurrentPattern(UIA_LegacyIAccessiblePatternId).QueryInterface(mod.IUIAutomationLegacyIAccessiblePattern)\n        try:\n            legacy.Select(UIA_SELECTIONFLAG_TAKESELECTION)\n            return True\n        except Exception:\n            pass\n        legacy.DoDefaultAction()\n        return True\n    except Exception:\n        return False\n\n\ndef main():\n    ap = argparse.ArgumentParser()\n    ap.add_argument(\"--hwnd\", type=int, required=True)\n    # 2026-08-08: --ancestor-sel-b64 경로는 트리거 셀렉터(--sel-b64) 없이\n    # 조상 셀렉터만으로 동작하므로 더는 무조건 필수가 아니다 — 아래에서\n    # 어느 한쪽은 반드시 있어야 함을 직접 검증한다.\n    ap.add_argument(\"--sel-b64\", default=None)\n    ap.add_argument(\"--item-name-b64\", default=None)\n    # 2026-07-31: ComboBoxEx(HeidiSQL 네트워크 유형)처럼 항목 Name이 전부 빈\n    # owner-drawn 드롭다운은 이름으로 지목할 수 없다. 목록 안에서의 순서로만\n    # 구별 가능하므로 인덱스를 받는다(좌표가 아니라 구조적 순서 —\n    # ListItem/TreeItem/DataItem 슬롯 인덱스와 같은 원리).\n    ap.add_argument(\"--item-index\", type=int, default=None)\n    ap.add_argument(\"--item-count\", type=int, default=None)\n    # 2026-08-08: 이름 없는 요소를 \"가까운 이름 있는 조상 + 동일 ControlType\n    # 형제 순번\"으로 재탐색하는 경로 (agent.py의 _ancestor_sibling_selector가\n    # 기록). 팝업/콤보를 여는 게 아니라 이미 화면에 떠 있는 형제(툴바 버튼 등)를\n    # 고르는 것이므로, 아래 ecp/Expand() 로직 전체와는 무관한 별도 분기다.\n    ap.add_argument(\"--ancestor-sel-b64\", default=None)\n    ap.add_argument(\"--ancestor-item-ctrltype\", type=int, default=None)\n    # 2026-08-09: --ancestor-sel-b64 경로에서도 더블클릭이 필요해졌다\n    # (agent.py가 doubleClick 이벤트에도 ancestorSiblingIndex를 채우는\n    # 로컬 파일목록/트리 항목). 이 스크립트 자신의 argparser에는 이 플래그가\n    # 없었다 — 다른 임베드 스크립트(server.js 안의 별개 main())에서 본\n    # --double을 이 스크립트도 이미 가진 것으로 착각한 실수, 회귀로 드러남.\n    ap.add_argument(\"--double\", action=\"store_true\")\n    args = ap.parse_args()\n\n    if not args.hwnd:\n        print(\"osExpandCollapse: --hwnd is required\", file=sys.stderr)\n        sys.exit(2)\n    if not args.sel_b64 and not args.ancestor_sel_b64:\n        print(\"osExpandCollapse: either --sel-b64 or --ancestor-sel-b64 is required\", file=sys.stderr)\n        sys.exit(2)\n\n    enable_per_monitor_dpi()\n    comtypes.CoInitialize()\n    mod = comtypes.client.GetModule(\"UIAutomationCore.dll\")\n    uia = comtypes.client.CreateObject(\n        \"{ff48dba4-60ef-4201-aa87-54103eef594e}\", interface=mod.IUIAutomation\n    )\n\n    # 2026-07-24 실측(FileZilla 재현): 방금 그 hwnd에 WinAppDriver scoped\n    # session이 막 생성된 직후(재생 로그: \"scoped session on 0x301010 ready\n    # in 1354ms\" 다음 STEP에서 곧바로) 이 프로세스의 별도 IUIAutomation COM\n    # 클라이언트가 같은 hwnd에 ElementFromHandle()을 호출하면 간헐적으로\n    # COMError(-2147220991)가 난다 — WinAppDriver의 내부 UIA 클라이언트가 그\n    # 창에 이벤트 구독을 마치기 전 레이스로 추정(에러 메시지 자체가 \"이벤트\n    # 구독자를 불러올 수 없음\"). 셀렉터/로직 문제가 아니라 타이밍 문제이므로,\n    # 실패로 단정하기 전에 짧게 재시도한다(osScopedInvoke.py의 4회 재시도와\n    # 같은 근거).\n    root = None\n    for attempt in range(4):\n        if attempt > 0:\n            time.sleep(0.3)\n        try:\n            root = uia.ElementFromHandle(args.hwnd)\n        except Exception as e:\n            root = None\n            if attempt == 3:\n                print(f\"osExpandCollapse: ElementFromHandle failed: {e}\", file=sys.stderr)\n        if root:\n            break\n    if not root:\n        print(\"osExpandCollapse: ElementFromHandle failed\", file=sys.stderr)\n        sys.exit(2)\n\n    # 2026-08-08: 조상+형제인덱스 경로 — 트리거를 열 필요가 없는(이미 화면에\n    # 떠 있는) 이름 없는 요소를 재탐색한다. --sel-b64는 이 모드에서는\n    # 조상 자체의 셀렉터로 재사용된다(field_conds가 그대로 통함 — automationId/\n    # name/className 조합 매칭은 대상이 트리거든 조상이든 동일한 로직).\n    if args.ancestor_sel_b64:\n        anc_sel = json.loads(base64.b64decode(args.ancestor_sel_b64).decode(\"utf-8\"))\n        if args.item_index is None or args.ancestor_item_ctrltype is None:\n            print(\"osExpandCollapse: --item-index and --ancestor-item-ctrltype \"\n                  \"are required with --ancestor-sel-b64\", file=sys.stderr)\n            sys.exit(2)\n        ancestor = None\n        for attempt in range(10):   # 기존 target 재탐색과 같은 예산(10회, 300ms)\n            if attempt > 0:\n                time.sleep(0.3)\n            ancestor = resolve_target(uia, root, anc_sel)\n            if ancestor:\n                break\n        if not ancestor:\n            print(f\"osExpandCollapse: ancestor not found (sel={args.ancestor_sel_b64})\",\n                  file=sys.stderr)\n            sys.exit(2)\n        cond = uia.CreatePropertyCondition(UIA_ControlTypeProperty, args.ancestor_item_ctrltype)\n        # 2026-08-09/10 (형제 개수 널뛰기 조사, poc/probe_ancestor_chain.py\n        # --sample 실측): 폴더 진입/트리 펼치기 직후 조상의 자식 개수가 실제\n        # 값으로 안정되기까지 트리는 0.6~0.9s, 리스트(폴더 진입)는 최대 1.8s\n        # 걸리는 걸 직접 재현 확인(project→code-generator, 정확히 1800ms에서\n        # 수렴) — 캡처 시점 개수와 처음 한 번의 조회가 다르다고 바로 \"tree\n        # drifted\"로 포기하지 않고, agent.py의 _find_sibling_by_controltype()\n        # 과 동일한 예산(10회, 0.3s 간격 = 2.7s, 실측 1.8s에 여유를 둠)으로\n        # 재시도한다.\n        items = None\n        actual_count = 0\n        for attempt in range(10):\n            if attempt > 0:\n                time.sleep(0.3)\n            try:\n                items = ancestor.FindAll(TreeScope_Children, cond)  # agent.py와 스코프 일치 필수\n            except Exception as e:\n                print(f\"osExpandCollapse: FindAll under ancestor failed: {e}\", file=sys.stderr)\n                sys.exit(2)\n            actual_count = items.Length if items else 0\n            if not args.item_count or actual_count == args.item_count:\n                break\n        if args.item_count and actual_count != args.item_count:\n            print(f\"osExpandCollapse: {actual_count} matching siblings but the \"\n                  f\"recording saw {args.item_count} — refusing to pick by position \"\n                  \"(tree drifted since capture)\", file=sys.stderr)\n            sys.exit(2)\n        if not items or args.item_index >= items.Length:\n            print(f\"osExpandCollapse: sibling index {args.item_index} out of range \"\n                  f\"(0..{actual_count - 1})\", file=sys.stderr)\n            sys.exit(2)\n        target = items.GetElement(args.item_index)\n        if invoke_item(uia, mod, target, args.double):\n            print(f\"[osExpandCollapse] ancestor+index: invoked sibling \"\n                  f\"#{args.item_index}/{actual_count}\")\n            sys.exit(0)\n        print(\"osExpandCollapse: invoke_item failed on ancestor+index target\", file=sys.stderr)\n        sys.exit(2)\n\n    sel = json.loads(base64.b64decode(args.sel_b64).decode(\"utf-8\"))\n    # 2026-08-04 실측(HeidiSQL \"환경 설정\" -> \"파일 및 탭\" 탭 전환 직후 콤보\n    # 검색): 탭 클릭(osScopedInvoke, 별도 프로세스) 자체는 성공으로 보고되는데,\n    # 그 직후 이 프로세스가 즉시 자식 트리를 검색하면 그 탭의 컨트롤(TComboBox\n    # 등)이 아직 UIA 트리에 올라오지 않아 매번 \"target element not found\"였다\n    # — osScopedInvoke.py가 렌더링 레이스에 대해 이미 갖고 있는 재시도 예산\n    # (2026-07-17/24, \"새 사이트(N)\" 인라인 이름변경 상자와 같은 근거)을 이\n    # 헬퍼의 resolve_target()은 여태 갖고 있지 않았다. 클릭과 같은 예산(10회,\n    # 300ms 간격, 최대 ~2.7초)을 그대로 맞춘다.\n    target = None\n    for attempt in range(10):\n        if attempt > 0:\n            time.sleep(0.3)\n        target = resolve_target(uia, root, sel)\n        if target:\n            break\n    if not target:\n        print(f\"osExpandCollapse: target element not found (sel={args.sel_b64})\", file=sys.stderr)\n        sys.exit(2)\n\n    item_name = None\n    if args.item_name_b64:\n        item_name = base64.b64decode(args.item_name_b64).decode(\"utf-8\")\n\n    # 2026-07-23 실측(FileZilla \"네트워크 구성 마법사(N)...\" 재현): agent.py의\n    # expandCollapse 태깅은 UIA의 \"IsExpandCollapsePatternAvailable\" 구조적\n    # 응답만 보는데, wx는 서브메뉴가 없는 리프 커맨드 MenuItem에도 이걸 true로\n    # 보고하는 경우가 있다(실제 GetCurrentPattern()/Expand() 호출 시점에야\n    # 드러남 — 캡처 시점 검사와 재생 시점 COM 호출 결과가 다름). item_name이\n    # 없는 단독 토글 이벤트(병합 안 된 경우)에서 이게 벌어지면, 그 클릭은\n    # 원래 \"메뉴 펼치기\"가 아니라 \"커맨드 실행\"이었다는 뜻이므로, 실패로\n    # 끝내는 대신 평범한 클릭(Invoke/Select/LegacyIAccessible)으로 폴백해\n    # 실제 유저가 한 동작(메뉴 항목 실행)을 재현한다. item_name이 있는\n    # (병합된 진짜 서브메뉴) 경우는 mergeExpandCollapseClicks의 rootHwndHex\n    # 창-경계 가드가 이제 이런 리프 커맨드를 트리거로 병합하지 않으므로\n    # 여기까지 오지 않는다 — 그 경로는 기존처럼 실패로 남겨 무엇이 잘못됐는지\n    # 숨기지 않는다.\n    # 2026-08-05 (FileZilla 도움말(H) 메뉴 실측 — 2026-08-04 HeidiSQL \"더 보기\"로\n    # 이미 기록됐던 백로그 항목이 그대로 재현): 아래 두 폴백의 조건이\n    # \"not item_name\"뿐이라, **인덱스로 항목을 고르는 이벤트**(item_name이 항상\n    # None — 이름이 아니라 위치로 고르는 게 그 방식의 정의다)까지 \"이건 서브메뉴가\n    # 없는 리프 커맨드였다\"로 오판했다. 그 결과 args.item_index가 버젓이 있는데도\n    # 그 값을 한 번도 안 보고 트리거만 다시 클릭한 뒤 exit 0으로 \"성공\" 보고 —\n    # 실측 로그: \"3:select item #4 FileZilla 정보(A)...\" 스텝이\n    # \"clicked 도움말(H)\" + \"invoked as a plain command instead\"로 끝나고,\n    # 정작 \"FileZilla 정보\"는 눌리지 않았다(§3 거짓 성공).\n    #\n    # item_index가 있으면 이 폴백을 타면 안 된다. 그런데 단순히 실패시키는 것도\n    # 답이 아니다 — 패턴이 없을 뿐 **트리거를 클릭하면 메뉴는 실제로 열린다**\n    # (invoke_item이 하는 일이 바로 그것이고, 위 오판 케이스에서 메뉴가 열렸다는\n    # 사실 자체가 그 증거다). 그러니 클릭으로 메뉴를 연 뒤 아래 인덱스 기반\n    # pool 검색으로 이어가면 원래 의도대로 동작한다.\n    ecp = None\n    try:\n        ecp = target.GetCurrentPattern(UIA_ExpandCollapsePatternId).QueryInterface(\n            mod.IUIAutomationExpandCollapsePattern)\n    except Exception:\n        if args.item_index is None:\n            if not item_name and invoke_item(uia, mod, target):\n                print(\"[osExpandCollapse] ExpandCollapsePattern unavailable — invoked as a plain command instead\")\n                sys.exit(0)\n            print(\"osExpandCollapse: ExpandCollapsePattern not supported on target\", file=sys.stderr)\n            sys.exit(2)\n\n    # 네이티브 메뉴바 Expand()는 대상 창이 실제 OS 포그라운드여야 팝업을 연다\n    # (2026-08-08 실측 — force_foreground() 정의부 주석 참고). 클릭 없이 도는\n    # 패턴 API라 launchApp()의 별도-프로세스 활성화가 이미 끝난 뒤에는 그\n    # 효과가 사라져 있을 수 있으므로, 이 프로세스 안에서 바로 다시 강제한다.\n    force_foreground(args.hwnd)\n\n    # 새 팝업 창(네이티브 TrackPopupMenu 등) 감지용 베이스라인은 Expand() 전에\n    # 찍는다 — FileZilla 메뉴바처럼 하위 항목이 그 팝업 서브트리에만 생기는 경우.\n    baseline = set(top_windows())\n\n    if ecp is None:\n        # 패턴 없음 + 인덱스 있음: 트리거를 클릭해 메뉴를 열고 pool 검색으로 간다.\n        if not invoke_item(uia, mod, target):\n            print(\"osExpandCollapse: ExpandCollapsePattern unavailable and the \"\n                  \"trigger could not be clicked either — cannot open the list\",\n                  file=sys.stderr)\n            sys.exit(2)\n        print(\"[osExpandCollapse] ExpandCollapsePattern unavailable — opened the \"\n              \"list by clicking the trigger instead (index-based pick follows)\")\n        time.sleep(0.4)\n    else:\n        try:\n            if ecp.CurrentExpandCollapseState != ExpandCollapseState_Expanded:\n                ecp.Expand()\n            else:\n                ecp.Collapse()\n                time.sleep(0.2)\n                ecp.Expand()\n        except Exception as e:\n            if args.item_index is None and not item_name and invoke_item(uia, mod, target):\n                print(\"[osExpandCollapse] Expand() failed — invoked as a plain command instead\")\n                sys.exit(0)\n            if args.item_index is None:\n                print(f\"osExpandCollapse: Expand() failed: {e}\", file=sys.stderr)\n                sys.exit(2)\n            # 인덱스 기반 선택은 위와 같은 이유로 여기서 포기하지 않는다.\n            if not invoke_item(uia, mod, target):\n                print(f\"osExpandCollapse: Expand() failed ({e}) and the trigger \"\n                      \"could not be clicked either\", file=sys.stderr)\n                sys.exit(2)\n            print(f\"[osExpandCollapse] Expand() failed ({e}) — opened the list by \"\n                  \"clicking the trigger instead (index-based pick follows)\")\n        time.sleep(0.4)\n        try:\n            state = ecp.CurrentExpandCollapseState\n        except Exception:\n            state = None\n        print(f\"[osExpandCollapse] state after Expand() = {state}\")\n        # 2026-08-08 실측(FileZilla \"도움말(H)\" 메뉴, launchApp 직후 첫 액션):\n        # Expand()가 예외 없이 리턴했는데도 실제로는 펼쳐지지 않는 경우가 있었다\n        # (state 그대로 0, 새 팝업도 0개) — 네이티브 메뉴바가 막 뜬 창에서\n        # 프로그래매틱 Expand()에 응답하지 않는 것으로 보인다(포그라운드를\n        # 강제해도 재현됨 — force_foreground()로도 못 고침, 첫 액션 이후에는\n        # 재현되지 않음). 반면 같은 트리거를 물리 클릭(COM-SendInput)하면 이\n        # 시점에도 매번 열렸다 — Expand()가 \"에러 없음\"을 \"성공\"으로 오인하지\n        # 않도록, 실제 상태가 Expanded가 아니면 invoke_item()(물리 클릭 우선,\n        # 실패 시 Invoke/Select/Legacy 폴백)으로 한 번 더 열어본다.\n        if state != ExpandCollapseState_Expanded:\n            if invoke_item(uia, mod, target):\n                time.sleep(0.3)\n                try:\n                    state = ecp.CurrentExpandCollapseState\n                except Exception:\n                    pass\n                print(f\"[osExpandCollapse] Expand() reported no error but did not \"\n                      f\"actually expand — retried via physical click, state now = {state}\")\n\n    # ── 인덱스로 항목 선택 (2026-07-31, owner-drawn ComboBoxEx;\n    #    2026-08-04 확장: owner-drawn 팝업 메뉴, HeidiSQL \"더 보기\") ────────\n    # 항목 Name이 전부 빈 드롭다운/메뉴는 이름 조건으로 못 찾는다. 펼친 뒤\n    # 보이는 ListItem(콤보) 또는 MenuItem(팝업 메뉴)을 트리 순서대로 모아\n    # N번째를 실행한다. 창 서브트리와 새로 뜬 팝업 창을 모두 훑는다(Win32\n    # 콤보는 목록을 별도 ComboLBox 창에 그리기도 한다). 어느 컨트롤 타입인지\n    # 알려주는 별도 플래그는 없다 — 그 시점에 실제로 열려 있는 게 콤보든\n    # 메뉴든 둘 중 하나뿐이므로, 두 타입 다 후보 풀에 넣고 기존 루프가\n    # item_count 일치 여부로 걸러내게 둔다. 좌표는 쓰지 않는다.\n    if args.item_index is not None:\n        time.sleep(0.2)\n        li_cond = uia.CreatePropertyCondition(UIA_ControlTypeProperty, UIA_ListItem)\n        mi_cond = uia.CreatePropertyCondition(UIA_ControlTypeProperty, UIA_MenuItem)\n        pools = []\n        for cond, kind in ((li_cond, \"ListItem\"), (mi_cond, \"MenuItem\")):\n            try:\n                pools.append((f\"main window ({kind})\", root.FindAll(TreeScope_Subtree, cond)))\n            except Exception:\n                pass\n        # 2026-08-05 진단(FileZilla 도움말 메뉴 실측): STEP 1에서 pool이\n        # \"main window\" 두 건만 잡히고 popup 항목이 하나도 안 나왔다 —\n        # 즉 Expand() 뒤에도 baseline에 없던 새 최상위 창이 발견되지 않았다.\n        # 그게 (a) 팝업이 아예 안 열려서인지 (b) 이미 baseline에 있던\n        # 창이라 걸러진 건지 로그만으로 구분이 안 돼서, 후보 수를 남긴다.\n        _seen_new, _skipped_baseline = 0, 0\n        for h in top_windows():\n            if h in baseline:\n                _skipped_baseline += 1\n                continue\n            _seen_new += 1\n            try:\n                pr = uia.ElementFromHandle(h)\n            except Exception:\n                continue\n            if not pr:\n                continue\n            for cond, kind in ((li_cond, \"ListItem\"), (mi_cond, \"MenuItem\")):\n                try:\n                    pools.append((f\"popup hwnd={h} ({kind})\", pr.FindAll(TreeScope_Subtree, cond)))\n                except Exception:\n                    continue\n        print(f\"[osExpandCollapse] popup scan: {_seen_new} new top-level window(s) \"\n              f\"after opening, {_skipped_baseline} pre-existing skipped\")\n        for where, arr in pools:\n            if not arr or not arr.Length:\n                continue\n            if args.item_count and arr.Length != args.item_count:\n                print(f\"[osExpandCollapse] {where}: {arr.Length} items but the \"\n                      f\"recording saw {args.item_count} — the list changed since \"\n                      \"capture; refusing to pick by position\", file=sys.stderr)\n                continue\n            if args.item_index >= arr.Length:\n                print(f\"[osExpandCollapse] {where}: index {args.item_index} out of \"\n                      f\"range ({arr.Length} items)\", file=sys.stderr)\n                continue\n            item = arr.GetElement(args.item_index)\n            label = \"\"\n            try:\n                label = item.CurrentName or \"\"\n            except Exception:\n                pass\n            if invoke_item(uia, mod, item):\n                print(f\"[osExpandCollapse] selected item #{args.item_index} of \"\n                      f\"{arr.Length} in {where}\"\n                      + (f\" (name={label!r})\" if label else \" (unnamed item)\"))\n                sys.exit(0)\n        print(f\"osExpandCollapse: could not select item #{args.item_index} in any \"\n              \"open list\", file=sys.stderr)\n        sys.exit(2)\n\n    if not item_name:\n        # 항목 선택 없이 펼치기/접기 자체가 목적인 이벤트(예: 트리 +- 토글).\n        sys.exit(0)\n\n    item_cond = uia.CreatePropertyCondition(UIA_NameProperty, item_name)\n\n    # (a) 같은 창 서브트리에서 찾기 — PuTTY ComboBox처럼 드롭다운 항목이 세션\n    #     스코프 안에 있는 경우(2026-07-13 실측: 'SOCKS 5' 발견됨).\n    try:\n        item = root.FindFirst(TreeScope_Descendants, item_cond)\n    except Exception:\n        item = None\n    if item and invoke_item(uia, mod, item):\n        print(f\"[osExpandCollapse] invoked '{item_name}' under main window subtree\")\n        sys.exit(0)\n\n    # (b) Expand() 이후 새로 뜬 최상위 창 서브트리 — FileZilla 메뉴바처럼 하위\n    #     항목이 네이티브 팝업(#32768 등)에만 있는 경우.\n    time.sleep(0.2)\n    for h in top_windows():\n        if h in baseline:\n            continue\n        try:\n            popup_root = uia.ElementFromHandle(h)\n            if not popup_root:\n                continue\n            item = popup_root.FindFirst(TreeScope_Descendants, item_cond)\n            if item and invoke_item(uia, mod, item):\n                print(f\"[osExpandCollapse] invoked '{item_name}' under new popup hwnd={h}\")\n                sys.exit(0)\n        except Exception:\n            continue\n\n    print(f\"osExpandCollapse: item '{item_name}' not found under main window or any new popup window\", file=sys.stderr)\n    sys.exit(2)\n\n\nif __name__ == \"__main__\":\n    main()\n",
    'osScopedInvoke.py': "import os, sys, json, base64, argparse, ctypes, time\nfrom ctypes import wintypes\n\nif sys.stdout.encoding and sys.stdout.encoding.lower() not in (\"utf-8\", \"utf8\"):\n    sys.stdout.reconfigure(encoding=\"utf-8\", errors=\"replace\")\nif sys.stderr.encoding and sys.stderr.encoding.lower() not in (\"utf-8\", \"utf8\"):\n    sys.stderr.reconfigure(encoding=\"utf-8\", errors=\"replace\")\n\nimport comtypes\nimport comtypes.client\n\nUIA_NameProperty = 30005\nUIA_AutomationIdProperty = 30011\nUIA_ClassNameProperty = 30012\nUIA_ControlTypeProperty = 30003\nUIA_InvokePatternId = 10000\nUIA_SelectionItemPatternId = 10010\nUIA_ValuePatternId = 10002\nUIA_LegacyIAccessiblePatternId = 10018\n# 2026-07-23 실측(FileZilla Site Manager \"고급/전송 설정/문자셋/일반\" 카테고리\n# 탭 재현, controlType=50019=TabItem): wxWidgets의 커스텀-드로잉 Notebook 탭은\n# UIA TabItem으로 노출되지만 InvokePattern도 SelectionItemPattern도 실제로는\n# 구현하지 않는다(QueryInterface는 성공 조건에 따라 예외 없이 통과할 때도 있으나\n# Invoke()/Select() 호출 자체가 아무 효과 없이 조용히 끝나거나 예외를 던짐 —\n# 둘 다 두 로그에서 8회 재시도 전부 실패로 확인됨). wx는 MSAA(IAccessible) 쪽은\n# 안정적으로 구현하므로 LegacyIAccessiblePattern(Select 우선, 안 되면\n# DoDefaultAction)을 좌표 없는 마지막 폴백으로 시도한다 — 여전히 픽셀 좌표는\n# 전혀 쓰지 않는다(§3 Hard Rules 준수, UIA/MSAA 프로퍼티 기반 탐색만 사용).\nUIA_SELECTIONFLAG_TAKESELECTION = 1\n# 2026-07-17 (2차) 실측(FileZilla Site Manager 재생 타임스탬프 진단):\n# osScopedInvoke가 \"target not found\"로 보고한 실패 중 다수가 사실은 요소를\n# 매번 찾았는데(item=found) Invoke/SelectionItemPattern 둘 다 미지원이라\n# invoke_item()이 False를 반환한 것이었다(Tree 컨테이너 자체, Edit 필드 클릭 —\n# 둘 다 그 패턴들을 구조적으로 지원 안 함). 이런 컨트롤에 대한 \"클릭\"의 실제\n# 의도는 포커스 이동뿐이므로, 이 5종 + SetFocus 성공 시에만 성공으로 인정한다.\n# Button/MenuItem/TreeItem/ListItem 등 실제 실행 가능한 컨트롤은 이 목록에\n# 없으므로 여전히 Invoke/Select 성공을 요구한다(거짓 PASS 방지 — 2026-07-13\n# 3차 교훈: UIA 패턴 \"지원 여부\"만으로 태깅하면 과다신호가 되므로 실증된\n# ControlType으로만 좁힌다).\nPASSIVE_CONTROL_TYPES = {50004, 50030, 50033, 50018, 50023}  # Edit, Document, Pane, Tab, Tree\nUIA_ScrollPatternId = 10004\nTreeScope_Descendants = 4\n# Element(1)|Children(2)|Descendants(4) — TreeScope_Descendants alone can\n# never match the root element being searched from (UIA standard behavior),\n# so a captured click whose target IS the window itself (e.g. a dialog's own\n# className=\"#32770\" root, no automationId) is structurally unfindable with\n# Descendants-only scope. Confirmed 2026-07-16 (FileZilla Site Manager\n# dialog click failing \"target not found\" despite the window genuinely\n# being open) — use Subtree everywhere a target might be a window root.\nTreeScope_Subtree = 7\n\nuser32 = ctypes.windll.user32\n\n# ── dynamic ClickablePoint + SendInput (2026-07-24) ─────────────────────────\n# 녹화된 좌표는 여기 어디에도 들어오지 않는다. 좌표는 매 실행마다 UIA가 방금\n# resolve한 요소로부터 계산해 즉시 소비하고 버린다 — 창이 이동/리사이즈되거나\n# 해상도가 바뀌어도 항상 새로 계산되므로 §3 금지의 원래 취지(저장된 좌표가\n# 재생 시점에 어긋나는 것)를 건드리지 않는다.\nSM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77\nSM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79\nMOUSEEVENTF_MOVE = 0x0001\nMOUSEEVENTF_LEFTDOWN = 0x0002\nMOUSEEVENTF_LEFTUP = 0x0004\nMOUSEEVENTF_VIRTUALDESK = 0x4000\nMOUSEEVENTF_ABSOLUTE = 0x8000\nINPUT_MOUSE = 0\n\nULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong\n\n\nclass MOUSEINPUT(ctypes.Structure):\n    _fields_ = [(\"dx\", wintypes.LONG), (\"dy\", wintypes.LONG),\n                (\"mouseData\", wintypes.DWORD), (\"dwFlags\", wintypes.DWORD),\n                (\"time\", wintypes.DWORD), (\"dwExtraInfo\", ULONG_PTR)]\n\n\nclass _INPUTUNION(ctypes.Union):\n    _fields_ = [(\"mi\", MOUSEINPUT)]\n\n\nclass INPUT(ctypes.Structure):\n    _anonymous_ = (\"u\",)\n    _fields_ = [(\"type\", wintypes.DWORD), (\"u\", _INPUTUNION)]\n\n\ndef force_foreground(hwnd):\n    \"\"\"AttachThreadInput 트릭으로 포그라운드 잠금을 우회해 hwnd를 실제 OS\n    포그라운드/활성 창으로 올린다 — SIMPLE_HEADER의 osActivate.ps1\n    (WinActivate.Force)과 같은 로직이지만, 별도 프로세스를 띄우고 그 프로세스가\n    끝나길 기다리는 대신 이 프로세스 안에서 곧바로 이어지는 Expand()/Invoke()\n    호출과 같은 프로세스 수명 안에서 실행된다.\n    2026-08-08 실측(FileZilla \"도움말(H)\" 메뉴, launchApp 직후 첫 액션):\n    launchApp()이 별도 PowerShell 프로세스(osActivate)로 창을 활성화했는데도\n    Expand()가 매번 state=0(펼쳐지지 않음), 새 팝업 0개로 실패했다 — PowerShell\n    프로세스가 활성화 직후 종료되면 Windows가 포그라운드를 그 부모(터미널)에게\n    돌려주는 것으로 보여, 별도 프로세스가 끝나는 순간 활성화 효과가 사라진다.\n    실제 화면 클릭(COM-SendInput)을 거치는 스텝들은 클릭 좌표 아래 창이 자연히\n    포그라운드가 되므로 이 레이스를 겪지 않는다 — Expand()처럼 시각적 동작이\n    없는 패턴 API만 문제다.\n    \"\"\"\n    if not hwnd:\n        return\n    try:\n        SW_RESTORE = 9\n        user32.ShowWindow(hwnd, SW_RESTORE)\n        fg = user32.GetForegroundWindow()\n        fg_tid = user32.GetWindowThreadProcessId(fg, None)\n        me_tid = ctypes.windll.kernel32.GetCurrentThreadId()\n        attached = False\n        if fg_tid and fg_tid != me_tid:\n            attached = bool(user32.AttachThreadInput(me_tid, fg_tid, True))\n        user32.BringWindowToTop(hwnd)\n        user32.SetForegroundWindow(hwnd)\n        user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0003)   # HWND_TOPMOST, NOMOVE|NOSIZE\n        user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0003)   # HWND_NOTOPMOST\n        if attached:\n            user32.AttachThreadInput(me_tid, fg_tid, False)\n        time.sleep(0.15)\n    except Exception:\n        pass\n\n\ndef enable_per_monitor_dpi():\n    # agent.py의 _enable_per_monitor_dpi_awareness()와 동일한 근거로 필수:\n    # 파이썬 프로세스는 기본 DPI-unaware라 125% 스케일 환경에서 UIA가 돌려주는\n    # rect/ClickablePoint가 가상화된 논리 좌표로 오는 반면 SendInput의 절대\n    # 좌표계는 물리 픽셀이다 — 격상하지 않으면 두 좌표계가 어긋나 엉뚱한\n    # 지점을 클릭한다. UIA 객체를 만들기 전에 호출해야 한다.\n    try:\n        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))\n        return\n    except Exception:\n        pass\n    try:\n        ctypes.windll.shcore.SetProcessDpiAwareness(2)\n    except Exception:\n        pass\n\n\ndef clickable_point(el):\n    \"\"\"UIA가 지금 이 순간 계산한 클릭 지점. 실패하면 None.\"\"\"\n    try:\n        res = el.GetClickablePoint()\n    except Exception:\n        res = None\n    # comtypes는 [out] 파라미터 2개(POINT, BOOL)를 튜플로 돌려주지만 바인딩\n    # 버전에 따라 POINT 하나만 오는 경우도 있어 양쪽을 모두 받아준다.\n    if res is not None:\n        pt, got = (res if isinstance(res, (tuple, list)) and len(res) == 2 else (res, 1))\n        if got and pt is not None:\n            try:\n                return int(pt.x), int(pt.y)\n            except Exception:\n                pass\n    try:\n        r = el.CurrentBoundingRectangle\n        if r.right > r.left and r.bottom > r.top:\n            return (r.left + r.right) // 2, (r.top + r.bottom) // 2\n    except Exception:\n        pass\n    return None\n\n\ndef listitem_label_point(uia, el):\n    \"\"\"리포트 뷰 행에서 실제로 항목을 여는 지점 — 이름(첫 컬럼) 셀의 중심.\n\n    실측 2026-08-04 (7-Zip SysListView32, poc 프로브):\n        행(ListItem) rect = (797, 267, 1405, 286)   중심 x=1101\n        이름 셀(Edit)  rect = (800, 267,  833, 286)  중심 x=816\n        중심(1101) 더블클릭 -> 제목/행 목록 전부 불변 (아무 일도 안 일어남)\n        이름 셀(816) 더블클릭 -> 즉시 진입, 행 1개에서 25개로\n\n    행 rect는 모든 컬럼을 가로지르므로 그 중심은 \"수정한 날짜/크기\" 컬럼의\n    빈 공간이다. 선택(단일 클릭)은 full-row select라 어디를 눌러도 되지만\n    활성화(더블클릭)는 이름 셀에서만 일어난다 — 그래서 단일 클릭 스텝은\n    멀쩡했고 폴더 진입만 조용히 실패했다.\n\n    좌표를 저장하지 않는다: 재생 시점에 살아 있는 자식 요소의 rect에서\n    계산한다(§3의 dynamic ClickablePoint 규칙 그대로). 자식이 없는 행이면\n    None을 돌려 기존 clickable_point() 동작을 그대로 쓴다.\n    \"\"\"\n    TS_CHILDREN = 2\n    UIA_ListItem = 50007\n    try:\n        if el.CurrentControlType != UIA_ListItem:\n            return None\n    except Exception:\n        return None\n    try:\n        arr = el.FindAll(TS_CHILDREN, uia.CreateTrueCondition())\n    except Exception:\n        return None\n    best = None\n    for i in range(arr.Length if arr else 0):\n        try:\n            r = arr.GetElement(i).CurrentBoundingRectangle\n        except Exception:\n            continue\n        if r.right <= r.left or r.bottom <= r.top:\n            continue\n        # 가장 왼쪽 셀 = 이름 컬럼. 아이콘+라벨이 여기 있다.\n        if best is None or r.left < best[0]:\n            best = (r.left, (r.left + r.right) // 2, (r.top + r.bottom) // 2)\n    return (best[1], best[2]) if best else None\n\n\ndef _same_or_descendant(uia, ancestor, el, max_up=6):\n    cur = el\n    try:\n        walker = uia.RawViewWalker\n    except Exception:\n        walker = None\n    for _ in range(max_up + 1):\n        try:\n            if uia.CompareElements(ancestor, cur):\n                return True\n        except Exception:\n            return False\n        if not walker:\n            return False\n        try:\n            cur = walker.GetParentElement(cur)\n        except Exception:\n            return False\n        if not cur:\n            return False\n    return False\n\n\ndef send_input_click(uia, el, tag, double=False):\n    \"\"\"UIA로 방금 찾은 요소를 실제 마우스 입력으로 클릭한다.\n\n    안전 검증을 하나라도 통과 못 하면 사유를 남기고 False — 호출자는 기존\n    프로그래매틱 Invoke()/Select() 체인으로 폴백한다(에러로 튕기는 것보다\n    비시각적으로라도 동작하는 게 낫다는 2026-07-24 지시).\n    \"\"\"\n    def bail(reason):\n        print(\"[COM-SendInput] fallback: \" + reason + \" - using programmatic Invoke/Select\", file=sys.stderr)\n        return False\n\n    if os.environ.get(\"QAFORGE_COM_CLICK\") == \"invoke\":\n        return bail(\"forced-programmatic (QAFORGE_COM_CLICK=invoke)\")\n    try:\n        if el.CurrentIsOffscreen:\n            return bail(\"offscreen\")\n    except Exception:\n        pass\n\n    # 라벨은 반드시 주입 **전에** 읽는다. 메뉴 항목/다이얼로그 버튼은 클릭\n    # 즉시 파괴돼 그 뒤의 프로퍼티 읽기가 실패하고, 로그가 '?'로 남아 추적이\n    # 불가능해진다(2026-07-24 FileZilla 실측: 메뉴 항목/예(Y)/취소 전부 '?',\n    # 살아남는 콤보 화살표만 '닫기'로 정상 출력).\n    try:\n        label = el.CurrentName or el.CurrentAutomationId or \"?\"\n    except Exception:\n        label = \"?\"\n\n    # 리포트 뷰 행은 rect 중심이 빈 컬럼이라 더블클릭이 안 먹는다 —\n    # listitem_label_point() 참고 (2026-08-04 실측).\n    pt = listitem_label_point(uia, el) or clickable_point(el)\n    if not pt:\n        return bail(\"no-clickable-point\")\n    x, y = pt\n    vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)\n    vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)\n    vw = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)\n    vh = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)\n    if vw <= 1 or vh <= 1 or not (vx <= x < vx + vw and vy <= y < vy + vh):\n        return bail(\"point-outside-virtual-screen (%d,%d)\" % (x, y))\n\n    # 그 지점이 정말 대상 요소의 것인지 두 겹으로 확인한다. 2026-07-15에\n    # osScopedInvoke가 완전히 남남인 사용자 창(탐색기/VS Code)을 클릭하고\n    # \"성공\"으로 보고한 사고, 2026-07-13에 클릭 지점이 요소 rect 밖이라\n    # 물리적으로 no-op이던 이벤트가 재생 때는 요소 중심을 클릭해 엉뚱한 패널을\n    # 연 트랩 — 둘 다 이 검증으로 구조적으로 막힌다.\n    winpt = wintypes.POINT(int(x), int(y))\n    try:\n        hwnd_at = user32.WindowFromPoint(winpt)\n        pid_at = wintypes.DWORD()\n        user32.GetWindowThreadProcessId(hwnd_at, ctypes.byref(pid_at))\n        if pid_at.value != el.CurrentProcessId:\n            return bail(\"covered-by-other-window (pid %d at point, target pid %d)\"\n                        % (pid_at.value, el.CurrentProcessId))\n    except Exception as e:\n        return bail(\"window-hit-test-failed (%s)\" % e)\n\n    try:\n        at_point = uia.ElementFromPoint(winpt)\n    except Exception as e:\n        return bail(\"element-from-point-failed (%s)\" % e)\n    if not at_point or not _same_or_descendant(uia, el, at_point):\n        return bail(\"point-resolves-elsewhere\")\n\n    nx = int(round((x - vx) * 65535.0 / (vw - 1)))\n    ny = int(round((y - vy) * 65535.0 / (vh - 1)))\n\n    def send(flags):\n        inp = INPUT(type=INPUT_MOUSE)\n        inp.mi = MOUSEINPUT(nx, ny, 0, flags, 0, 0)\n        return user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))\n\n    # 사람이 눈으로 따라갈 수 있도록 이동/누름/뗌 사이에 간격을 둔다(§6).\n    if not send(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK):\n        return bail(\"SendInput(move) rejected\")\n    time.sleep(0.04)\n    if not send(MOUSEEVENTF_LEFTDOWN | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK):\n        return bail(\"SendInput(down) rejected\")\n    time.sleep(0.04)\n    send(MOUSEEVENTF_LEFTUP | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK)\n\n    # 두 번째 누름/뗌 — 시스템 더블클릭 간격(기본 500ms) 안에 보내야 앱이\n    # 더블클릭으로 인식한다. 여기 간격 합은 ~90ms.\n    #\n    # 2026-08-04: 이 분기가 없어서 녹화된 더블클릭이 앱에는 단일 클릭으로\n    # 도달했다. 네이티브 리스트 행에서 단일 클릭은 \"선택\"이지 \"열기\"가 아니다.\n    # 원래 설계는 InvokePattern.Invoke()(=기본 동작=열기)에 기대고 있었는데\n    # (server.js ListItem 분기 주석, 2026-07-15 실측), 2026-07-24에\n    # send_input_click()이 invoke_item() 체인 맨 앞에 붙으면서\n    # \"if send_input_click(...): return True\"가 되어 그 Invoke()가 도달 불가능한\n    # 죽은 코드가 됐다. 결과: 7-Zip에서 \"3:doubleClick 컴퓨터\"가 성공으로\n    # 보고되지만 폴더는 안 열리고, \"4:doubleClick C:\"부터 전부 무너진다\n    # (2026-08-04 두 번 연속 동일 재현).\n    if double:\n        time.sleep(0.05)\n        send(MOUSEEVENTF_LEFTDOWN | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK)\n        time.sleep(0.04)\n        send(MOUSEEVENTF_LEFTUP | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK)\n\n    verb = \" double-clicked '\" if double else \" clicked '\"\n    print(\"[COM-SendInput] \" + tag + verb + label + \"' at (%d,%d)\" % (x, y))\n    time.sleep(0.05)\n    return True\n\n\n# ── TogglePattern (2026-07-31) ─────────────────────────────────────────────\n# 실측(TeamViewer 15.79 WebView2, poc/diag_teamviewer_a11y_wakeup.py [D]):\n# 체크박스 2종의 지원 패턴은 정확히 {Toggle, Legacy}뿐 — Invoke도\n# SelectionItem도 없다. 기존 클릭 체인(Invoke → SelectionItem → Legacy)에는\n# Toggle이 아예 없어서 앞의 둘이 연달아 예외로 떨어진 뒤\n# Legacy.Select(TAKESELECTION)이 예외 없이 통과하며 True를 반환했다. 그런데\n# 체크박스에서 \"셀렉션\"은 체크 상태를 바꾸지 않으므로, 재생은 성공으로\n# 보고되는데 화면에서는 아무 일도 일어나지 않는다 — 그 체크박스가 띄우는\n# 확인 다이얼로그도 당연히 안 뜬다(2026-07-31 클라이언트 피드백 증상 그대로).\n# Legacy 폴백보다 앞서 Toggle을 시도하되, ToggleState가 실제로 바뀐 경우에만\n# 성공으로 인정한다(거짓 PASS 방지 — 2026-07-13 3차 교훈과 같은 원칙).\nUIA_TogglePatternId = 10015\n\n\ndef toggle_item(mod, el, tag):\n    try:\n        tp = el.GetCurrentPattern(UIA_TogglePatternId).QueryInterface(\n            mod.IUIAutomationTogglePattern)\n    except Exception:\n        return False\n    try:\n        before = tp.CurrentToggleState\n        tp.Toggle()\n        time.sleep(0.05)\n        after = tp.CurrentToggleState\n    except Exception as e:\n        print(\"[\" + tag + \"] TogglePattern.Toggle() raised: %s\" % e, file=sys.stderr)\n        return False\n    if after != before:\n        print(\"[\" + tag + \"] TogglePattern.Toggle() %d -> %d\" % (before, after))\n        return True\n    print(\"[\" + tag + \"] TogglePattern.Toggle() left ToggleState unchanged (%d)\"\n          \" - falling through to the remaining patterns\" % before, file=sys.stderr)\n    return False\n\n\n# ── checkbox 값-변경 검증 (2026-08-04) ───────────────────────────────────────\n# 위 toggle_item()은 invoke_item()의 체인 맨 끝(Legacy 폴백보다 앞)에서만\n# 쓰인다 — invoke_item()이 맨 앞에서 시도하는 send_input_click()(실제 화면\n# 클릭)이 성공하면 그 자리에서 곧바로 True를 반환하므로(§6 \"재생은 시각적으로\n# 확인 가능해야 한다\"는 요구를 만족하는 기본 경로), 대다수 체크박스 클릭은\n# toggle_item()까지 내려가지도 않는다. 그 결과 \"클릭 자체는 에러 없이 끝났다\"만\n# 보고 실제로 체크 상태가 바뀌었는지는 아무도 확인하지 않는다 — TeamViewer\n# WebView2 토글에서 실측된 것과 같은 종류의 거짓 PASS 위험이 CheckBox\n# controlType 전반에 구조적으로 남아 있다(HeidiSQL/PuTTY의 TCheckBox·Button\n# 스타일 체크박스도 이 경로를 그대로 탄다 — 2026-08-04 점검 시점엔 아직 실제\n# 재생 실패로 드러난 적은 없지만, 다음에 체크박스가 있는 녹화를 재생하면 언제든\n# 조용히 터질 수 있는 잠재적 구멍). 시각적 클릭은 그대로 유지하면서(§6), 클릭\n# 전후 ToggleState를 비교해 실제로 바뀌었는지만 추가로 검증한다 — 안 바뀌었으면\n# toggle_item()의 직접 Toggle() 호출로 한 번 더 보정 시도하고, 그래도 안 바뀌면\n# 정직하게 실패로 보고한다(호출부가 exit code로 판단해 _failures에 기록).\ndef verified_toggle_click(uia, mod, el, tag=\"osScopedInvoke\", double=False):\n    try:\n        tp = el.GetCurrentPattern(UIA_TogglePatternId).QueryInterface(\n            mod.IUIAutomationTogglePattern)\n    except Exception:\n        # TogglePattern이 없다 = 이 컨트롤은 애초에 체크박스가 아니다(예:\n        # CheckBox로 잘못 태깅됐거나 캡처 시점 이후 컨트롤이 바뀐 경우) —\n        # 검증할 상태 자체가 없으므로 평범한 클릭으로 폴백한다. 없는 걸\n        # 있다고 우기며 거짓 실패를 만들지 않는다.\n        return invoke_item(uia, mod, el, double)\n    try:\n        before = tp.CurrentToggleState\n    except Exception:\n        return invoke_item(uia, mod, el, double)\n    if not invoke_item(uia, mod, el, double):\n        return False\n    time.sleep(0.05)\n    try:\n        after = tp.CurrentToggleState\n    except Exception as e:\n        print(f\"[{tag}] click succeeded but ToggleState could not be \"\n              f\"re-read afterward ({e}) — cannot verify, trusting the click\", file=sys.stderr)\n        return True\n    if after != before:\n        print(f\"[{tag}] checkbox toggled {before} -> {after} (verified)\")\n        return True\n    print(f\"[{tag}] click reported success but ToggleState stayed {before} \"\n          \"unchanged — retrying via TogglePattern.Toggle() directly\", file=sys.stderr)\n    return toggle_item(mod, el, tag)\n\n\ndef top_windows():\n    found = []\n\n    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)\n    def cb(hwnd, _):\n        if user32.IsWindowVisible(hwnd):\n            found.append(hwnd)\n        return True\n\n    user32.EnumWindows(cb, 0)\n    return found\n\n\ndef window_title(hwnd):\n    n = user32.GetWindowTextLengthW(hwnd)\n    buf = ctypes.create_unicode_buffer(n + 1)\n    user32.GetWindowTextW(hwnd, buf, n + 1)\n    return buf.value\n\n\ndef owner_window_for(main_h, main_pid, owner_title):\n    \"\"\"The same-PID top-level window this event was actually recorded in.\n\n    Cross-window steps used to search the MAIN window first and only then\n    other top-level windows. That is wrong whenever the recorded selector is\n    weak enough to also match something in the main window. Measured\n    2026-08-03 (PuTTY): step '13:click 닫기' belongs to the 'PuTTY User\n    Manual' window and carries NO AutomationId and NO ClassName, so its\n    selector is the bare name 닫기 — which the main PuTTY Configuration\n    window ALSO contains (two ComboBox DropDown arrows are named 닫기, and a\n    Korean titlebar Close button is Button[@Name=\"닫기\"] as well). The main\n    window search matched 2 elements, picked one by recorded position, and\n    closed PuTTY Configuration — after which every later step failed with\n    click-not-found.\n\n    Returning the recorded window here lets the caller search it FIRST. Match\n    on containment because titles carry volatile suffixes; skip when the hint\n    matches the main window (the ordinary same-window case) so nothing about\n    the existing path changes there.\n    \"\"\"\n    if not owner_title:\n        return None\n    main_title = window_title(main_h)\n    if owner_title in main_title or main_title in owner_title:\n        return None\n    for h in top_windows():\n        if h == main_h:\n            continue\n        cand_pid = wintypes.DWORD()\n        user32.GetWindowThreadProcessId(h, ctypes.byref(cand_pid))\n        if cand_pid.value != main_pid:\n            continue\n        if owner_title in window_title(h):\n            return h\n    return None\n\n\ndef resolve_cond(uia, sel):\n    conds = []\n    if sel.get(\"automationId\"):\n        conds.append(uia.CreatePropertyCondition(UIA_AutomationIdProperty, sel[\"automationId\"]))\n    if sel.get(\"name\"):\n        conds.append(uia.CreatePropertyCondition(UIA_NameProperty, sel[\"name\"]))\n    if sel.get(\"className\"):\n        conds.append(uia.CreatePropertyCondition(UIA_ClassNameProperty, sel[\"className\"]))\n    # 2026-08-10 (FileZilla \"C:\" 트리 vs 리스트 혼동 실측): comSafeTarget()이\n    # automationId/className 둘 다 없을 때만 controlTypeId를 싣는다 — 같은\n    # 화면에 같은 Name의 owner-drawn 컨트롤이 둘 이상(TreeItem/ListItem) 있을\n    # 때 FindFirst가 아무거나 먼저 걸리는 걸 막는 추가 AND 조건.\n    if sel.get(\"controlTypeId\") is not None:\n        conds.append(uia.CreatePropertyCondition(\n            UIA_ControlTypeProperty, int(sel[\"controlTypeId\"])))\n    if not conds:\n        return None\n    cond = conds[0]\n    for c in conds[1:]:\n        cond = uia.CreateAndCondition(cond, c)\n    # 2026-08-08 실측 (FileZilla \"파일 검색\" 창 STEP 37 \"click 닫기\"): 그\n    # 창의 타이틀바 닫기 버튼은 automationId/className이 전혀 없어 셀렉터가\n    # Name=\"닫기\" 단독이 된다. 같은 창 안의 ComboBox 드롭다운 화살표\n    # (automationId=\"DropDown\")도 펼쳐진 상태에서는 Name이 \"닫기\"로 바뀐다\n    # (owner_window_for()의 2026-08-03 PuTTY 코멘트에 이미 같은 충돌이\n    # 기록돼 있음). 이전 스텝들이 드롭다운 3개를 펼쳐놓은 채였던 이번 케이스는\n    # FindFirst가 그 화살표 중 하나를 먼저 찾아 \"성공\"으로 보고했지만 실제\n    # 창은 닫히지 않았다. 드롭다운 화살표는 자기 자신을 가리킬 때 항상\n    # automationId=\"DropDown\"을 셀렉터에 싣는다(comSafeTarget의\n    # isComboDropDownArrow) — 그러니 automationId/className이 전혀 없는\n    # (즉 진짜로 모호한) 순수 Name 매칭에서만 그 화살표들을 후보에서 제외한다.\n    if not sel.get(\"automationId\") and not sel.get(\"className\") and sel.get(\"name\"):\n        try:\n            not_dropdown = uia.CreateNotCondition(\n                uia.CreatePropertyCondition(UIA_AutomationIdProperty, \"DropDown\"))\n            cond = uia.CreateAndCondition(cond, not_dropdown)\n        except Exception:\n            pass\n    return cond\n\n\n# 2026-07-24: osExpandCollapse.py와 동일한 근거로 이식 — 이 파일의 COM 경로\n# (owned 다이얼로그/새 팝업창의 좁은 예외)에는 WAD의 암묵적 오프스크린\n# 스크롤-인-뷰가 없다. Invoke/Select/LegacyIAccessible 시도 전에 조상\n# ScrollPattern으로 한 번 끌어와 본다 — 좌표는 쓰지 않는다(§3).\ndef find_scrollable_ancestor(uia, el, max_up=8):\n    try:\n        walker = uia.RawViewWalker\n    except Exception:\n        return None\n    cur = el\n    for _ in range(max_up):\n        try:\n            cur.GetCurrentPattern(UIA_ScrollPatternId)\n            return cur\n        except Exception:\n            pass\n        try:\n            cur = walker.GetParentElement(cur)\n            if not cur:\n                return None\n        except Exception:\n            return None\n    return None\n\n\ndef ensure_visible(uia, mod, el):\n    try:\n        if not el.CurrentIsOffscreen:\n            return\n    except Exception:\n        return\n    print(\"[osScopedInvoke] target is offscreen — attempting scroll-into-view via ancestor ScrollPattern\", file=sys.stderr)\n    ancestor = find_scrollable_ancestor(uia, el)\n    if not ancestor:\n        return\n    try:\n        sp = ancestor.GetCurrentPattern(UIA_ScrollPatternId).QueryInterface(mod.IUIAutomationScrollPattern)\n        sp.SetScrollPercent(50.0, 50.0)\n        time.sleep(0.2)\n    except Exception:\n        pass\n\n\ndef invoke_item(uia, mod, el, double=False):\n    ensure_visible(uia, mod, el)\n    focus_ok = False\n    try:\n        el.SetFocus()\n        focus_ok = True\n    except Exception:\n        pass\n    # 시각적 재생 우선(2026-07-24, §6) — 성공하면 반드시 여기서 반환한다.\n    # 이어서 Invoke()까지 부르면 같은 동작이 두 번 실행된다.\n    # double=True면 두 번 누른다 — 아래 Invoke() 폴백은 기본 동작(=열기)이라\n    # 더블클릭 의미를 그대로 만족하므로 폴백 체인은 손대지 않는다.\n    if send_input_click(uia, el, \"osScopedInvoke\", double):\n        return True\n    try:\n        el.GetCurrentPattern(UIA_InvokePatternId).QueryInterface(mod.IUIAutomationInvokePattern).Invoke()\n        return True\n    except Exception:\n        pass\n    if toggle_item(mod, el, \"osScopedInvoke\"):\n        return True\n    try:\n        el.GetCurrentPattern(UIA_SelectionItemPatternId).QueryInterface(mod.IUIAutomationSelectionItemPattern).Select()\n        return True\n    except Exception:\n        pass\n    try:\n        legacy = el.GetCurrentPattern(UIA_LegacyIAccessiblePatternId).QueryInterface(mod.IUIAutomationLegacyIAccessiblePattern)\n        try:\n            legacy.Select(UIA_SELECTIONFLAG_TAKESELECTION)\n            return True\n        except Exception:\n            pass\n        legacy.DoDefaultAction()\n        return True\n    except Exception:\n        pass\n    try:\n        ctrl_type = el.CurrentControlType\n    except Exception:\n        ctrl_type = None\n    if focus_ok and ctrl_type in PASSIVE_CONTROL_TYPES:\n        return True\n    print(f\"[osScopedInvoke] found element (controlType={ctrl_type}) but no actionable pattern (Invoke/Select/LegacyIAccessible) succeeded\", file=sys.stderr)\n    return False\n\n\n# 2026-07-24 실측(PuTTY): 콤보박스 드롭다운 화살표의 automationId는 창 안의\n# 모든 콤보가 \"DropDown\"으로 공유한다 — 상태 의존 Name(\"닫기\")은 2026-07-14\n# 가드가 이미 떼어내므로 AND 조건으로 좁힐 수도 없다. FindFirst는 트리의 첫\n# 번째(= Translation 패널의 \"Remote character set\") 화살표만 계속 잡았고,\n# Proxy 패널 항목을 고르는 스텝 두 개가 \"같은 콤보를 반복해서 여는\" 증상으로\n# 실패했다(재생 로그가 매번 동일한 좌표 (1223,412)를 찍은 것이 증거).\n# PuTTY류 다이얼로그는 비활성 패널의 컨트롤을 화면에서 내리므로, 후보가\n# 여럿이면 offscreen이 아닌 것을 고른다. 여러 후보를 차례로 클릭해보는 방식은\n# 쓰지 않는다 — 실패한 후보가 드롭다운을 열어둔 채로 남아 다음 후보 클릭이\n# 그 팝업 해제에 먹히기 때문.\ndef elements_from(arr):\n    cands = []\n    if not arr:\n        return cands\n    for i in range(arr.Length):\n        try:\n            cands.append(arr.GetElement(i))\n        except Exception:\n            pass\n    return cands\n\n\n# 2026-07-29 (HeidiSQL: 두 콤보박스의 드롭다운 화살표가 automationId=\"DropDown\"을\n# 공유): pick_trigger의 기존 \"offscreen 제외 후 첫 번째\" 방식은 PuTTY 전용\n# 가정(비활성 패널 컨트롤이 화면 밖으로 밀려남)에 의존한다 — HeidiSQL은 두\n# 콤보가 동시에 진짜로 화면에 떠 있어 이 필터가 아무 것도 못 거른다. 캡처된\n# window-relative Y(relY)를 현재 창의 실제 위치에 매 실행마다 다시 더해\n# 절대 Y로 변환하고, 후보들 중 그 위치에 가장 가까운 rect.top을 가진 걸\n# 고른다 — 저장된 화면 좌표를 클릭에 쓰는 게 아니라(§3 금지 대상과 다름),\n# 이미 구조적으로 찾아낸 후보들 사이에서 어느 걸 고를지 정하는 신호로만\n# 쓴다(§3 2026-07-24 재정의: 매 실행 재계산·즉시 소비·저장 안 함 — 이미\n# 승인된 dynamic ClickablePoint 예외와 같은 성격).\ndef pick_by_position(cands, win_top, rel_y):\n    if not cands:\n        return None\n    if len(cands) == 1 or win_top is None or rel_y is None:\n        return cands[0]\n    target_y = win_top + rel_y\n    best, best_dist = None, None\n    for c in cands:\n        try:\n            r = c.CurrentBoundingRectangle\n            d = abs(r.top - target_y)\n        except Exception:\n            continue\n        if best_dist is None or d < best_dist:\n            best, best_dist = c, d\n    return best if best is not None else cands[0]\n\n\ndef pick_trigger(uia, root, cond, win_top=None, rel_y=None):\n    try:\n        arr = root.FindAll(TreeScope_Subtree, cond)\n    except Exception:\n        arr = None\n    cands = elements_from(arr)\n    if not cands:\n        return None\n    if len(cands) == 1:\n        return cands[0]\n    visible = []\n    for c in cands:\n        try:\n            if not c.CurrentIsOffscreen:\n                visible.append(c)\n        except Exception:\n            visible.append(c)\n    pool = visible or cands\n    if win_top is not None and rel_y is not None:\n        picked = pick_by_position(pool, win_top, rel_y)\n        if picked is not None:\n            print(f\"[osScopedInvoke] trigger selector matched {len(cands)} elements \"\n                  f\"({len(visible)} on screen) — disambiguated by recorded position\")\n            return picked\n    print(f\"[osScopedInvoke] WARN trigger selector matched {len(cands)} elements \"\n          f\"({len(visible)} on screen) — the automationId is reused across \"\n          f\"controls; picking the first on-screen one\")\n    return pool[0]\n\n\n# 2026-07-17: owned 다이얼로그(WAD가 scoped session을 거부하는 창)에 타이핑하기\n# 위한 COM 경로. 기존에는 getWindowSession()이 owned 창을 만나면 WinAppDriver\n# Root 세션 REST로 전체 데스크톱 XPath 검색을 폴백으로 썼는데, 실측(2026-07-17\n# FileZilla 다이얼로그 진단): 이 Root-세션 REST 호출은 쿼리 내용/매치 여부와\n# 무관하게 매번 15~20초가 걸린다(빈 결과조차 15.6초) — WinAppDriver 3.5.2의\n# Root 세션 자체가 모든 element 조회에 고정 비용을 갖는 것으로 보임. hwnd는\n# 이미 EnumWindows로 알고 있으므로, 같은 COM 스택(osScopedInvoke의 클릭 경로와\n# 동일)으로 즉시 타이핑하면 이 15~20초를 완전히 우회한다.\n# 2026-07-24 실측(poc/diag_filezilla_rename.py, FileZilla Site Manager):\n# \"새 사이트(N)\" 직후 뜨는 인라인 이름변경 상자는\n#   - 캡처 시점에는 automationId=\"1\"로 기록되지만\n#   - 재생 시점의 라이브 요소는 automationId=\"\" (name도 \"\")\n# 즉 그 id는 런타임에 변하는 값이라 셀렉터로 절대 매칭되지 않는다(ListItem\n# 슬롯 인덱스와 같은 부류). 대신 이 상자는 **나타나는 순간 항상 키보드 포커스를\n# 가진다**(인라인 rename의 정의상 그렇다) — 좌표를 쓰지 않고 이 상자를 집는\n# 가장 신뢰할 수 있는 방법이다. 타이핑 대상을 못 찾았을 때만, 그리고 포커스\n# 요소가 우리 프로세스의 입력 컨트롤일 때만 쓴다(남의 창에 타이핑 방지).\nINPUT_CONTROL_TYPES = {50004, 50030, 50003}  # Edit, Document, ComboBox\n\n\ndef focused_input(uia, main_pid):\n    try:\n        el = uia.GetFocusedElement()\n    except Exception:\n        return None\n    if not el:\n        return None\n    try:\n        if el.CurrentProcessId != main_pid:\n            return None\n        if el.CurrentControlType not in INPUT_CONTROL_TYPES:\n            return None\n        aid = el.CurrentAutomationId\n        cls = el.CurrentClassName\n    except Exception:\n        return None\n    print(f\"[osScopedInvoke] target not found — falling back to the focused \"\n          f\"input control (id='{aid}' class='{cls}'); an inline rename box \"\n          f\"exposes a runtime-varying AutomationId, so focus is the stable handle\")\n    return el\n\n\ndef type_item(uia, mod, el, text):\n    ensure_visible(uia, mod, el)\n    try:\n        el.SetFocus()\n    except Exception:\n        pass\n    # 2026-07-17 (2차) 실측: 인라인 이름변경 편집 상자에 입력된 값은 물리\n    # 캡처에서 트레일링 개행(\"d\\n\")을 포함한다(사용자가 Enter로 확정) — 이걸\n    # 그대로 SetValue에 넣으면 개행이 리터럴 문자로 들어갈 뿐 Enter 키로\n    # 동작하지 않아 편집 상자가 미확정 상태로 남고, 그 상태에서 같은\n    # 다이얼로그의 다른 서브트리(탭 패널 등)가 UIA 검색에서 안 잡히는 게\n    # 실측으로 확인됨(FileZilla Site Manager). 트레일링 개행은 스트립 —\n    # 실제 Enter 키 주입은 이 상태에서 어디까지 필요한지 GUI 재검증 후 별도로.\n    value = text[:-1] if text.endswith('\\n') else text\n    try:\n        el.GetCurrentPattern(UIA_ValuePatternId).QueryInterface(mod.IUIAutomationValuePattern).SetValue(value)\n        return True\n    except Exception:\n        return False\n\n\ndef main():\n    ap = argparse.ArgumentParser()\n    ap.add_argument(\"--hwnd\", type=int, required=True)\n    ap.add_argument(\"--sel-b64\", required=True)\n    ap.add_argument(\"--trigger-sel-b64\", default=None)\n    ap.add_argument(\"--text-b64\", default=None)\n    # 2026-07-29 (HeidiSQL DropDown 재사용): 구조적으로 여러 후보가 걸릴 때\n    # 어느 걸 고를지에만 쓰는 위치 힌트 — 클릭 좌표 자체가 아니다(§3 참고,\n    # pick_by_position 주석). rel-y/trigger-rel-y는 캡처 시점의 창-상대 Y이고,\n    # 매 실행 현재 창 위치에 다시 더해서만 쓴다.\n    ap.add_argument(\"--rel-y\", type=int, default=None)\n    ap.add_argument(\"--trigger-rel-y\", type=int, default=None)\n    # 이 이벤트가 실제로 녹화된 창의 제목 — owner_window_for() 참고.\n    ap.add_argument(\"--owner-title-b64\", default=None)\n    # 녹화된 동작이 더블클릭이었는가. 네이티브 리스트 행에서 단일 클릭은\n    # 선택일 뿐 열기가 아니라 이 구분이 필요하다 (2026-08-04).\n    ap.add_argument(\"--double\", action=\"store_true\")\n    # 체크박스 클릭 전후 ToggleState를 비교해 실제로 바뀌었는지 검증한다\n    # (2026-08-04, verified_toggle_click 참고 — CheckBox controlType 전반의\n    # 잠재적 거짓 PASS 구멍을 막는다).\n    ap.add_argument(\"--verify-toggle\", action=\"store_true\")\n    args = ap.parse_args()\n\n    enable_per_monitor_dpi()\n    comtypes.CoInitialize()\n    mod = comtypes.client.GetModule(\"UIAutomationCore.dll\")\n    uia = comtypes.client.CreateObject(\n        \"{ff48dba4-60ef-4201-aa87-54103eef594e}\", interface=mod.IUIAutomation\n    )\n\n    main_h = args.hwnd\n    if not main_h:\n        print(\"osScopedInvoke: --hwnd is required\", file=sys.stderr)\n        sys.exit(2)\n    # 2026-07-24: osExpandCollapse.py와 동일한 방어 — 방금 그 hwnd에\n    # WinAppDriver scoped session이 막 생성된 직후 이 프로세스의 별도\n    # IUIAutomation COM 클라이언트가 ElementFromHandle()을 호출하면 간헐적으로\n    # COMError(-2147220991, 이벤트 구독자 관련)가 실측됨 — 셀렉터/로직 문제가\n    # 아니라 타이밍 레이스이므로 실패 단정 전에 짧게 재시도한다.\n    root = None\n    for attempt in range(4):\n        if attempt > 0:\n            time.sleep(0.3)\n        try:\n            root = uia.ElementFromHandle(main_h)\n        except Exception:\n            root = None\n        if root:\n            break\n    if not root:\n        print(\"osScopedInvoke: ElementFromHandle failed\", file=sys.stderr)\n        sys.exit(2)\n\n    win_top = None\n    try:\n        r = wintypes.RECT()\n        if user32.GetWindowRect(main_h, ctypes.byref(r)):\n            win_top = r.top\n    except Exception:\n        pass\n\n    sel = json.loads(base64.b64decode(args.sel_b64).decode(\"utf-8\"))\n    item_cond = resolve_cond(uia, sel)\n    if item_cond is None:\n        print(\"osScopedInvoke: selector has no usable fields\", file=sys.stderr)\n        sys.exit(2)\n\n    # 트리거(버튼 등)가 있으면 이 실행 안에서 먼저 클릭 — 별도 프로세스로\n    # 쪼개 두 번 호출하지 않아 트리거-검색 사이의 지연(및 그로 인한 드롭다운\n    # 자동-닫힘)을 없앤다.\n    if args.trigger_sel_b64:\n        trigger_sel = json.loads(base64.b64decode(args.trigger_sel_b64).decode(\"utf-8\"))\n        trigger_cond = resolve_cond(uia, trigger_sel)\n        if trigger_cond is not None:\n            trigger = pick_trigger(uia, root, trigger_cond, win_top, args.trigger_rel_y)\n            if trigger:\n                invoke_item(uia, mod, trigger)\n            else:\n                # 트리거를 못 찾으면 드롭다운이 아예 안 열려 이후 아이템\n                # 검색이 원인불명으로 실패하는 것처럼 보인다 — 눈에 보이게\n                # 남긴다 (2026-07-14, 침묵 스킵이 진단을 어렵게 만든 것을 확인).\n                print(f\"[osScopedInvoke] WARN trigger not found (sel={args.trigger_sel_b64}) — dropdown likely never opened\")\n\n    # --text-b64가 있으면 클릭/Invoke 대신 타이핑(ValuePattern.SetValue) —\n    # osScopedType() JS wrapper 전용 (2026-07-17, owned 다이얼로그 안 Edit\n    # 컨트롤에 타이핑하기 위해 도입 — Root 세션 REST 폴백의 15~20초 고정\n    # 비용을 피한다. 검색 로직((a)(b) 둘 다)은 클릭과 완전히 동일).\n    act = (lambda el: type_item(uia, mod, el, base64.b64decode(args.text_b64).decode(\"utf-8\")))         if args.text_b64 else (\n            (lambda el: verified_toggle_click(uia, mod, el, \"osScopedInvoke\", args.double))\n            if args.verify_toggle else (lambda el: invoke_item(uia, mod, el, args.double))\n        )\n    verb = 'typed into' if args.text_b64 else 'invoked'\n\n    # 최대 4회 시도(즉시 1회 + 300ms 간격 재시도 3회, 총 최대 ~0.9초) — 2026-07-17\n    # 실측: \"새 사이트(N)\" 클릭 직후 뜨는 인라인 이름변경 상자(automationId=\"1\")를\n    # 즉시 1회만 찾으면 렌더링 레이스로 못 찾는 경우가 실제 GUI에서 재현됨\n    # (FileZilla Site Manager). 기존 REST 경로(_findScoped)는 1초 간격으로 최대\n    # 8초 폴링해 이런 레이스를 자연히 흡수했는데, COM 경로는 단발 시도라 그\n    # 여유가 없었다 — _step()의 범용 Fail-and-Recover(ESC)에 기대면 이름변경\n    # 상자에서 ESC가 변경 자체를 취소시켜 재시도도 함께 실패하므로(esc-recovery\n    # 후 osScopedType 재실패로 실측 확인), 스크립트 자체에 짧은 재시도를 둔다.\n    # 2026-07-24 실측: FileZilla 이름변경 상자는 \"새 사이트(N)\" 클릭 후\n    # **2260ms**만에 나타났다(poc/diag_filezilla_rename.py) — 기존 예산\n    # 4회(~0.9초)로는 구조적으로 못 잡는다. 타이핑은 20회(~6초)까지 기다리고\n    # (REST 경로 _findScoped의 8초 폴링보다는 짧게), 클릭은 10회(~2.7초)로\n    # 둔다. _step()의 ESC 복구는 이 대상에서 오히려 해로워 이제 건너뛰므로\n    # (_escWouldHarm) 재시도 여유는 이 스크립트 안에서 확보해야 한다.\n    attempts = 20 if args.text_b64 else 10\n    main_pid = wintypes.DWORD()\n    user32.GetWindowThreadProcessId(main_h, ctypes.byref(main_pid))\n    owner_title = (base64.b64decode(args.owner_title_b64).decode(\"utf-8\")\n                   if args.owner_title_b64 else \"\")\n    for attempt in range(attempts):\n        if attempt > 0:\n            time.sleep(0.3)\n\n        # (a0) 이 이벤트가 녹화된 창이 메인 창이 아니라면 거기서 먼저 찾는다.\n        #      약한 셀렉터(이름 단독)가 메인 창의 엉뚱한 동명 컨트롤에 먼저\n        #      걸리는 것을 막는다 — owner_window_for() 주석의 PuTTY 닫기 사례.\n        owner_h = owner_window_for(main_h, main_pid.value, owner_title)\n        if owner_h:\n            try:\n                owner_root = uia.ElementFromHandle(owner_h)\n                if owner_root:\n                    item = owner_root.FindFirst(TreeScope_Subtree, item_cond)\n                    if item and act(item):\n                        print(f\"[osScopedInvoke] {verb} under the window it was \"\n                              f\"recorded in ({owner_title!r} hwnd={owner_h})\")\n                        sys.exit(0)\n            except Exception:\n                pass\n\n        # (a) 메인 창 서브트리. Subtree = 창 자기 자신(root)도 포함해 검색한다 —\n        #     Descendants만 쓰면 캡처된 타겟이 창 자체(예: className=\"#32770\")인\n        #     경우 구조적으로 못 찾는다(2026-07-16 FileZilla 다이얼로그 클릭 확인).\n        # rel_y 힌트가 있으면 FindAll로 모든 후보를 모아 위치로 가려낸다\n        # (2026-07-29, HeidiSQL: 같은 창 안에 automationId=\"DropDown\"을\n        # 공유하는 콤보가 둘 이상 — FindFirst는 항상 같은 하나만 반환).\n        # 힌트가 없으면 기존과 동일하게 FindFirst 한 건만 본다.\n        item = None\n        try:\n            if args.rel_y is not None:\n                cands = elements_from(root.FindAll(TreeScope_Subtree, item_cond))\n                if len(cands) > 1:\n                    picked = pick_by_position(cands, win_top, args.rel_y)\n                    print(f\"[osScopedInvoke] selector matched {len(cands)} elements in main \"\n                          f\"window — disambiguated by recorded position\")\n                    item = picked\n                elif cands:\n                    item = cands[0]\n            else:\n                item = root.FindFirst(TreeScope_Subtree, item_cond)\n        except Exception:\n            item = None\n        if item and act(item):\n            print(f\"[osScopedInvoke] {verb} under main window subtree\")\n            sys.exit(0)\n\n        # (b) 메인 창과 같은 프로세스(PID)가 소유한 다른 최상위 창 서브트리 —\n        #     이미 열려 있는 팝업/드롭다운(예: PuTTY의 ComboLBox, FileZilla\n        #     메뉴)을 잡는다. 새로 뜬 창인지 여부는 따지지 않는다(트리거가\n        #     이미 직전 스텝에서 실행됐으므로 baseline diff 불필요). PID로\n        #     반드시 한정한다 — PID 무관하게 데스크톱 전체를 뒤지면 완전히\n        #     남남인 창을 잘못 클릭할 수 있음을 실측으로 확인(2026-07-15:\n        #     7-Zip에서 \"hansung\"/\"project\" 등 사용자의 실제 폴더명을 검색하다가\n        #     (a)에서 못 찾자 사용자가 실제로 열어둔 탐색기 창(explorer.exe,\n        #     class=CabinetWClass)과 VS Code 창(Code.exe)에서 우연히 같은\n        #     이름을 찾아 그 창을 대신 클릭 — 거짓 성공으로 로그에 \"invoked\"\n        #     찍힘 + 사용자 창에 실제 부작용).\n        for h in top_windows():\n            if h == main_h:\n                continue\n            cand_pid = wintypes.DWORD()\n            user32.GetWindowThreadProcessId(h, ctypes.byref(cand_pid))\n            if cand_pid.value != main_pid.value:\n                continue\n            try:\n                other_root = uia.ElementFromHandle(h)\n                if not other_root:\n                    continue\n                item = other_root.FindFirst(TreeScope_Subtree, item_cond)\n                if item and act(item):\n                    print(f\"[osScopedInvoke] {verb} under other top-level window hwnd={h}\")\n                    sys.exit(0)\n            except Exception:\n                continue\n\n    # 마지막 수단(타이핑 전용): 포커스를 가진 입력 컨트롤. 인라인 이름변경\n    # 상자처럼 automationId가 런타임에 변하는 대상은 셀렉터로는 영원히 못\n    # 찾지만 포커스는 항상 갖고 있다. 클릭에는 적용하지 않는다 — \"포커스된\n    # 무언가를 대신 클릭\"은 엉뚱한 동작을 조용히 수행할 위험이 크다.\n    if args.text_b64:\n        el = focused_input(uia, main_pid.value)\n        if el and act(el):\n            print(f\"[osScopedInvoke] {verb} the focused input control\")\n            sys.exit(0)\n\n    print(f\"osScopedInvoke: target not found under main window or any other top-level window (sel={args.sel_b64})\", file=sys.stderr)\n    sys.exit(2)\n\n\nif __name__ == \"__main__\":\n    main()\n",
};
let _helperDir = null;
function _helperFile(name) {
    if (!_helperDir) _helperDir = mkdtempSync(join(tmpdir(), 'qaforge-helpers-'));
    const p = join(_helperDir, name);
    if (!existsSync(p)) {
        // powershell -File reads a BOM-less file as the system ANSI codepage
        // (CP949 here), not UTF-8 — which mangles osDismissPopup.ps1's Korean
        // button names ('취소'/'아니요'/'닫기'/'확인'/'예') into mojibake and
        // breaks the PS parser outright with UnexpectedToken. saveFiles() has
        // prepended a BOM for exactly this reason since 2026-07-08; this
        // extraction path must do the same or the popup Fail-and-Recover
        // mechanism silently dies (confirmed 2026-07-27 by reproducing the
        // same parse error against a temp-extracted copy).
        const body = name.endsWith('.ps1') && !_H[name].startsWith('\ufeff')
            ? '\ufeff' + _H[name]
            : _H[name];
        writeFileSync(p, body, 'utf8');
    }
    return p;
}

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
let _appiumLogFd = null;
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
    // stdio must NOT be 'pipe' — nothing here ever reads _spawnedAppium's
    // stdout/stderr, and Appium logs every request/response verbosely. Once
    // the OS pipe buffer fills, the child blocks on write() forever and the
    // whole HTTP server goes unresponsive mid-run (2026-07-27: root cause of
    // replays hanging/timing out at a different step every time — the
    // threshold is cumulative log bytes, not step count). Redirect to a log
    // file fd instead: never blocks, and doubles as the Appium-server-side
    // log this project has repeatedly needed for post-mortems.
    const appiumLogPath = join(__dirname, 'appium.log');
    _appiumLogFd = openSync(appiumLogPath, 'w');
    console.log(`[appium] logging to ${appiumLogPath}`);
    _spawnedAppium = spawn(process.execPath, [appiumBin, '--allow-insecure', '*:winappdriver', '--port', '4723'], { stdio: ['ignore', _appiumLogFd, _appiumLogFd] });
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
    if (_appiumLogFd !== null) {
        try { closeSync(_appiumLogFd); } catch {}
        _appiumLogFd = null;
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
        // 2026-08-05 (TeamViewer "빠른 연결 허용" 이메일/비밀번호 실측): 이
        // 함수는 성공 시 지금까지 아무것도 안 찍었다 — 실패만 찍는 catch와
        // 합쳐서 보면 "성공해서 조용했다"와 "그 어떤 호출도 실제로 안
        // 일어났다"를 로그만으로 구분할 방법이 없었다(simple 모드로 생성된
        // 파일에서 이 함수가 실제로 도는 코드인 걸 확인하는 데만 세 번의
        // 재녹화가 들었다 — session-mode 전용 코드만 계속 고치고 있었음).
        // WinAppDriver의 element/value가 예외 없이 성공을 보고해도 앱에
        // 실제로 반영 안 되는 사례가 이미 WebView2에서 확인됐다(§ isWebContent
        // 타이핑 분기 주석) — 여기서 element/value가 "성공"을 보고했다는
        // 사실 자체를 남겨야, 다음에도 같은 증상(로그는 성공, 화면은 빈칸)이
        // 나면 "WAD REST가 거짓 성공을 보고하는 컨트롤"로 확정할 수 있다.
        console.log(`[type] scoped sendKeys ok (sid=${sid} elId=${elId} sel=${selector})`);
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

// Current foreground window handle (user32!GetForegroundWindow via a base64
// -EncodedCommand — no quote-escaping, read-only). _step() uses it to decide
// whether an ESC would land on a real popup or on the main dialog itself.
// 2026-07-27: moved here from SIMPLE_HEADER — SESSION_HEADER's _step() calls
// this too (2026-07-24 parity fix) but the definition itself never followed,
// leaving session-mode output with a call site and no definition (same
// pattern as the 2026-07-16 osExpandCollapse "Bug D").
function osForegroundHwnd() {
    try {
        const out = execSync(
            `powershell -NoProfile -EncodedCommand QQBkAGQALQBUAHkAcABlACAAQAAiAAoAdQBzAGkAbgBnACAAUwB5AHMAdABlAG0AOwAKAHUAcwBpAG4AZwAgAFMAeQBzAHQAZQBtAC4AUgB1AG4AdABpAG0AZQAuAEkAbgB0AGUAcgBvAHAAUwBlAHIAdgBpAGMAZQBzADsACgBwAHUAYgBsAGkAYwAgAGMAbABhAHMAcwAgAEYAZwAgAHsAIABbAEQAbABsAEkAbQBwAG8AcgB0ACgAIgB1AHMAZQByADMAMgAuAGQAbABsACIAKQBdACAAcAB1AGIAbABpAGMAIABzAHQAYQB0AGkAYwAgAGUAeAB0AGUAcgBuACAASQBuAHQAUAB0AHIAIABHAGUAdABGAG8AcgBlAGcAcgBvAHUAbgBkAFcAaQBuAGQAbwB3ACgAKQA7ACAAfQAKACIAQAAgAC0ARQByAHIAbwByAEEAYwB0AGkAbwBuACAAUwBpAGwAZQBuAHQAbAB5AEMAbwBuAHQAaQBuAHUAZQAKAFsARgBnAF0AOgA6AEcAZQB0AEYAbwByAGUAZwByAG8AdQBuAGQAVwBpAG4AZABvAHcAKAApAC4AVABvAEkAbgB0ADYANAAoACkA`,
            { stdio: 'pipe', timeout: 15000 }
        ).toString().trim();
        const m = out.match(/-?\d+/);
        return m ? (parseInt(m[0], 10) || 0) : 0;
    } catch (e) {
        console.warn('[osForegroundHwnd] failed:', String(e.message || e).substring(0, 100));
        return 0;
    }
}

// 프로그래매틱 스크롤 — osScroll.py가 추적된 top-level hwnd 아래에서 녹화된
// 컨테이너를 UIA로 찾아 ScrollPattern.Scroll()을 호출하고, ScrollPattern
// 미지원 레거시 컨트롤에만 hwnd-scoped WM_MOUSEWHEEL을 PostMessageW로
// 전달한다. 픽셀 좌표/물리 커서 주입 없음 (2026-07-10 좌표 실행 금지 지시).
function osScrollEl(hwnd, target, delta) {
    if (!hwnd) {
        _failures.push('osScroll:no-hwnd');
        console.warn('[osScroll] no window hwnd — cannot scroll without a window handle');
        return;
    }
    try {
        const selB64 = Buffer.from(JSON.stringify(target || {}), 'utf8').toString('base64');
        const out = execSync(
            `python "${_helperFile('osScroll.py')}" --hwnd ${hwnd} --sel-b64 "${selB64}" --delta ${delta}`,
            { stdio: 'pipe', timeout: 20000 }
        ).toString().trim();
        if (out) console.log(out);
    } catch (e) {
        _failures.push('osScroll');
        console.warn('[osScroll] failed:', String((e.stderr && e.stderr.toString()) || e.message || e).slice(-1500));
    }
}

// ExpandCollapsePattern 재생 — ComboBox 드롭다운/메뉴바 MenuItem/트리 +- 토글은
// 일반 클릭(InvokePattern)만으로 재현 안 됨(2026-07-13 진단, poc/diag_expandcollapse.py
// 실측: PuTTY ComboBox는 드롭다운이 안 열리고, FileZilla 메뉴바는 Expand()는
// 성공해도 하위 항목이 새 최상위 팝업 창에 생겨 원래 서브트리에서 안 보임).
// WinAppDriver REST를 거치지 않고 COM UIA로 직접 처리(osExpandCollapse.py —
// comtypes, 레거시 SysTreeView32 TreeItem까지 보임; 2026-07-14 .NET managed UIA
// 맹점 수정). 세션이 새 팝업 창을 못 보는 제약도 우회. itemName이 있으면 펼친
// 뒤 그 항목을 찾아 Invoke, 없으면 펼치기/접기 자체만(트리 +- 토글).
function osExpandCollapse(hwnd, target, itemName, itemIndex, itemCount) {
    if (!hwnd) {
        _failures.push('osExpandCollapse:no-hwnd');
        console.warn('[osExpandCollapse] no window hwnd — cannot expand without a window handle');
        return;
    }
    try {
        const selB64 = Buffer.from(JSON.stringify(target || {}), 'utf8').toString('base64');
        const itemArg = itemName ? `--item-name-b64 "${Buffer.from(itemName, 'utf8').toString('base64')}"` : '';
        // owner-drawn dropdown (every item Name empty) — address by list position
        const idxArg = Number.isInteger(itemIndex)
            ? `--item-index ${itemIndex}` + (Number.isInteger(itemCount) ? ` --item-count ${itemCount}` : '')
            : '';
        const out = execSync(
            `python "${_helperFile('osExpandCollapse.py')}" --hwnd ${hwnd} --sel-b64 "${selB64}" ${itemArg} ${idxArg}`,
            { stdio: 'pipe', timeout: 20000 }
        ).toString().trim();
        if (out) console.log(out);
    } catch (e) {
        _failures.push('osExpandCollapse');
        const stdoutTxt = String((e.stdout && e.stdout.toString()) || '').trim();
        if (stdoutTxt) console.warn('[osExpandCollapse] stdout before failure:', stdoutTxt.slice(-1500));
        console.warn('[osExpandCollapse] failed:', String((e.stderr && e.stderr.toString()) || e.message || e).slice(-1500));
    }
}

// 이름 없는 요소를 "가까운 이름 있는 조상 + 동일 ControlType 형제 순번"으로
// 재탐색 (2026-08-08, agent.py의 _ancestor_sibling_selector가 기록). 새 함수로
// 분리한 이유: osExpandCollapse()는 이미 5개 위치 인자에 null-sentinel 관례가
// 박혀있어, 시그니처를 확장하면 기존 호출부들과 충돌 위험이 있다. 트리거를
// 펼 필요가 없는(이미 화면에 떠 있는 형제를 고르는) 별개의 경로이므로
// osExpandCollapse.py의 --ancestor-sel-b64 분기로 보낸다.
function osAncestorInvoke(hwnd, ancestorTarget, itemIndex, itemCount, ctrlTypeId, double) {
    if (!hwnd) {
        _failures.push('osAncestorInvoke:no-hwnd');
        console.warn('[osAncestorInvoke] no window hwnd — cannot resolve ancestor without a window handle');
        return;
    }
    if (!Number.isInteger(itemIndex) || !Number.isInteger(ctrlTypeId)) {
        _failures.push('osAncestorInvoke:no-index');
        console.warn('[osAncestorInvoke] missing itemIndex/ctrlTypeId — cannot address a sibling by position');
        return;
    }
    try {
        const ancB64 = Buffer.from(JSON.stringify(ancestorTarget || {}), 'utf8').toString('base64');
        const cntArg = Number.isInteger(itemCount) ? ` --item-count ${itemCount}` : '';
        const doubleArg = double ? ' --double' : '';
        const out = execSync(
            `python "${_helperFile('osExpandCollapse.py')}" --hwnd ${hwnd} --ancestor-sel-b64 "${ancB64}" --item-index ${itemIndex}${cntArg} --ancestor-item-ctrltype ${ctrlTypeId}${doubleArg}`,
            { stdio: 'pipe', timeout: 20000 }
        ).toString().trim();
        if (out) console.log(out);
    } catch (e) {
        _failures.push('osAncestorInvoke');
        console.warn('[osAncestorInvoke] failed:', String((e.stderr && e.stderr.toString()) || e.message || e).slice(-1500));
    }
}

// 창-교차 클릭 재생 — 이벤트의 캡처 시점 창 크기/위치가 이 앱의 메인 창과
// 다르면(2026-07-13, PuTTY "Remote character set:" 콤보박스 조사: 옆
// "DropDown" 버튼 클릭으로 열리는 목록이 별도 최상위 창(Win32 클래스
// "ComboLBox")으로 뜸 — FileZilla 메뉴 팝업과 같은 부류) 그 대상은
// WinAppDriver 세션(메인 창에 스코프) 밖에 있다. osScopedInvoke.py가
// COM UIA로 메인 창 → 그 외 모든 최상위 창 순으로 직접 찾아 Invoke.
// triggerTarget이 있으면(버튼 클릭으로 여는 경우) 같은 스크립트 실행 안에서
// 그 트리거를 먼저 클릭한 뒤 곧바로 항목을 검색한다 — 트리거 클릭과 항목
// 검색을 별도 스텝(별도 프로세스)으로 쪼개면 그 사이 지연 동안 드롭다운이
// 자동으로 닫혀버림을 실측으로 확인(2026-07-13 재현) — 한 프로세스 실행
// 안에서 끊김 없이 처리해 그 레이스를 없앤다.
function osScopedInvoke(hwnd, target, triggerTarget, relY, triggerRelY, ownerTitle, double, verifyToggle) {
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
        // relY/triggerRelY (2026-07-29, HeidiSQL DropDown reuse): a
        // disambiguation hint among structurally-found candidates only —
        // recomputed from the recorded window-relative Y against the
        // CURRENT window position inside osScopedInvoke.py, never a stored
        // screen coordinate used to click (see pick_by_position there).
        const relYArg = Number.isInteger(relY) ? `--rel-y ${relY}` : '';
        const triggerRelYArg = Number.isInteger(triggerRelY) ? `--trigger-rel-y ${triggerRelY}` : '';
        // ownerTitle: the window this event was recorded in. Searched BEFORE
        // the main window so a bare-name selector cannot match a same-named
        // control in the main window first (PuTTY 닫기, 2026-08-03).
        const ownerArg = ownerTitle
            ? `--owner-title-b64 "${Buffer.from(String(ownerTitle), 'utf8').toString('base64')}"`
            : '';
        // double: 녹화된 동작이 더블클릭이었을 때만. 네이티브 리스트 행에서
        // 단일 클릭은 선택이지 열기가 아니다 (2026-08-04, 7-Zip 컴퓨터→C:).
        const doubleArg = double ? '--double' : '';
        // verifyToggle (2026-08-04): CheckBox 클릭 전후 ToggleState를 비교해
        // 실제로 바뀌었는지 검증 — verified_toggle_click 참고.
        const verifyToggleArg = verifyToggle ? '--verify-toggle' : '';
        const out = execSync(
            `python "${_helperFile('osScopedInvoke.py')}" --hwnd ${hwnd} --sel-b64 "${selB64}" ${triggerArg} ${relYArg} ${triggerRelYArg} ${ownerArg} ${doubleArg} ${verifyToggleArg}`,
            { stdio: 'pipe', timeout: 20000 }
        ).toString().trim();
        if (out) console.log(out);
    } catch (e) {
        _failures.push('osScopedInvoke');
        // stdout carries any WARN lines (e.g. trigger-not-found) written before
        // the script's final Write-Error — surface both so the WARN isn't lost
        // behind the terminal error (2026-07-14: this WARN is what pinpoints
        // "dropdown never opened" vs. other reasons the item search failed).
        const stdoutMsg = (e.stdout && e.stdout.toString().trim()) || '';
        if (stdoutMsg) console.log(stdoutMsg);
        console.warn('[osScopedInvoke] failed:', String((e.stderr && e.stderr.toString()) || e.message || e).slice(-1500));
    }
}

// hwnd of the window this WinAppDriver session actually owns. Title-substring
// matching is non-deterministic whenever a same-titled window already exists
// (e.g. a FreeDM instance the user had open, alongside the fresh instance the
// session just launched, on a different monitor) — confirmed 2026-07-06: the
// session window landed on monitor 1 while the pre-existing user window sat
// on monitor 2, and title match grabbed whichever one it happened to find
// first, so clicks that were otherwise correctly computed missed entirely.
// getWindowHandle() returns the session's own NativeWindowHandle — unique,
// no title ambiguity — so every OS-level lookup below prefers it once known.
let _appHwnd = 0;

async function initAppHwnd() {
    try {
        const r = await _appiumFetch(`/session/${_appSid}/window`);
        const j = await r.json();
        const h = j.value;   // e.g. "0x00061D2C"
        _appHwnd = parseInt(h, 16);
        console.log(`[hwnd] session window hwnd=${_appHwnd} (0x${_appHwnd.toString(16)})`);
        // Some apps (confirmed: HeidiSQL, a Delphi/VCL app) own a hidden 0x0
        // 'TApplication' helper window in addition to their real visible
        // form — appium:app launches by process and can bind the session to
        // that hidden window instead, so every later element search runs
        // against an empty 0x0 window and finds nothing. Detect this and
        // re-create the session scoped directly to a real, sized sibling
        // window of the same process (appium:appTopLevelWindow), same
        // mechanism already used for owned-dialog scoped sessions.
        const rect0 = _resolveWinRect('');
        if (rect0 && rect0.width === 0 && rect0.height === 0) {
            const sibOut = execSync(
                `powershell -NoProfile -File "${_helperFile('osWindowRect.ps1')}" -siblingOf ${_appHwnd}`,
                { stdio: 'pipe', timeout: 15000 }
            ).toString().trim();
            const sibling = parseInt(sibOut, 10);
            if (sibling) {
                console.warn(`[hwnd] session window is zero-size — re-creating session scoped to real window hwnd=${sibling} (0x${sibling.toString(16)})`);
                // Deliberately NOT deleting the old (zero-size) session here:
                // it was created with the 'app' capability (process launch),
                // and WinAppDriver's deleteSession for an app-owning session
                // kills the process it launched — which would also destroy
                // the real window we just switched to (same process, same
                // app instance). Confirmed live: HeidiSQL closed at exactly
                // this point once the DELETE call was added. Left open, it's
                // an idle extra session that goes away with the Appium
                // process this script's own ensureAppium() spawned and tears
                // down at exit — no separate cleanup needed.
                _appSid = await _createSession('0x' + sibling.toString(16));
                _appHwnd = sibling;
                console.log(`[hwnd] session window hwnd=${_appHwnd} (0x${_appHwnd.toString(16)})`);
            } else {
                console.warn('[hwnd] session window is zero-size and no sized sibling window was found');
            }
        }
    } catch (e) {
        console.warn('[hwnd] getWindowHandle failed — falling back to title match:', String(e.message || e).substring(0, 100));
    }
}

// OS-level window activation via PowerShell user32.dll. Simple-mode tests
// never run launchApp's foreground/normalize step, so a freshly launched app
// can be spawned behind other windows or off-position — bring it forward
// before the first click so OS-level input actually reaches it.
function osActivate(titleLike, hwnd) {
    try {
        // 2026-08-05 (TeamViewer "빠른 연결 허용" 이메일/비밀번호 실측): 원래
        // hwnd 인자가 없어서 항상 _appHwnd(메인 창)만 활성화할 수 있었다 —
        // 크로스윈도우 대화상자를 지정할 방법이 아예 없었다. session 헤더의
        // osActivate 시그니처와 맞춘다. 기존 호출부(인자 1개, 예: 빈 문자열 또는
        // title만 넘기는 호출)는 hwnd가 undefined이므로 _appHwnd 폴백으로
        // 그대로 동작해 변화 없음.
        const h = hwnd || _appHwnd;
        const args = h ? `-hwnd ${h}` : `-titleLike "${titleLike}"`;
        execSync(
            `powershell -NoProfile -File "${_helperFile('osActivate.ps1')}" ${args}`,
            { stdio: 'pipe', timeout: 15000 }
        );
    } catch (e) {
        console.warn('[osActivate] failed:', String(e.message || e).substring(0, 100));
    }
}

// 2026-08-05: session 헤더의 _listWindowHwnds()와 동일 — 크로스윈도우 타이핑
// 스텝이 simple 모드에서도 대화상자의 진짜 hwnd를 라이브로 찾을 수 있어야
// 해서 여기에도 둔다(원래 session 전용이었음).
function _listWindowHwnds(frag) {
    if (!frag) return [];
    try {
        const out = execSync(
            `powershell -NoProfile -File "${_helperFile('osWindowRect.ps1')}" -titleLike "${frag}" -listOnly`,
            { stdio: 'pipe', timeout: 15000 }
        ).toString().trim();
        if (!out) return [];
        return out.split(/\r?\n/).map(s => s.trim()).filter(Boolean).map(Number);
    } catch {
        return [];
    }
}

// Reads the window's CURRENT rect — by hwnd when the session's own window is
// known (deterministic), by title match otherwise. Geometry read only —
// used by normalizeWindowSimple to restore the recorded window position/size.
function _resolveWinRect(titleLike) {
    try {
        const args = _appHwnd ? `-hwnd ${_appHwnd}` : `-titleLike "${titleLike}"`;
        const out = execSync(
            `powershell -NoProfile -File "${_helperFile('osWindowRect.ps1')}" ${args}`,
            { stdio: 'pipe', timeout: 15000 }
        ).toString().trim();
        const m = out.match(/(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)/);
        if (m) return { left: +m[1], top: +m[2], width: +m[3], height: +m[4] };
    } catch (e) {
        console.warn('[winRect] failed:', String(e.message || e).substring(0, 100));
    }
    return null;
}

// Moves+resizes the session's window back to its recorded geometry. The
// freshly launched window can appear on a different monitor or at a
// different size than recording, which both skews DPI-scaling behavior and
// makes the recorded relX/relY (window-relative UI offsets) point at the
// wrong spot after the resulting UI reflow.
function normalizeWindowSimple(rect) {
    if (!_appHwnd || !rect) return;
    try {
        execSync(
            `powershell -NoProfile -File "${_helperFile('osMoveWindow.ps1')}" -hwnd ${_appHwnd} -left ${rect.left} -top ${rect.top} -width ${rect.width} -height ${rect.height}`,
            { stdio: 'pipe', timeout: 15000 }
        );
        const after = _resolveWinRect('');
        console.log('[normalize] window moved to', after);
    } catch (e) {
        _failures.push('normalize');
        console.warn('[normalize] failed:', String(e.message || e).substring(0, 100));
    }
}

// OS-level keystrokes into the focused control (SendKeys — keyboard
// injection, not coordinate execution). Fallback for edit controls whose
// element/value endpoint WinAppDriver rejects (confirmed 2026-07-08:
// Win11 Notepad's RichEditD2DPT Document control).
function osType(text) {
    try {
        const b64 = Buffer.from(text, 'utf8').toString('base64');
        execSync(
            `powershell -NoProfile -File "${_helperFile('osType.ps1')}" -b64 "${b64}"`,
            { stdio: 'pipe', timeout: 15000 }
        );
    } catch (e) {
        _failures.push('osType');
        console.warn('[osType] failed:', String(e.message || e).substring(0, 100));
    }
}

// Fail-and-Recover popup dismissal (v2) — only called from _step() below,
// after a step has already failed, so the happy path pays zero cost.
// _appHwnd (resolved by initAppHwnd() in beforeAll) identifies the main
// app window deterministically for owner-PID scoping. Simple mode drives a
// single window (_appHwnd, already excluded as the ps1's $mainHwnd), so no
// -exclude list is needed here — see the session header's osDismissPopup.
function osDismissPopup() {
    try {
        const args = _appHwnd ? `-hwnd ${_appHwnd}` : '';
        const out = execSync(
            `powershell -NoProfile -File "${_helperFile('osDismissPopup.ps1')}" ${args}`,
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
            `powershell -NoProfile -File "${_helperFile('osEscape.ps1')}"`,
            { stdio: 'pipe', timeout: 15000 }
        );
        return true;
    } catch (e) {
        console.warn('[osEscape] failed:', String(e.message || e).substring(0, 100));
        return false;
    }
}

// osForegroundHwnd() now lives in STANDALONE_PREAMBLE (shared by both
// headers) — see above.

// Wraps a single replay step: on the happy path (no exception, no new
// _failures entry) this costs nothing extra. On failure, scans for and
// dismisses a known-shape popup that didn't exist at recording time (e.g.
// FDM's "file already exists"), then retries the step ONCE. If no dismiss
// button was found, it may ESC to back out of a transient modal state — but
// only when a real popup (not the main dialog) holds the foreground; see below.
// If recovery still fails, the original failure/exception stands (no false PASSED).
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
        // No known popup button found. On a dialog-based main window (PuTTY
        // Configuration) ESC == Cancel == close the app, so an unconditional
        // ESC here nukes the whole run on the first failed step (confirmed
        // 2026-07-14: the old osActivate('')+ESC closed PuTTY every time). Only
        // ESC when a DIFFERENT top-level window (a real popup/dropdown) holds
        // the foreground; if OUR main window is foreground there is nothing to
        // dismiss and ESC would only kill the app — skip it.
        const fg = osForegroundHwnd();
        if (_appHwnd && fg === _appHwnd) {
            _warnings.push('esc-skipped-main-foreground:' + label);
        } else {
            osEscape();
            // Backstop: if ESC did land on a dialog-based window and closed the
            // app, surface the ORIGINAL failure cleanly instead of a misleading
            // no-such-window cascade (2026-07-13).
            if (_appHwnd && !_resolveWinRect('')) {
                _failures.push('esc-recovery-closed-app:' + label);
                throw new Error(`ESC recovery closed the app window during step: ${label}`);
            }
            _warnings.push('esc-recovery:' + label);
        }
    }
    _failures.length = before;
    await fn();
}

// Windows in this recording:
//   [W1] "PuTTY Configuration" (main)
//   [W2] "PuTTY Configuration" (opened during recording)

class MockGoldenPuTTYPageByClass {

    // ════════════════════════════════════════════════════════════
    // [W1] PuTTY Configuration (main window)
    // ════════════════════════════════════════════════════════════
    async click1() {
        await _clickBySid(_appSid, null, '//TreeItem[@Name="Keyboard"]');
    }

    async click2() {
        await _clickBySid(_appSid, null, '//TreeItem[@Name="Features"]');
    }

    async click3() {
        await _clickBySid(_appSid, null, '//TreeItem[@Name="Appearance"]');
    }

    async click4() {
        await _clickBySid(_appSid, null, '//TreeItem[@Name="Behaviour"]');
    }

    async click5() {
        await _clickBySid(_appSid, null, '//TreeItem[@Name="Translation"]');
    }

    async click6() {
        await _clickBySid(_appSid, null, '//TreeItem[@Name="Selection"]');
    }

    async click7() {
        await _clickBySid(_appSid, null, '//TreeItem[@Name="Connection"]');
    }

    async click8() {
        osExpandCollapse(_appHwnd, {"automationId":"","className":"","name":"Connection"}, null, null, null);
    }

    async click9() {
        osExpandCollapse(_appHwnd, {"automationId":"","className":"","name":"Connection"}, null, null, null);
    }

    async click10() {
        await _clickBySid(_appSid, null, '//TreeItem[@Name="Proxy"]');
    }

    async click11() {
        osScopedInvoke(_appHwnd, {"automationId":"DropDown","className":"","name":""}, null, 115);
    }


    // ════════════════════════════════════════════════════════════
    // [W2] PuTTY Configuration (new window)
    // ════════════════════════════════════════════════════════════
    async click12() {
        await _clickBySid(_appSid, null, '//Edit[@ClassName="Edit" and @Name="Exclude Hosts/IPs"]');
    }

    async click13() {
        await _clickBySid(_appSid, null, '//Button[@ClassName="Button" and @Name="Cancel"]');
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

    await ensureAppium();
    _appSid = await _createSession("C:\\\\Program Files\\\\PuTTY\\\\putty.exe");
    console.log(`[session] app session ${_appSid} ready`);
    await initAppHwnd();
    normalizeWindowSimple({"left":651,"top":264,"width":618,"height":551});

        const page = new MockGoldenPuTTYPageByClass();
            osActivate("PuTTY Configuration");

    // ════════════════════════════════════════════════════════════
    // [W1] PuTTY Configuration (main window)
    // ════════════════════════════════════════════════════════════
            await _step('1:click Keyboard', () => page.click1());
            await _step('2:click Features', () => page.click2());
            await _step('3:click Appearance', () => page.click3());
            await _step('4:click Behaviour', () => page.click4());
            await _step('5:click Translation', () => page.click5());
            await _step('6:click Selection', () => page.click6());
            await _step('7:click Connection', () => page.click7());
            await _step('8:expandCollapse Connection', () => page.click8());
            await _step('9:expandCollapse Connection', () => page.click9());
            await _step('10:click Proxy', () => page.click10());
            await _step('11:click 열기', () => page.click11());

    // ════════════════════════════════════════════════════════════
    // [W2] PuTTY Configuration (new window)
    // ════════════════════════════════════════════════════════════
            await _step('12:click Exclude Hosts/IPs', () => page.click12());
            await _step('13:click Cancel', () => page.click13());
    } finally {

        if (_appSid) { try { await _appiumFetch(`/session/${_appSid}`, { method: 'DELETE' }, 5000); } catch {} }
        _killSpawnedAppium();
    }
    if (_warnings.length) console.warn('[replay-warnings]', _warnings);
    if (_failures.length) { console.error('[FAIL]', _failures); process.exitCode = 1; }
    else console.log('[PASS] all steps completed');
}

run().catch(e => { console.error('[FATAL]', e); process.exitCode = 1; });
