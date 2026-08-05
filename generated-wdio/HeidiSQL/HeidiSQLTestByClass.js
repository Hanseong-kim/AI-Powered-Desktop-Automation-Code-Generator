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
const _H = {
    'osWindowRect.ps1': "param([string]$titleLike, [string]$hwnd, [switch]$listOnly, [switch]$ownerOnly, [string]$siblingOf)\nAdd-Type @\"\nusing System;\nusing System.Text;\nusing System.Collections.Generic;\nusing System.Runtime.InteropServices;\npublic class WinEnum {\n  public delegate bool EnumProc(IntPtr hWnd, IntPtr lParam);\n  [DllImport(\"user32.dll\")] public static extern bool EnumWindows(EnumProc proc, IntPtr lParam);\n  [DllImport(\"user32.dll\")] public static extern bool IsWindowVisible(IntPtr hWnd);\n  [DllImport(\"user32.dll\")] public static extern bool IsIconic(IntPtr hWnd);\n  [DllImport(\"user32.dll\")] public static extern int GetWindowTextLength(IntPtr hWnd);\n  [DllImport(\"user32.dll\", CharSet = CharSet.Auto)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder sb, int max);\n  [DllImport(\"user32.dll\")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT r);\n  [DllImport(\"user32.dll\")] public static extern IntPtr GetForegroundWindow();\n  [DllImport(\"user32.dll\")] public static extern IntPtr GetWindow(IntPtr hWnd, uint cmd);\n  [DllImport(\"user32.dll\")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);\n  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }\n  public static List<IntPtr> Find(string titleLike) {\n    var found = new List<IntPtr>();\n    EnumWindows((hWnd, lParam) => {\n      if (!IsWindowVisible(hWnd) || IsIconic(hWnd)) return true;\n      int len = GetWindowTextLength(hWnd);\n      if (len == 0) return true;\n      var sb = new StringBuilder(len + 1);\n      GetWindowText(hWnd, sb, sb.Capacity);\n      if (sb.ToString().Contains(titleLike)) found.Add(hWnd);\n      return true;\n    }, IntPtr.Zero);\n    return found;\n  }\n  // Sibling top-level windows of the same process as hwndOf, excluding\n  // hwndOf itself, restricted to ones with a real (non-zero) rect. Used to\n  // recover from WinAppDriver/OS binding the \"main\" window to a hidden 0x0\n  // helper window instead of the app's actual visible form (observed with\n  // HeidiSQL's Delphi/VCL 'TApplication' window).\n  public static List<IntPtr> FindSizedSiblings(IntPtr hwndOf) {\n    var found = new List<IntPtr>();\n    uint pid;\n    GetWindowThreadProcessId(hwndOf, out pid);\n    if (pid == 0) return found;\n    EnumWindows((hWnd, lParam) => {\n      if (hWnd == hwndOf || !IsWindowVisible(hWnd)) return true;\n      uint wpid;\n      GetWindowThreadProcessId(hWnd, out wpid);\n      if (wpid != pid) return true;\n      RECT r;\n      if (GetWindowRect(hWnd, out r) && (r.Right - r.Left) > 0 && (r.Bottom - r.Top) > 0) {\n        found.Add(hWnd);\n      }\n      return true;\n    }, IntPtr.Zero);\n    return found;\n  }\n}\n\"@ -ErrorAction SilentlyContinue\nif ($siblingOf) {\n  $sibs = [WinEnum]::FindSizedSiblings([IntPtr]([int64]$siblingOf))\n  if ($sibs.Count -gt 0) { Write-Output ([int64]$sibs[0]) }\n  exit\n}\n# -hwnd targets one specific window directly, bypassing title matching entirely.\n# Title matching alone is ambiguous whenever more than one window shares a\n# substring (e.g. every VS Code window's title ends in \"Visual Studio Code\") —\n# callers that already know their window's handle MUST use -hwnd so replay\n# never drifts onto an unrelated window (see launchApp's hwnd tracking).\nif ($hwnd) {\n  $h = [IntPtr]([int64]$hwnd)\n  if ($ownerOnly) {\n    # GW_OWNER=4 — nonzero means an owned (dialog-style) window, which\n    # WinAppDriver's appTopLevelWindow rejects outright (\"not a top level\n    # window handle\"), so callers skip the scoped-session attempt entirely.\n    Write-Output ([int64][WinEnum]::GetWindow($h, 4))\n    exit\n  }\n  $r = New-Object WinEnum+RECT\n  if ([WinEnum]::GetWindowRect($h, [ref]$r)) {\n    Write-Output (\"{0} {1} {2} {3}\" -f $r.Left, $r.Top, ($r.Right - $r.Left), ($r.Bottom - $r.Top))\n  }\n  exit\n}\n$matches = [WinEnum]::Find($titleLike)\nif ($listOnly) {\n  foreach ($h in $matches) { Write-Output ([int64]$h) }\n  exit\n}\nif ($matches.Count -gt 0) {\n  $fg = [WinEnum]::GetForegroundWindow()\n  $hWnd = $matches[0]\n  foreach ($h in $matches) { if ($h -eq $fg) { $hWnd = $h; break } }\n  $r = New-Object WinEnum+RECT\n  [WinEnum]::GetWindowRect($hWnd, [ref]$r) | Out-Null\n  Write-Output (\"{0} {1} {2} {3}\" -f $r.Left, $r.Top, ($r.Right - $r.Left), ($r.Bottom - $r.Top))\n}\n",
    'osMoveWindow.ps1': "param([string]$titleLike, [string]$hwnd, [int]$left, [int]$top, [int]$width, [int]$height)\nAdd-Type @\"\nusing System;\nusing System.Text;\nusing System.Collections.Generic;\nusing System.Runtime.InteropServices;\npublic class WinMove {\n  public delegate bool EnumProc(IntPtr hWnd, IntPtr lParam);\n  [DllImport(\"user32.dll\")] public static extern bool EnumWindows(EnumProc proc, IntPtr lParam);\n  [DllImport(\"user32.dll\")] public static extern bool IsWindowVisible(IntPtr hWnd);\n  [DllImport(\"user32.dll\")] public static extern bool IsIconic(IntPtr hWnd);\n  [DllImport(\"user32.dll\")] public static extern int GetWindowTextLength(IntPtr hWnd);\n  [DllImport(\"user32.dll\", CharSet = CharSet.Auto)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder sb, int max);\n  [DllImport(\"user32.dll\")] public static extern IntPtr GetForegroundWindow();\n  [DllImport(\"user32.dll\")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);\n  [DllImport(\"user32.dll\")] public static extern bool MoveWindow(IntPtr hWnd, int X, int Y, int nWidth, int nHeight, bool bRepaint);\n  [DllImport(\"user32.dll\")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT r);\n  [DllImport(\"user32.dll\")] public static extern bool IsZoomed(IntPtr hWnd);\n  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }\n  public static List<IntPtr> Find(string titleLike) {\n    var found = new List<IntPtr>();\n    EnumWindows((hWnd, lParam) => {\n      if (!IsWindowVisible(hWnd) || IsIconic(hWnd)) return true;\n      int len = GetWindowTextLength(hWnd);\n      if (len == 0) return true;\n      var sb = new StringBuilder(len + 1);\n      GetWindowText(hWnd, sb, sb.Capacity);\n      if (sb.ToString().Contains(titleLike)) found.Add(hWnd);\n      return true;\n    }, IntPtr.Zero);\n    return found;\n  }\n}\n\"@ -ErrorAction SilentlyContinue\n# -hwnd bypasses title matching — see OS_WINRECT_PS1 for why ambiguous title\n# substrings (e.g. any two VS Code windows) are unsafe to move/resize by.\nif ($hwnd) {\n  $hWnd = [IntPtr]([int64]$hwnd)\n} else {\n  $matches = [WinMove]::Find($titleLike)\n  $hWnd = [IntPtr]::Zero\n  if ($matches.Count -gt 0) {\n    $fg = [WinMove]::GetForegroundWindow()\n    $hWnd = $matches[0]\n    foreach ($h in $matches) { if ($h -eq $fg) { $hWnd = $h; break } }\n  }\n}\nif ($hWnd -ne [IntPtr]::Zero) {\n  # Idempotency fast-path: if the window is already at the target geometry\n  # (and not maximized), skip ShowWindow(RESTORE)+MoveWindow entirely — avoids\n  # a visible restore-then-resize flicker when replay finds the window already\n  # in the recorded position (e.g. the \"already maximized\" case reported\n  # 2026-07-07: recorded flow assumes a maximize step is needed, but the\n  # window is already there).\n  $already = New-Object WinMove+RECT\n  [WinMove]::GetWindowRect($hWnd, [ref]$already) | Out-Null\n  $sameW = [math]::Abs(($already.Right - $already.Left) - $width) -le 2\n  $sameH = [math]::Abs(($already.Bottom - $already.Top) - $height) -le 2\n  $sameL = [math]::Abs($already.Left - $left) -le 2\n  $sameT = [math]::Abs($already.Top - $top) -le 2\n  if (-not [WinMove]::IsZoomed($hWnd) -and $sameW -and $sameH -and $sameL -and $sameT) {\n    exit\n  }\n  [WinMove]::ShowWindow($hWnd, 9) | Out-Null\n  Start-Sleep -Milliseconds 300\n  $candW = $width\n  $candH = $height\n  for ($i = 0; $i -lt 3; $i++) {\n    [WinMove]::MoveWindow($hWnd, $left, $top, $candW, $candH, $true) | Out-Null\n    Start-Sleep -Milliseconds 300\n    $r = New-Object WinMove+RECT\n    [WinMove]::GetWindowRect($hWnd, [ref]$r) | Out-Null\n    $actualW = $r.Right - $r.Left\n    $actualH = $r.Bottom - $r.Top\n    if ([math]::Abs($actualW - $width) -le 2 -and [math]::Abs($actualH - $height) -le 2) { break }\n    if ($actualW -le 0 -or $actualH -le 0) { break }\n    $candW = [int]([math]::Round(($width * $candW) / [double]$actualW))\n    $candH = [int]([math]::Round(($height * $candH) / [double]$actualH))\n  }\n}\n",
    'osType.ps1': "param([string]$b64)\nAdd-Type -AssemblyName System.Windows.Forms\n$text = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($b64))\n$special = '+^%~(){}[]'\nStart-Sleep -Milliseconds 200\nforeach ($ch in $text.ToCharArray()) {\n  if ($ch -eq \"`n\") { [System.Windows.Forms.SendKeys]::SendWait(\"{ENTER}\"); Start-Sleep -Milliseconds 15; continue }\n  if ($ch -eq \"`r\") { continue }\n  $s = [string]$ch\n  if ($special.IndexOf($ch) -ge 0) { $s = \"{$ch}\" }\n  [System.Windows.Forms.SendKeys]::SendWait($s)\n  Start-Sleep -Milliseconds 15\n}\n",
    'osEscape.ps1': "Add-Type -AssemblyName System.Windows.Forms\nStart-Sleep -Milliseconds 100\n[System.Windows.Forms.SendKeys]::SendWait(\"{ESC}\")\n",
    'osActivate.ps1': "param([string]$titleLike, [string]$hwnd)\nAdd-Type @\"\nusing System;\nusing System.Text;\nusing System.Collections.Generic;\nusing System.Runtime.InteropServices;\npublic class WinActivate {\n  public delegate bool EnumProc(IntPtr h, IntPtr l);\n  [DllImport(\"user32.dll\")] public static extern bool EnumWindows(EnumProc p, IntPtr l);\n  [DllImport(\"user32.dll\")] public static extern bool IsWindowVisible(IntPtr h);\n  [DllImport(\"user32.dll\")] public static extern int GetWindowTextLength(IntPtr h);\n  [DllImport(\"user32.dll\", CharSet=CharSet.Auto)] public static extern int GetWindowText(IntPtr h, StringBuilder sb, int m);\n  [DllImport(\"user32.dll\")] public static extern bool ShowWindow(IntPtr h, int n);\n  [DllImport(\"user32.dll\")] public static extern bool SetForegroundWindow(IntPtr h);\n  [DllImport(\"user32.dll\")] public static extern bool BringWindowToTop(IntPtr h);\n  [DllImport(\"user32.dll\")] public static extern IntPtr GetForegroundWindow();\n  [DllImport(\"user32.dll\")] public static extern uint GetWindowThreadProcessId(IntPtr h, IntPtr pid);\n  [DllImport(\"user32.dll\")] public static extern bool AttachThreadInput(uint a, uint b, bool f);\n  [DllImport(\"kernel32.dll\")] public static extern uint GetCurrentThreadId();\n  [DllImport(\"user32.dll\")] public static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint flags);\n  public static List<IntPtr> Find(string t) {\n    var f = new List<IntPtr>();\n    EnumWindows((h,l) => { if(!IsWindowVisible(h)) return true; int n=GetWindowTextLength(h); if(n==0) return true;\n      var sb=new StringBuilder(n+1); GetWindowText(h,sb,sb.Capacity); if(sb.ToString().Contains(t)) f.Add(h); return true; }, IntPtr.Zero);\n    return f;\n  }\n  public static void Force(IntPtr h) {\n    ShowWindow(h, 9); // SW_RESTORE\n    uint fg = GetWindowThreadProcessId(GetForegroundWindow(), IntPtr.Zero);\n    uint me = GetCurrentThreadId();\n    if (fg != me) AttachThreadInput(me, fg, true);\n    BringWindowToTop(h); SetForegroundWindow(h);\n    SetWindowPos(h, (IntPtr)(-1), 0,0,0,0, 0x0003); // TOPMOST, NOMOVE|NOSIZE\n    SetWindowPos(h, (IntPtr)(-2), 0,0,0,0, 0x0003); // NOTOPMOST\n    if (fg != me) AttachThreadInput(me, fg, false);\n  }\n}\n\"@ -ErrorAction SilentlyContinue\nif ($hwnd) {\n  [WinActivate]::Force([IntPtr]([int64]$hwnd)); Start-Sleep -Milliseconds 250\n} else {\n  $m = [WinActivate]::Find($titleLike)\n  if ($m.Count -gt 0) { [WinActivate]::Force($m[0]); Start-Sleep -Milliseconds 250 }\n}\n",
    'osDismissPopup.ps1': "param([string]$titleLike, [string]$hwnd, [string]$exclude)\ntry { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}\nAdd-Type -AssemblyName UIAutomationClient, UIAutomationTypes -ErrorAction SilentlyContinue\nAdd-Type @\"\nusing System;\nusing System.Text;\nusing System.Collections.Generic;\nusing System.Runtime.InteropServices;\npublic class PopupWin {\n  public delegate bool EnumProc(IntPtr hWnd, IntPtr lParam);\n  [DllImport(\"user32.dll\")] public static extern bool EnumWindows(EnumProc proc, IntPtr lParam);\n  [DllImport(\"user32.dll\")] public static extern bool IsWindowVisible(IntPtr hWnd);\n  [DllImport(\"user32.dll\")] public static extern bool IsWindow(IntPtr hWnd);\n  [DllImport(\"user32.dll\")] public static extern int GetWindowTextLength(IntPtr hWnd);\n  [DllImport(\"user32.dll\", CharSet = CharSet.Auto)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder sb, int max);\n  [DllImport(\"user32.dll\", CharSet = CharSet.Auto)] public static extern int GetClassName(IntPtr hWnd, StringBuilder sb, int max);\n  [DllImport(\"user32.dll\")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);\n  [DllImport(\"user32.dll\")] public static extern IntPtr SendMessage(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);\n  [DllImport(\"user32.dll\")] public static extern IntPtr GetWindow(IntPtr hWnd, uint cmd);\n  public static IntPtr OwnerOf(IntPtr h) { return GetWindow(h, 4); } // GW_OWNER\n  public static List<IntPtr> AllTop() {\n    var f = new List<IntPtr>();\n    EnumWindows((h, l) => { if (IsWindowVisible(h)) f.Add(h); return true; }, IntPtr.Zero);\n    return f;\n  }\n  public static string ClassOf(IntPtr h) {\n    var sb = new StringBuilder(256);\n    GetClassName(h, sb, sb.Capacity);\n    return sb.ToString();\n  }\n  public static string TitleOf(IntPtr h) {\n    int len = GetWindowTextLength(h);\n    if (len == 0) return \"\";\n    var sb = new StringBuilder(len + 1);\n    GetWindowText(h, sb, sb.Capacity);\n    return sb.ToString();\n  }\n  public static uint PidOf(IntPtr h) {\n    uint pid; GetWindowThreadProcessId(h, out pid); return pid;\n  }\n}\n\"@ -ErrorAction SilentlyContinue\n\n$mainHwnd = [IntPtr]::Zero\nif ($hwnd) { $mainHwnd = [IntPtr]([int64]$hwnd) }\nelseif ($titleLike) {\n  foreach ($h in [PopupWin]::AllTop()) {\n    if ([PopupWin]::TitleOf($h).Contains($titleLike)) { $mainHwnd = $h; break }\n  }\n}\n$targetPid = 0\nif ($mainHwnd -ne [IntPtr]::Zero) { $targetPid = [PopupWin]::PidOf($mainHwnd) }\n\n$excludeSet = New-Object 'System.Collections.Generic.HashSet[long]'\nif ($exclude) {\n  foreach ($tok in ($exclude -split ',')) {\n    $t = $tok.Trim()\n    if ($t) { [void]$excludeSet.Add([int64]$t) }\n  }\n}\n\n# Candidate = dialog-shaped window of the target process only:\n#  - same PID AND (#32770 class OR owned window)  → native/Electron/Qt dialogs\n#  - #32770 owned by a target-PID window          → dialog hosted out-of-process\n# Never: unowned main-class windows (a sibling VS Code window shares the PID\n# but is nobody's popup), excluded hwnds (windows the replay itself drives),\n# or windows of unrelated processes. If no target PID could be resolved at\n# all, dismiss nothing — guessing across the whole desktop is how an\n# unrelated app loses a dialog.\n$candidates = New-Object System.Collections.Generic.List[IntPtr]\nif ($targetPid -ne 0) {\n  foreach ($h in [PopupWin]::AllTop()) {\n    if ($h -eq $mainHwnd) { continue }\n    if ($excludeSet.Contains([int64]$h)) { continue }\n    $isDialogClass = ([PopupWin]::ClassOf($h) -eq '#32770')\n    $owner = [PopupWin]::OwnerOf($h)\n    $qualifies = $false\n    if ([PopupWin]::PidOf($h) -eq $targetPid) {\n      $qualifies = ($isDialogClass -or ($owner -ne [IntPtr]::Zero))\n    } elseif ($isDialogClass -and $owner -ne [IntPtr]::Zero -and [PopupWin]::PidOf($owner) -eq $targetPid) {\n      $qualifies = $true\n    }\n    if ($qualifies) { $candidates.Add($h) }\n  }\n}\n\n# Conservative order: no-side-effect dismissal first (Cancel/No/Close), only\n# reach for an affirmative (OK/Yes) if nothing safer matched — a wrong click\n# here should never silently accept a destructive action.\n$preferred = @('취소', '아니요', '닫기', 'Cancel', 'No', 'Close', '확인', 'OK', '예', 'Yes')\n\n$dismissed = $false\nforeach ($h in $candidates) {\n  if ($dismissed) { break }\n  try {\n    $el = [System.Windows.Automation.AutomationElement]::FromHandle($h)\n    if (-not $el) { continue }\n    $cond = New-Object System.Windows.Automation.PropertyCondition(\n      [System.Windows.Automation.AutomationElement]::ControlTypeProperty,\n      [System.Windows.Automation.ControlType]::Button)\n    $buttons = $el.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cond)\n    foreach ($btnName in $preferred) {\n      if ($dismissed) { break }\n      foreach ($b in $buttons) {\n        if ($b.Current.Name -ne $btnName) { continue }\n        $clicked = $false\n        try {\n          $inv = $b.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)\n          $inv.Invoke()\n          $clicked = $true\n        } catch {\n          try {\n            $legacy = $b.GetCurrentPattern([System.Windows.Automation.LegacyIAccessiblePattern]::Pattern)\n            $legacy.DoDefaultAction()\n            $clicked = $true\n          } catch {\n            # Owner-drawn / non-standard control — last resort: BM_CLICK via SendMessage.\n            try {\n              $bh = [IntPtr]$b.Current.NativeWindowHandle\n              if ($bh -ne [IntPtr]::Zero) { [PopupWin]::SendMessage($bh, 0x00F5, [IntPtr]::Zero, [IntPtr]::Zero) | Out-Null; $clicked = $true }\n            } catch {}\n          }\n        }\n        if ($clicked) {\n          $deadline = (Get-Date).AddSeconds(3)\n          while ((Get-Date) -lt $deadline -and [PopupWin]::IsWindow($h)) { Start-Sleep -Milliseconds 100 }\n          Write-Output \"DISMISSED|$btnName|$([PopupWin]::TitleOf($h))\"\n          $dismissed = $true\n        }\n        break\n      }\n    }\n  } catch {}\n}\nif (-not $dismissed) { Write-Output \"NONE\" }\n",
    'osScroll.py': "import sys, json, base64, argparse, ctypes\nfrom ctypes import wintypes\n\nif sys.stdout.encoding and sys.stdout.encoding.lower() not in (\"utf-8\", \"utf8\"):\n    sys.stdout.reconfigure(encoding=\"utf-8\", errors=\"replace\")\nif sys.stderr.encoding and sys.stderr.encoding.lower() not in (\"utf-8\", \"utf8\"):\n    sys.stderr.reconfigure(encoding=\"utf-8\", errors=\"replace\")\n\nimport comtypes\nimport comtypes.client\n\nUIA_NameProperty = 30005\nUIA_AutomationIdProperty = 30011\nUIA_ClassNameProperty = 30012\nUIA_ScrollPatternId = 10004\nTreeScope_Descendants = 4\nWM_MOUSEWHEEL = 0x020A\n# ScrollAmount enum (UIAutomationClient.h): LargeDecrement=0, SmallDecrement=1,\n# NoAmount=2, LargeIncrement=3, SmallIncrement=4.\nSCROLL_NO_AMOUNT = 2\nSCROLL_SMALL_DECREMENT = 1\nSCROLL_SMALL_INCREMENT = 4\n\nuser32 = ctypes.windll.user32\nuser32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]\nuser32.PostMessageW.restype = wintypes.BOOL\n\n\ndef find_target(uia, root, sel):\n    # 캡처 시점에 agent.py가 ScrollPattern 보유 조상으로 걸어 올라가 기록한\n    # 컨테이너 셀렉터 — PS1과 동일하게 automationId/className/name 순으로\n    # 단독 조건을 하나씩 시도(AND 아님).\n    if sel:\n        for prop, key in ((UIA_AutomationIdProperty, \"automationId\"),\n                           (UIA_ClassNameProperty, \"className\"),\n                           (UIA_NameProperty, \"name\")):\n            if sel.get(key):\n                try:\n                    cond = uia.CreatePropertyCondition(prop, sel[key])\n                    t = root.FindFirst(TreeScope_Descendants, cond)\n                    if t:\n                        return t\n                except Exception:\n                    pass\n    return root\n\n\ndef main():\n    ap = argparse.ArgumentParser()\n    ap.add_argument(\"--hwnd\", type=int, required=True)\n    ap.add_argument(\"--sel-b64\", default=\"\")\n    ap.add_argument(\"--delta\", type=int, required=True)\n    args = ap.parse_args()\n\n    if not args.hwnd:\n        print(\"osScroll: --hwnd is required\", file=sys.stderr)\n        sys.exit(2)\n\n    comtypes.CoInitialize()\n    mod = comtypes.client.GetModule(\"UIAutomationCore.dll\")\n    uia = comtypes.client.CreateObject(\n        \"{ff48dba4-60ef-4201-aa87-54103eef594e}\", interface=mod.IUIAutomation\n    )\n\n    try:\n        root = uia.ElementFromHandle(args.hwnd)\n    except Exception as e:\n        print(f\"osScroll: ElementFromHandle raised: {e}\", file=sys.stderr)\n        sys.exit(2)\n    if not root:\n        print(\"osScroll: ElementFromHandle failed\", file=sys.stderr)\n        sys.exit(2)\n\n    sel = None\n    if args.sel_b64:\n        try:\n            sel = json.loads(base64.b64decode(args.sel_b64).decode(\"utf-8\"))\n        except Exception:\n            sel = None\n    target = find_target(uia, root, sel)\n\n    # 1차: 대상(또는 가장 가까운 스크롤 가능 조상)의 ScrollPattern.\n    walker = uia.ControlViewWalker\n    cur = target\n    scroll = None\n    for _ in range(10):\n        if cur is None:\n            break\n        try:\n            p = cur.GetCurrentPattern(UIA_ScrollPatternId)\n            if p:\n                sp = p.QueryInterface(mod.IUIAutomationScrollPattern)\n                if sp.CurrentVerticallyScrollable:\n                    scroll = sp\n                    break\n        except Exception:\n            pass\n        try:\n            cur = walker.GetParentElement(cur)\n        except Exception:\n            break\n\n    if scroll:\n        try:\n            before = scroll.CurrentVerticalScrollPercent\n        except Exception:\n            before = None\n        # 휠 업(양수 delta) = 콘텐츠 위로 = SmallDecrement. 노치당 약 3줄.\n        direction = SCROLL_SMALL_DECREMENT if args.delta > 0 else SCROLL_SMALL_INCREMENT\n        n = abs(args.delta) * 3\n        # Scroll()은 PS1 원본에도 예외처리가 없던 자리 — 콤보 팝업이 스크롤\n        # 도중 상태를 바꾸면(자동 닫힘 등) 반복 호출 중 COM 예외를 던져 스크립트\n        # 전체가 죽고, 그게 _step()의 ESC 복구로 이어져 다이얼로그 기반 앱\n        # (ESC==Cancel)을 통째로 닫혀버리게 만드는 것을 실측(2026-07-14, PuTTY\n        # STEP 6). 1회라도 성공했으면 성공으로 보고하고, 중간에 실패하면 그\n        # 지점에서 멈추고 아래로 흘려보낸다 — 한 번도 못 돌렸으면(scrolled==0)\n        # ScrollPattern 자체가 못 미더운 것으로 보고 PostMessageW 폴백으로.\n        scrolled = 0\n        for _ in range(n):\n            try:\n                scroll.Scroll(SCROLL_NO_AMOUNT, direction)\n                scrolled += 1\n            except Exception as e:\n                print(f\"[osScroll] WARN Scroll() failed after {scrolled}/{n} notches: {e}\")\n                break\n        if scrolled > 0:\n            try:\n                after = scroll.CurrentVerticalScrollPercent\n            except Exception:\n                after = None\n            print(f\"[osScroll] ScrollPattern {before} -> {after} (delta={args.delta}, {scrolled}/{n} notches applied)\")\n            sys.exit(0)\n        print(\"[osScroll] WARN ScrollPattern found but Scroll() failed immediately — falling back to PostMessageW\")\n\n    # 2차: hwnd-scoped WM_MOUSEWHEEL (PostMessageW — 비동기, SendMessage 금지).\n    post_h = args.hwnd\n    cur = target\n    for _ in range(10):\n        if cur is None:\n            break\n        try:\n            nh = cur.CurrentNativeWindowHandle\n            if nh:\n                post_h = nh\n                break\n            cur = walker.GetParentElement(cur)\n        except Exception:\n            break\n\n    cx = cy = 0\n    try:\n        r = target.CurrentBoundingRectangle\n        cx = int(r.left + (r.right - r.left) / 2)\n        cy = int(r.top + (r.bottom - r.top) / 2)\n    except Exception:\n        pass\n\n    wparam = ((args.delta * 120) << 16) & 0xFFFFFFFFFFFFFFFF\n    lparam = ((cy & 0xFFFF) << 16) | (cx & 0xFFFF)\n    user32.PostMessageW(post_h, WM_MOUSEWHEEL, wparam, lparam)\n    print(f\"[osScroll] PostMessageW WM_MOUSEWHEEL hwnd={post_h} delta={args.delta} (ScrollPattern unavailable)\")\n\n\nif __name__ == \"__main__\":\n    main()\n",
    'osExpandCollapse.py': "import os, sys, json, base64, argparse, ctypes, time\nfrom ctypes import wintypes\n\nif sys.stdout.encoding and sys.stdout.encoding.lower() not in (\"utf-8\", \"utf8\"):\n    sys.stdout.reconfigure(encoding=\"utf-8\", errors=\"replace\")\nif sys.stderr.encoding and sys.stderr.encoding.lower() not in (\"utf-8\", \"utf8\"):\n    sys.stderr.reconfigure(encoding=\"utf-8\", errors=\"replace\")\n\nimport comtypes\nimport comtypes.client\n\nUIA_NameProperty = 30005\nUIA_AutomationIdProperty = 30011\nUIA_ClassNameProperty = 30012\nUIA_InvokePatternId = 10000\nUIA_ExpandCollapsePatternId = 10005\nUIA_SelectionItemPatternId = 10010\nUIA_LegacyIAccessiblePatternId = 10018\nUIA_SELECTIONFLAG_TAKESELECTION = 1\nUIA_ScrollPatternId = 10004\nUIA_ControlTypeProperty = 30003\nUIA_ListItem = 50007\nTreeScope_Descendants = 4\nTreeScope_Subtree = 7\nExpandCollapseState_Expanded = 1\n\nuser32 = ctypes.windll.user32\n\n# ── dynamic ClickablePoint + SendInput (2026-07-24) ─────────────────────────\n# 녹화된 좌표는 여기 어디에도 들어오지 않는다. 좌표는 매 실행마다 UIA가 방금\n# resolve한 요소로부터 계산해 즉시 소비하고 버린다 — 창이 이동/리사이즈되거나\n# 해상도가 바뀌어도 항상 새로 계산되므로 §3 금지의 원래 취지(저장된 좌표가\n# 재생 시점에 어긋나는 것)를 건드리지 않는다.\nSM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77\nSM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79\nMOUSEEVENTF_MOVE = 0x0001\nMOUSEEVENTF_LEFTDOWN = 0x0002\nMOUSEEVENTF_LEFTUP = 0x0004\nMOUSEEVENTF_VIRTUALDESK = 0x4000\nMOUSEEVENTF_ABSOLUTE = 0x8000\nINPUT_MOUSE = 0\n\nULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong\n\n\nclass MOUSEINPUT(ctypes.Structure):\n    _fields_ = [(\"dx\", wintypes.LONG), (\"dy\", wintypes.LONG),\n                (\"mouseData\", wintypes.DWORD), (\"dwFlags\", wintypes.DWORD),\n                (\"time\", wintypes.DWORD), (\"dwExtraInfo\", ULONG_PTR)]\n\n\nclass _INPUTUNION(ctypes.Union):\n    _fields_ = [(\"mi\", MOUSEINPUT)]\n\n\nclass INPUT(ctypes.Structure):\n    _anonymous_ = (\"u\",)\n    _fields_ = [(\"type\", wintypes.DWORD), (\"u\", _INPUTUNION)]\n\n\ndef enable_per_monitor_dpi():\n    # agent.py의 _enable_per_monitor_dpi_awareness()와 동일한 근거로 필수:\n    # 파이썬 프로세스는 기본 DPI-unaware라 125% 스케일 환경에서 UIA가 돌려주는\n    # rect/ClickablePoint가 가상화된 논리 좌표로 오는 반면 SendInput의 절대\n    # 좌표계는 물리 픽셀이다 — 격상하지 않으면 두 좌표계가 어긋나 엉뚱한\n    # 지점을 클릭한다. UIA 객체를 만들기 전에 호출해야 한다.\n    try:\n        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))\n        return\n    except Exception:\n        pass\n    try:\n        ctypes.windll.shcore.SetProcessDpiAwareness(2)\n    except Exception:\n        pass\n\n\ndef clickable_point(el):\n    \"\"\"UIA가 지금 이 순간 계산한 클릭 지점. 실패하면 None.\"\"\"\n    try:\n        res = el.GetClickablePoint()\n    except Exception:\n        res = None\n    # comtypes는 [out] 파라미터 2개(POINT, BOOL)를 튜플로 돌려주지만 바인딩\n    # 버전에 따라 POINT 하나만 오는 경우도 있어 양쪽을 모두 받아준다.\n    if res is not None:\n        pt, got = (res if isinstance(res, (tuple, list)) and len(res) == 2 else (res, 1))\n        if got and pt is not None:\n            try:\n                return int(pt.x), int(pt.y)\n            except Exception:\n                pass\n    try:\n        r = el.CurrentBoundingRectangle\n        if r.right > r.left and r.bottom > r.top:\n            return (r.left + r.right) // 2, (r.top + r.bottom) // 2\n    except Exception:\n        pass\n    return None\n\n\ndef _same_or_descendant(uia, ancestor, el, max_up=6):\n    cur = el\n    try:\n        walker = uia.RawViewWalker\n    except Exception:\n        walker = None\n    for _ in range(max_up + 1):\n        try:\n            if uia.CompareElements(ancestor, cur):\n                return True\n        except Exception:\n            return False\n        if not walker:\n            return False\n        try:\n            cur = walker.GetParentElement(cur)\n        except Exception:\n            return False\n        if not cur:\n            return False\n    return False\n\n\ndef send_input_click(uia, el, tag):\n    \"\"\"UIA로 방금 찾은 요소를 실제 마우스 입력으로 클릭한다.\n\n    안전 검증을 하나라도 통과 못 하면 사유를 남기고 False — 호출자는 기존\n    프로그래매틱 Invoke()/Select() 체인으로 폴백한다(에러로 튕기는 것보다\n    비시각적으로라도 동작하는 게 낫다는 2026-07-24 지시).\n    \"\"\"\n    def bail(reason):\n        print(\"[COM-SendInput] fallback: \" + reason + \" - using programmatic Invoke/Select\", file=sys.stderr)\n        return False\n\n    if os.environ.get(\"QAFORGE_COM_CLICK\") == \"invoke\":\n        return bail(\"forced-programmatic (QAFORGE_COM_CLICK=invoke)\")\n    try:\n        if el.CurrentIsOffscreen:\n            return bail(\"offscreen\")\n    except Exception:\n        pass\n\n    # 라벨은 반드시 주입 **전에** 읽는다. 메뉴 항목/다이얼로그 버튼은 클릭\n    # 즉시 파괴돼 그 뒤의 프로퍼티 읽기가 실패하고, 로그가 '?'로 남아 추적이\n    # 불가능해진다(2026-07-24 FileZilla 실측: 메뉴 항목/예(Y)/취소 전부 '?',\n    # 살아남는 콤보 화살표만 '닫기'로 정상 출력).\n    try:\n        label = el.CurrentName or el.CurrentAutomationId or \"?\"\n    except Exception:\n        label = \"?\"\n\n    pt = clickable_point(el)\n    if not pt:\n        return bail(\"no-clickable-point\")\n    x, y = pt\n    vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)\n    vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)\n    vw = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)\n    vh = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)\n    if vw <= 1 or vh <= 1 or not (vx <= x < vx + vw and vy <= y < vy + vh):\n        return bail(\"point-outside-virtual-screen (%d,%d)\" % (x, y))\n\n    # 그 지점이 정말 대상 요소의 것인지 두 겹으로 확인한다. 2026-07-15에\n    # osScopedInvoke가 완전히 남남인 사용자 창(탐색기/VS Code)을 클릭하고\n    # \"성공\"으로 보고한 사고, 2026-07-13에 클릭 지점이 요소 rect 밖이라\n    # 물리적으로 no-op이던 이벤트가 재생 때는 요소 중심을 클릭해 엉뚱한 패널을\n    # 연 트랩 — 둘 다 이 검증으로 구조적으로 막힌다.\n    winpt = wintypes.POINT(int(x), int(y))\n    try:\n        hwnd_at = user32.WindowFromPoint(winpt)\n        pid_at = wintypes.DWORD()\n        user32.GetWindowThreadProcessId(hwnd_at, ctypes.byref(pid_at))\n        if pid_at.value != el.CurrentProcessId:\n            return bail(\"covered-by-other-window (pid %d at point, target pid %d)\"\n                        % (pid_at.value, el.CurrentProcessId))\n    except Exception as e:\n        return bail(\"window-hit-test-failed (%s)\" % e)\n\n    try:\n        at_point = uia.ElementFromPoint(winpt)\n    except Exception as e:\n        return bail(\"element-from-point-failed (%s)\" % e)\n    if not at_point or not _same_or_descendant(uia, el, at_point):\n        return bail(\"point-resolves-elsewhere\")\n\n    nx = int(round((x - vx) * 65535.0 / (vw - 1)))\n    ny = int(round((y - vy) * 65535.0 / (vh - 1)))\n\n    def send(flags):\n        inp = INPUT(type=INPUT_MOUSE)\n        inp.mi = MOUSEINPUT(nx, ny, 0, flags, 0, 0)\n        return user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))\n\n    # 사람이 눈으로 따라갈 수 있도록 이동/누름/뗌 사이에 간격을 둔다(§6).\n    if not send(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK):\n        return bail(\"SendInput(move) rejected\")\n    time.sleep(0.04)\n    if not send(MOUSEEVENTF_LEFTDOWN | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK):\n        return bail(\"SendInput(down) rejected\")\n    time.sleep(0.04)\n    send(MOUSEEVENTF_LEFTUP | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK)\n\n    print(\"[COM-SendInput] \" + tag + \" clicked '\" + label + \"' at (%d,%d)\" % (x, y))\n    time.sleep(0.05)\n    return True\n\n\n# ── TogglePattern (2026-07-31) ─────────────────────────────────────────────\n# 실측(TeamViewer 15.79 WebView2, poc/diag_teamviewer_a11y_wakeup.py [D]):\n# 체크박스 2종의 지원 패턴은 정확히 {Toggle, Legacy}뿐 — Invoke도\n# SelectionItem도 없다. 기존 클릭 체인(Invoke → SelectionItem → Legacy)에는\n# Toggle이 아예 없어서 앞의 둘이 연달아 예외로 떨어진 뒤\n# Legacy.Select(TAKESELECTION)이 예외 없이 통과하며 True를 반환했다. 그런데\n# 체크박스에서 \"셀렉션\"은 체크 상태를 바꾸지 않으므로, 재생은 성공으로\n# 보고되는데 화면에서는 아무 일도 일어나지 않는다 — 그 체크박스가 띄우는\n# 확인 다이얼로그도 당연히 안 뜬다(2026-07-31 클라이언트 피드백 증상 그대로).\n# Legacy 폴백보다 앞서 Toggle을 시도하되, ToggleState가 실제로 바뀐 경우에만\n# 성공으로 인정한다(거짓 PASS 방지 — 2026-07-13 3차 교훈과 같은 원칙).\nUIA_TogglePatternId = 10015\n\n\ndef toggle_item(mod, el, tag):\n    try:\n        tp = el.GetCurrentPattern(UIA_TogglePatternId).QueryInterface(\n            mod.IUIAutomationTogglePattern)\n    except Exception:\n        return False\n    try:\n        before = tp.CurrentToggleState\n        tp.Toggle()\n        time.sleep(0.05)\n        after = tp.CurrentToggleState\n    except Exception as e:\n        print(\"[\" + tag + \"] TogglePattern.Toggle() raised: %s\" % e, file=sys.stderr)\n        return False\n    if after != before:\n        print(\"[\" + tag + \"] TogglePattern.Toggle() %d -> %d\" % (before, after))\n        return True\n    print(\"[\" + tag + \"] TogglePattern.Toggle() left ToggleState unchanged (%d)\"\n          \" - falling through to the remaining patterns\" % before, file=sys.stderr)\n    return False\n\n\ndef top_windows():\n    found = []\n\n    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)\n    def cb(hwnd, _):\n        if user32.IsWindowVisible(hwnd):\n            found.append(hwnd)\n        return True\n\n    user32.EnumWindows(cb, 0)\n    return found\n\n\n# 2026-07-24: WAD의 element/click은 오프스크린(스크롤로 안 보이는) 요소를\n# 자동으로 스크롤-인-뷰한 뒤 클릭한다(암묵적 동작) — 이 파일이 다루는 COM\n# 경로(WAD 세션이 못 보는 팝업/새 창의 좁은 예외)에는 그 자동 스크롤이 없어,\n# Expand() 이후 메뉴/드롭다운 항목이 화면 밖에 있으면 Invoke 전에 조상\n# ScrollPattern으로 끌어와야 한다. 좌표는 쓰지 않는다(§3).\ndef find_scrollable_ancestor(uia, el, max_up=8):\n    try:\n        walker = uia.RawViewWalker\n    except Exception:\n        return None\n    cur = el\n    for _ in range(max_up):\n        try:\n            cur.GetCurrentPattern(UIA_ScrollPatternId)\n            return cur\n        except Exception:\n            pass\n        try:\n            cur = walker.GetParentElement(cur)\n            if not cur:\n                return None\n        except Exception:\n            return None\n    return None\n\n\ndef ensure_visible(uia, mod, el):\n    try:\n        if not el.CurrentIsOffscreen:\n            return\n    except Exception:\n        return\n    print(\"[osExpandCollapse] target is offscreen — attempting scroll-into-view via ancestor ScrollPattern\", file=sys.stderr)\n    ancestor = find_scrollable_ancestor(uia, el)\n    if not ancestor:\n        return\n    try:\n        sp = ancestor.GetCurrentPattern(UIA_ScrollPatternId).QueryInterface(mod.IUIAutomationScrollPattern)\n        sp.SetScrollPercent(50.0, 50.0)\n        time.sleep(0.2)\n    except Exception:\n        pass\n\n\ndef field_conds(uia, sel):\n    conds = []\n    if sel.get(\"automationId\"):\n        conds.append(uia.CreatePropertyCondition(UIA_AutomationIdProperty, sel[\"automationId\"]))\n    if sel.get(\"name\"):\n        conds.append(uia.CreatePropertyCondition(UIA_NameProperty, sel[\"name\"]))\n    if sel.get(\"className\"):\n        conds.append(uia.CreatePropertyCondition(UIA_ClassNameProperty, sel[\"className\"]))\n    return conds\n\n\ndef resolve_target(uia, root, sel):\n    # PuTTY류 다이얼로그는 카테고리 패널마다 숫자 AutomationId를 재사용한다\n    # (2026-07-13 실측: id=1044가 라디오 버튼과 \"Proxy type:\" 콤보에 동시에 붙음)\n    # — 있는 필드를 전부 AND로 묶은 조건을 먼저 시도해 모호성을 없애고, 그래도\n    # 못 찾으면 필드별 단독 조건으로 폴백.\n    conds = field_conds(uia, sel)\n    if not conds:\n        return None\n    if len(conds) > 1:\n        combined = conds[0]\n        for c in conds[1:]:\n            combined = uia.CreateAndCondition(combined, c)\n        try:\n            t = root.FindFirst(TreeScope_Descendants, combined)\n            if t:\n                return t\n        except Exception:\n            pass\n    for c in conds:\n        try:\n            t = root.FindFirst(TreeScope_Descendants, c)\n            if t:\n                return t\n        except Exception:\n            continue\n    return None\n\n\ndef invoke_item(uia, mod, el):\n    ensure_visible(uia, mod, el)\n    try:\n        el.SetFocus()\n    except Exception:\n        pass\n    # 시각적 재생 우선(2026-07-24, §6) — 성공하면 반드시 여기서 반환한다.\n    # 이어서 Invoke()까지 부르면 같은 동작이 두 번 실행된다.\n    if send_input_click(uia, el, \"osExpandCollapse\"):\n        return True\n    try:\n        el.GetCurrentPattern(UIA_InvokePatternId).QueryInterface(mod.IUIAutomationInvokePattern).Invoke()\n        return True\n    except Exception:\n        pass\n    if toggle_item(mod, el, \"osExpandCollapse\"):\n        return True\n    try:\n        el.GetCurrentPattern(UIA_SelectionItemPatternId).QueryInterface(mod.IUIAutomationSelectionItemPattern).Select()\n        return True\n    except Exception:\n        pass\n    try:\n        legacy = el.GetCurrentPattern(UIA_LegacyIAccessiblePatternId).QueryInterface(mod.IUIAutomationLegacyIAccessiblePattern)\n        try:\n            legacy.Select(UIA_SELECTIONFLAG_TAKESELECTION)\n            return True\n        except Exception:\n            pass\n        legacy.DoDefaultAction()\n        return True\n    except Exception:\n        return False\n\n\ndef main():\n    ap = argparse.ArgumentParser()\n    ap.add_argument(\"--hwnd\", type=int, required=True)\n    ap.add_argument(\"--sel-b64\", required=True)\n    ap.add_argument(\"--item-name-b64\", default=None)\n    # 2026-07-31: ComboBoxEx(HeidiSQL 네트워크 유형)처럼 항목 Name이 전부 빈\n    # owner-drawn 드롭다운은 이름으로 지목할 수 없다. 목록 안에서의 순서로만\n    # 구별 가능하므로 인덱스를 받는다(좌표가 아니라 구조적 순서 —\n    # ListItem/TreeItem/DataItem 슬롯 인덱스와 같은 원리).\n    ap.add_argument(\"--item-index\", type=int, default=None)\n    ap.add_argument(\"--item-count\", type=int, default=None)\n    args = ap.parse_args()\n\n    if not args.hwnd:\n        print(\"osExpandCollapse: --hwnd is required\", file=sys.stderr)\n        sys.exit(2)\n\n    enable_per_monitor_dpi()\n    comtypes.CoInitialize()\n    mod = comtypes.client.GetModule(\"UIAutomationCore.dll\")\n    uia = comtypes.client.CreateObject(\n        \"{ff48dba4-60ef-4201-aa87-54103eef594e}\", interface=mod.IUIAutomation\n    )\n\n    # 2026-07-24 실측(FileZilla 재현): 방금 그 hwnd에 WinAppDriver scoped\n    # session이 막 생성된 직후(재생 로그: \"scoped session on 0x301010 ready\n    # in 1354ms\" 다음 STEP에서 곧바로) 이 프로세스의 별도 IUIAutomation COM\n    # 클라이언트가 같은 hwnd에 ElementFromHandle()을 호출하면 간헐적으로\n    # COMError(-2147220991)가 난다 — WinAppDriver의 내부 UIA 클라이언트가 그\n    # 창에 이벤트 구독을 마치기 전 레이스로 추정(에러 메시지 자체가 \"이벤트\n    # 구독자를 불러올 수 없음\"). 셀렉터/로직 문제가 아니라 타이밍 문제이므로,\n    # 실패로 단정하기 전에 짧게 재시도한다(osScopedInvoke.py의 4회 재시도와\n    # 같은 근거).\n    root = None\n    for attempt in range(4):\n        if attempt > 0:\n            time.sleep(0.3)\n        try:\n            root = uia.ElementFromHandle(args.hwnd)\n        except Exception as e:\n            root = None\n            if attempt == 3:\n                print(f\"osExpandCollapse: ElementFromHandle failed: {e}\", file=sys.stderr)\n        if root:\n            break\n    if not root:\n        print(\"osExpandCollapse: ElementFromHandle failed\", file=sys.stderr)\n        sys.exit(2)\n\n    sel = json.loads(base64.b64decode(args.sel_b64).decode(\"utf-8\"))\n    target = resolve_target(uia, root, sel)\n    if not target:\n        print(f\"osExpandCollapse: target element not found (sel={args.sel_b64})\", file=sys.stderr)\n        sys.exit(2)\n\n    item_name = None\n    if args.item_name_b64:\n        item_name = base64.b64decode(args.item_name_b64).decode(\"utf-8\")\n\n    # 2026-07-23 실측(FileZilla \"네트워크 구성 마법사(N)...\" 재현): agent.py의\n    # expandCollapse 태깅은 UIA의 \"IsExpandCollapsePatternAvailable\" 구조적\n    # 응답만 보는데, wx는 서브메뉴가 없는 리프 커맨드 MenuItem에도 이걸 true로\n    # 보고하는 경우가 있다(실제 GetCurrentPattern()/Expand() 호출 시점에야\n    # 드러남 — 캡처 시점 검사와 재생 시점 COM 호출 결과가 다름). item_name이\n    # 없는 단독 토글 이벤트(병합 안 된 경우)에서 이게 벌어지면, 그 클릭은\n    # 원래 \"메뉴 펼치기\"가 아니라 \"커맨드 실행\"이었다는 뜻이므로, 실패로\n    # 끝내는 대신 평범한 클릭(Invoke/Select/LegacyIAccessible)으로 폴백해\n    # 실제 유저가 한 동작(메뉴 항목 실행)을 재현한다. item_name이 있는\n    # (병합된 진짜 서브메뉴) 경우는 mergeExpandCollapseClicks의 rootHwndHex\n    # 창-경계 가드가 이제 이런 리프 커맨드를 트리거로 병합하지 않으므로\n    # 여기까지 오지 않는다 — 그 경로는 기존처럼 실패로 남겨 무엇이 잘못됐는지\n    # 숨기지 않는다.\n    try:\n        ecp = target.GetCurrentPattern(UIA_ExpandCollapsePatternId).QueryInterface(\n            mod.IUIAutomationExpandCollapsePattern)\n    except Exception:\n        if not item_name and invoke_item(uia, mod, target):\n            print(\"[osExpandCollapse] ExpandCollapsePattern unavailable — invoked as a plain command instead\")\n            sys.exit(0)\n        print(\"osExpandCollapse: ExpandCollapsePattern not supported on target\", file=sys.stderr)\n        sys.exit(2)\n\n    # 새 팝업 창(네이티브 TrackPopupMenu 등) 감지용 베이스라인은 Expand() 전에\n    # 찍는다 — FileZilla 메뉴바처럼 하위 항목이 그 팝업 서브트리에만 생기는 경우.\n    baseline = set(top_windows())\n\n    try:\n        if ecp.CurrentExpandCollapseState != ExpandCollapseState_Expanded:\n            ecp.Expand()\n        else:\n            ecp.Collapse()\n            time.sleep(0.2)\n            ecp.Expand()\n    except Exception as e:\n        if not item_name and invoke_item(uia, mod, target):\n            print(\"[osExpandCollapse] Expand() failed — invoked as a plain command instead\")\n            sys.exit(0)\n        print(f\"osExpandCollapse: Expand() failed: {e}\", file=sys.stderr)\n        sys.exit(2)\n    time.sleep(0.4)\n    print(f\"[osExpandCollapse] state after Expand() = {ecp.CurrentExpandCollapseState}\")\n\n    # ── 인덱스로 항목 선택 (2026-07-31, owner-drawn ComboBoxEx) ─────────────\n    # 항목 Name이 전부 빈 드롭다운은 이름 조건으로 못 찾는다. 펼친 뒤 보이는\n    # ListItem을 트리 순서대로 모아 N번째를 실행한다. 창 서브트리와 새로 뜬\n    # 팝업 창을 모두 훑는다(Win32 콤보는 목록을 별도 ComboLBox 창에 그리기도\n    # 한다). 좌표는 쓰지 않는다.\n    if args.item_index is not None:\n        time.sleep(0.2)\n        li_cond = uia.CreatePropertyCondition(UIA_ControlTypeProperty, UIA_ListItem)\n        pools = []\n        try:\n            pools.append((\"main window\", root.FindAll(TreeScope_Subtree, li_cond)))\n        except Exception:\n            pass\n        for h in top_windows():\n            if h in baseline:\n                continue\n            try:\n                pr = uia.ElementFromHandle(h)\n                if pr:\n                    pools.append((f\"popup hwnd={h}\", pr.FindAll(TreeScope_Subtree, li_cond)))\n            except Exception:\n                continue\n        for where, arr in pools:\n            if not arr or not arr.Length:\n                continue\n            if args.item_count and arr.Length != args.item_count:\n                print(f\"[osExpandCollapse] {where}: {arr.Length} items but the \"\n                      f\"recording saw {args.item_count} — the list changed since \"\n                      \"capture; refusing to pick by position\", file=sys.stderr)\n                continue\n            if args.item_index >= arr.Length:\n                print(f\"[osExpandCollapse] {where}: index {args.item_index} out of \"\n                      f\"range ({arr.Length} items)\", file=sys.stderr)\n                continue\n            item = arr.GetElement(args.item_index)\n            label = \"\"\n            try:\n                label = item.CurrentName or \"\"\n            except Exception:\n                pass\n            if invoke_item(uia, mod, item):\n                print(f\"[osExpandCollapse] selected item #{args.item_index} of \"\n                      f\"{arr.Length} in {where}\"\n                      + (f\" (name={label!r})\" if label else \" (unnamed item)\"))\n                sys.exit(0)\n        print(f\"osExpandCollapse: could not select item #{args.item_index} in any \"\n              \"open list\", file=sys.stderr)\n        sys.exit(2)\n\n    if not item_name:\n        # 항목 선택 없이 펼치기/접기 자체가 목적인 이벤트(예: 트리 +- 토글).\n        sys.exit(0)\n\n    item_cond = uia.CreatePropertyCondition(UIA_NameProperty, item_name)\n\n    # (a) 같은 창 서브트리에서 찾기 — PuTTY ComboBox처럼 드롭다운 항목이 세션\n    #     스코프 안에 있는 경우(2026-07-13 실측: 'SOCKS 5' 발견됨).\n    try:\n        item = root.FindFirst(TreeScope_Descendants, item_cond)\n    except Exception:\n        item = None\n    if item and invoke_item(uia, mod, item):\n        print(f\"[osExpandCollapse] invoked '{item_name}' under main window subtree\")\n        sys.exit(0)\n\n    # (b) Expand() 이후 새로 뜬 최상위 창 서브트리 — FileZilla 메뉴바처럼 하위\n    #     항목이 네이티브 팝업(#32768 등)에만 있는 경우.\n    time.sleep(0.2)\n    for h in top_windows():\n        if h in baseline:\n            continue\n        try:\n            popup_root = uia.ElementFromHandle(h)\n            if not popup_root:\n                continue\n            item = popup_root.FindFirst(TreeScope_Descendants, item_cond)\n            if item and invoke_item(uia, mod, item):\n                print(f\"[osExpandCollapse] invoked '{item_name}' under new popup hwnd={h}\")\n                sys.exit(0)\n        except Exception:\n            continue\n\n    print(f\"osExpandCollapse: item '{item_name}' not found under main window or any new popup window\", file=sys.stderr)\n    sys.exit(2)\n\n\nif __name__ == \"__main__\":\n    main()\n",
    'osScopedInvoke.py': "import os, sys, json, base64, argparse, ctypes, time\nfrom ctypes import wintypes\n\nif sys.stdout.encoding and sys.stdout.encoding.lower() not in (\"utf-8\", \"utf8\"):\n    sys.stdout.reconfigure(encoding=\"utf-8\", errors=\"replace\")\nif sys.stderr.encoding and sys.stderr.encoding.lower() not in (\"utf-8\", \"utf8\"):\n    sys.stderr.reconfigure(encoding=\"utf-8\", errors=\"replace\")\n\nimport comtypes\nimport comtypes.client\n\nUIA_NameProperty = 30005\nUIA_AutomationIdProperty = 30011\nUIA_ClassNameProperty = 30012\nUIA_ControlTypeProperty = 30003\nUIA_InvokePatternId = 10000\nUIA_SelectionItemPatternId = 10010\nUIA_ValuePatternId = 10002\nUIA_LegacyIAccessiblePatternId = 10018\n# 2026-07-23 실측(FileZilla Site Manager \"고급/전송 설정/문자셋/일반\" 카테고리\n# 탭 재현, controlType=50019=TabItem): wxWidgets의 커스텀-드로잉 Notebook 탭은\n# UIA TabItem으로 노출되지만 InvokePattern도 SelectionItemPattern도 실제로는\n# 구현하지 않는다(QueryInterface는 성공 조건에 따라 예외 없이 통과할 때도 있으나\n# Invoke()/Select() 호출 자체가 아무 효과 없이 조용히 끝나거나 예외를 던짐 —\n# 둘 다 두 로그에서 8회 재시도 전부 실패로 확인됨). wx는 MSAA(IAccessible) 쪽은\n# 안정적으로 구현하므로 LegacyIAccessiblePattern(Select 우선, 안 되면\n# DoDefaultAction)을 좌표 없는 마지막 폴백으로 시도한다 — 여전히 픽셀 좌표는\n# 전혀 쓰지 않는다(§3 Hard Rules 준수, UIA/MSAA 프로퍼티 기반 탐색만 사용).\nUIA_SELECTIONFLAG_TAKESELECTION = 1\n# 2026-07-17 (2차) 실측(FileZilla Site Manager 재생 타임스탬프 진단):\n# osScopedInvoke가 \"target not found\"로 보고한 실패 중 다수가 사실은 요소를\n# 매번 찾았는데(item=found) Invoke/SelectionItemPattern 둘 다 미지원이라\n# invoke_item()이 False를 반환한 것이었다(Tree 컨테이너 자체, Edit 필드 클릭 —\n# 둘 다 그 패턴들을 구조적으로 지원 안 함). 이런 컨트롤에 대한 \"클릭\"의 실제\n# 의도는 포커스 이동뿐이므로, 이 5종 + SetFocus 성공 시에만 성공으로 인정한다.\n# Button/MenuItem/TreeItem/ListItem 등 실제 실행 가능한 컨트롤은 이 목록에\n# 없으므로 여전히 Invoke/Select 성공을 요구한다(거짓 PASS 방지 — 2026-07-13\n# 3차 교훈: UIA 패턴 \"지원 여부\"만으로 태깅하면 과다신호가 되므로 실증된\n# ControlType으로만 좁힌다).\nPASSIVE_CONTROL_TYPES = {50004, 50030, 50033, 50018, 50023}  # Edit, Document, Pane, Tab, Tree\nUIA_ScrollPatternId = 10004\nTreeScope_Descendants = 4\n# Element(1)|Children(2)|Descendants(4) — TreeScope_Descendants alone can\n# never match the root element being searched from (UIA standard behavior),\n# so a captured click whose target IS the window itself (e.g. a dialog's own\n# className=\"#32770\" root, no automationId) is structurally unfindable with\n# Descendants-only scope. Confirmed 2026-07-16 (FileZilla Site Manager\n# dialog click failing \"target not found\" despite the window genuinely\n# being open) — use Subtree everywhere a target might be a window root.\nTreeScope_Subtree = 7\n\nuser32 = ctypes.windll.user32\n\n# ── dynamic ClickablePoint + SendInput (2026-07-24) ─────────────────────────\n# 녹화된 좌표는 여기 어디에도 들어오지 않는다. 좌표는 매 실행마다 UIA가 방금\n# resolve한 요소로부터 계산해 즉시 소비하고 버린다 — 창이 이동/리사이즈되거나\n# 해상도가 바뀌어도 항상 새로 계산되므로 §3 금지의 원래 취지(저장된 좌표가\n# 재생 시점에 어긋나는 것)를 건드리지 않는다.\nSM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77\nSM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79\nMOUSEEVENTF_MOVE = 0x0001\nMOUSEEVENTF_LEFTDOWN = 0x0002\nMOUSEEVENTF_LEFTUP = 0x0004\nMOUSEEVENTF_VIRTUALDESK = 0x4000\nMOUSEEVENTF_ABSOLUTE = 0x8000\nINPUT_MOUSE = 0\n\nULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong\n\n\nclass MOUSEINPUT(ctypes.Structure):\n    _fields_ = [(\"dx\", wintypes.LONG), (\"dy\", wintypes.LONG),\n                (\"mouseData\", wintypes.DWORD), (\"dwFlags\", wintypes.DWORD),\n                (\"time\", wintypes.DWORD), (\"dwExtraInfo\", ULONG_PTR)]\n\n\nclass _INPUTUNION(ctypes.Union):\n    _fields_ = [(\"mi\", MOUSEINPUT)]\n\n\nclass INPUT(ctypes.Structure):\n    _anonymous_ = (\"u\",)\n    _fields_ = [(\"type\", wintypes.DWORD), (\"u\", _INPUTUNION)]\n\n\ndef enable_per_monitor_dpi():\n    # agent.py의 _enable_per_monitor_dpi_awareness()와 동일한 근거로 필수:\n    # 파이썬 프로세스는 기본 DPI-unaware라 125% 스케일 환경에서 UIA가 돌려주는\n    # rect/ClickablePoint가 가상화된 논리 좌표로 오는 반면 SendInput의 절대\n    # 좌표계는 물리 픽셀이다 — 격상하지 않으면 두 좌표계가 어긋나 엉뚱한\n    # 지점을 클릭한다. UIA 객체를 만들기 전에 호출해야 한다.\n    try:\n        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))\n        return\n    except Exception:\n        pass\n    try:\n        ctypes.windll.shcore.SetProcessDpiAwareness(2)\n    except Exception:\n        pass\n\n\ndef clickable_point(el):\n    \"\"\"UIA가 지금 이 순간 계산한 클릭 지점. 실패하면 None.\"\"\"\n    try:\n        res = el.GetClickablePoint()\n    except Exception:\n        res = None\n    # comtypes는 [out] 파라미터 2개(POINT, BOOL)를 튜플로 돌려주지만 바인딩\n    # 버전에 따라 POINT 하나만 오는 경우도 있어 양쪽을 모두 받아준다.\n    if res is not None:\n        pt, got = (res if isinstance(res, (tuple, list)) and len(res) == 2 else (res, 1))\n        if got and pt is not None:\n            try:\n                return int(pt.x), int(pt.y)\n            except Exception:\n                pass\n    try:\n        r = el.CurrentBoundingRectangle\n        if r.right > r.left and r.bottom > r.top:\n            return (r.left + r.right) // 2, (r.top + r.bottom) // 2\n    except Exception:\n        pass\n    return None\n\n\ndef _same_or_descendant(uia, ancestor, el, max_up=6):\n    cur = el\n    try:\n        walker = uia.RawViewWalker\n    except Exception:\n        walker = None\n    for _ in range(max_up + 1):\n        try:\n            if uia.CompareElements(ancestor, cur):\n                return True\n        except Exception:\n            return False\n        if not walker:\n            return False\n        try:\n            cur = walker.GetParentElement(cur)\n        except Exception:\n            return False\n        if not cur:\n            return False\n    return False\n\n\ndef send_input_click(uia, el, tag):\n    \"\"\"UIA로 방금 찾은 요소를 실제 마우스 입력으로 클릭한다.\n\n    안전 검증을 하나라도 통과 못 하면 사유를 남기고 False — 호출자는 기존\n    프로그래매틱 Invoke()/Select() 체인으로 폴백한다(에러로 튕기는 것보다\n    비시각적으로라도 동작하는 게 낫다는 2026-07-24 지시).\n    \"\"\"\n    def bail(reason):\n        print(\"[COM-SendInput] fallback: \" + reason + \" - using programmatic Invoke/Select\", file=sys.stderr)\n        return False\n\n    if os.environ.get(\"QAFORGE_COM_CLICK\") == \"invoke\":\n        return bail(\"forced-programmatic (QAFORGE_COM_CLICK=invoke)\")\n    try:\n        if el.CurrentIsOffscreen:\n            return bail(\"offscreen\")\n    except Exception:\n        pass\n\n    # 라벨은 반드시 주입 **전에** 읽는다. 메뉴 항목/다이얼로그 버튼은 클릭\n    # 즉시 파괴돼 그 뒤의 프로퍼티 읽기가 실패하고, 로그가 '?'로 남아 추적이\n    # 불가능해진다(2026-07-24 FileZilla 실측: 메뉴 항목/예(Y)/취소 전부 '?',\n    # 살아남는 콤보 화살표만 '닫기'로 정상 출력).\n    try:\n        label = el.CurrentName or el.CurrentAutomationId or \"?\"\n    except Exception:\n        label = \"?\"\n\n    pt = clickable_point(el)\n    if not pt:\n        return bail(\"no-clickable-point\")\n    x, y = pt\n    vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)\n    vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)\n    vw = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)\n    vh = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)\n    if vw <= 1 or vh <= 1 or not (vx <= x < vx + vw and vy <= y < vy + vh):\n        return bail(\"point-outside-virtual-screen (%d,%d)\" % (x, y))\n\n    # 그 지점이 정말 대상 요소의 것인지 두 겹으로 확인한다. 2026-07-15에\n    # osScopedInvoke가 완전히 남남인 사용자 창(탐색기/VS Code)을 클릭하고\n    # \"성공\"으로 보고한 사고, 2026-07-13에 클릭 지점이 요소 rect 밖이라\n    # 물리적으로 no-op이던 이벤트가 재생 때는 요소 중심을 클릭해 엉뚱한 패널을\n    # 연 트랩 — 둘 다 이 검증으로 구조적으로 막힌다.\n    winpt = wintypes.POINT(int(x), int(y))\n    try:\n        hwnd_at = user32.WindowFromPoint(winpt)\n        pid_at = wintypes.DWORD()\n        user32.GetWindowThreadProcessId(hwnd_at, ctypes.byref(pid_at))\n        if pid_at.value != el.CurrentProcessId:\n            return bail(\"covered-by-other-window (pid %d at point, target pid %d)\"\n                        % (pid_at.value, el.CurrentProcessId))\n    except Exception as e:\n        return bail(\"window-hit-test-failed (%s)\" % e)\n\n    try:\n        at_point = uia.ElementFromPoint(winpt)\n    except Exception as e:\n        return bail(\"element-from-point-failed (%s)\" % e)\n    if not at_point or not _same_or_descendant(uia, el, at_point):\n        return bail(\"point-resolves-elsewhere\")\n\n    nx = int(round((x - vx) * 65535.0 / (vw - 1)))\n    ny = int(round((y - vy) * 65535.0 / (vh - 1)))\n\n    def send(flags):\n        inp = INPUT(type=INPUT_MOUSE)\n        inp.mi = MOUSEINPUT(nx, ny, 0, flags, 0, 0)\n        return user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))\n\n    # 사람이 눈으로 따라갈 수 있도록 이동/누름/뗌 사이에 간격을 둔다(§6).\n    if not send(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK):\n        return bail(\"SendInput(move) rejected\")\n    time.sleep(0.04)\n    if not send(MOUSEEVENTF_LEFTDOWN | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK):\n        return bail(\"SendInput(down) rejected\")\n    time.sleep(0.04)\n    send(MOUSEEVENTF_LEFTUP | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK)\n\n    print(\"[COM-SendInput] \" + tag + \" clicked '\" + label + \"' at (%d,%d)\" % (x, y))\n    time.sleep(0.05)\n    return True\n\n\n# ── TogglePattern (2026-07-31) ─────────────────────────────────────────────\n# 실측(TeamViewer 15.79 WebView2, poc/diag_teamviewer_a11y_wakeup.py [D]):\n# 체크박스 2종의 지원 패턴은 정확히 {Toggle, Legacy}뿐 — Invoke도\n# SelectionItem도 없다. 기존 클릭 체인(Invoke → SelectionItem → Legacy)에는\n# Toggle이 아예 없어서 앞의 둘이 연달아 예외로 떨어진 뒤\n# Legacy.Select(TAKESELECTION)이 예외 없이 통과하며 True를 반환했다. 그런데\n# 체크박스에서 \"셀렉션\"은 체크 상태를 바꾸지 않으므로, 재생은 성공으로\n# 보고되는데 화면에서는 아무 일도 일어나지 않는다 — 그 체크박스가 띄우는\n# 확인 다이얼로그도 당연히 안 뜬다(2026-07-31 클라이언트 피드백 증상 그대로).\n# Legacy 폴백보다 앞서 Toggle을 시도하되, ToggleState가 실제로 바뀐 경우에만\n# 성공으로 인정한다(거짓 PASS 방지 — 2026-07-13 3차 교훈과 같은 원칙).\nUIA_TogglePatternId = 10015\n\n\ndef toggle_item(mod, el, tag):\n    try:\n        tp = el.GetCurrentPattern(UIA_TogglePatternId).QueryInterface(\n            mod.IUIAutomationTogglePattern)\n    except Exception:\n        return False\n    try:\n        before = tp.CurrentToggleState\n        tp.Toggle()\n        time.sleep(0.05)\n        after = tp.CurrentToggleState\n    except Exception as e:\n        print(\"[\" + tag + \"] TogglePattern.Toggle() raised: %s\" % e, file=sys.stderr)\n        return False\n    if after != before:\n        print(\"[\" + tag + \"] TogglePattern.Toggle() %d -> %d\" % (before, after))\n        return True\n    print(\"[\" + tag + \"] TogglePattern.Toggle() left ToggleState unchanged (%d)\"\n          \" - falling through to the remaining patterns\" % before, file=sys.stderr)\n    return False\n\n\ndef top_windows():\n    found = []\n\n    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)\n    def cb(hwnd, _):\n        if user32.IsWindowVisible(hwnd):\n            found.append(hwnd)\n        return True\n\n    user32.EnumWindows(cb, 0)\n    return found\n\n\ndef resolve_cond(uia, sel):\n    conds = []\n    if sel.get(\"automationId\"):\n        conds.append(uia.CreatePropertyCondition(UIA_AutomationIdProperty, sel[\"automationId\"]))\n    if sel.get(\"name\"):\n        conds.append(uia.CreatePropertyCondition(UIA_NameProperty, sel[\"name\"]))\n    if sel.get(\"className\"):\n        conds.append(uia.CreatePropertyCondition(UIA_ClassNameProperty, sel[\"className\"]))\n    if not conds:\n        return None\n    cond = conds[0]\n    for c in conds[1:]:\n        cond = uia.CreateAndCondition(cond, c)\n    return cond\n\n\n# 2026-07-24: osExpandCollapse.py와 동일한 근거로 이식 — 이 파일의 COM 경로\n# (owned 다이얼로그/새 팝업창의 좁은 예외)에는 WAD의 암묵적 오프스크린\n# 스크롤-인-뷰가 없다. Invoke/Select/LegacyIAccessible 시도 전에 조상\n# ScrollPattern으로 한 번 끌어와 본다 — 좌표는 쓰지 않는다(§3).\ndef find_scrollable_ancestor(uia, el, max_up=8):\n    try:\n        walker = uia.RawViewWalker\n    except Exception:\n        return None\n    cur = el\n    for _ in range(max_up):\n        try:\n            cur.GetCurrentPattern(UIA_ScrollPatternId)\n            return cur\n        except Exception:\n            pass\n        try:\n            cur = walker.GetParentElement(cur)\n            if not cur:\n                return None\n        except Exception:\n            return None\n    return None\n\n\ndef ensure_visible(uia, mod, el):\n    try:\n        if not el.CurrentIsOffscreen:\n            return\n    except Exception:\n        return\n    print(\"[osScopedInvoke] target is offscreen — attempting scroll-into-view via ancestor ScrollPattern\", file=sys.stderr)\n    ancestor = find_scrollable_ancestor(uia, el)\n    if not ancestor:\n        return\n    try:\n        sp = ancestor.GetCurrentPattern(UIA_ScrollPatternId).QueryInterface(mod.IUIAutomationScrollPattern)\n        sp.SetScrollPercent(50.0, 50.0)\n        time.sleep(0.2)\n    except Exception:\n        pass\n\n\ndef invoke_item(uia, mod, el):\n    ensure_visible(uia, mod, el)\n    focus_ok = False\n    try:\n        el.SetFocus()\n        focus_ok = True\n    except Exception:\n        pass\n    # 시각적 재생 우선(2026-07-24, §6) — 성공하면 반드시 여기서 반환한다.\n    # 이어서 Invoke()까지 부르면 같은 동작이 두 번 실행된다.\n    if send_input_click(uia, el, \"osScopedInvoke\"):\n        return True\n    try:\n        el.GetCurrentPattern(UIA_InvokePatternId).QueryInterface(mod.IUIAutomationInvokePattern).Invoke()\n        return True\n    except Exception:\n        pass\n    if toggle_item(mod, el, \"osScopedInvoke\"):\n        return True\n    try:\n        el.GetCurrentPattern(UIA_SelectionItemPatternId).QueryInterface(mod.IUIAutomationSelectionItemPattern).Select()\n        return True\n    except Exception:\n        pass\n    try:\n        legacy = el.GetCurrentPattern(UIA_LegacyIAccessiblePatternId).QueryInterface(mod.IUIAutomationLegacyIAccessiblePattern)\n        try:\n            legacy.Select(UIA_SELECTIONFLAG_TAKESELECTION)\n            return True\n        except Exception:\n            pass\n        legacy.DoDefaultAction()\n        return True\n    except Exception:\n        pass\n    try:\n        ctrl_type = el.CurrentControlType\n    except Exception:\n        ctrl_type = None\n    if focus_ok and ctrl_type in PASSIVE_CONTROL_TYPES:\n        return True\n    print(f\"[osScopedInvoke] found element (controlType={ctrl_type}) but no actionable pattern (Invoke/Select/LegacyIAccessible) succeeded\", file=sys.stderr)\n    return False\n\n\n# 2026-07-24 실측(PuTTY): 콤보박스 드롭다운 화살표의 automationId는 창 안의\n# 모든 콤보가 \"DropDown\"으로 공유한다 — 상태 의존 Name(\"닫기\")은 2026-07-14\n# 가드가 이미 떼어내므로 AND 조건으로 좁힐 수도 없다. FindFirst는 트리의 첫\n# 번째(= Translation 패널의 \"Remote character set\") 화살표만 계속 잡았고,\n# Proxy 패널 항목을 고르는 스텝 두 개가 \"같은 콤보를 반복해서 여는\" 증상으로\n# 실패했다(재생 로그가 매번 동일한 좌표 (1223,412)를 찍은 것이 증거).\n# PuTTY류 다이얼로그는 비활성 패널의 컨트롤을 화면에서 내리므로, 후보가\n# 여럿이면 offscreen이 아닌 것을 고른다. 여러 후보를 차례로 클릭해보는 방식은\n# 쓰지 않는다 — 실패한 후보가 드롭다운을 열어둔 채로 남아 다음 후보 클릭이\n# 그 팝업 해제에 먹히기 때문.\ndef elements_from(arr):\n    cands = []\n    if not arr:\n        return cands\n    for i in range(arr.Length):\n        try:\n            cands.append(arr.GetElement(i))\n        except Exception:\n            pass\n    return cands\n\n\n# 2026-07-29 (HeidiSQL: 두 콤보박스의 드롭다운 화살표가 automationId=\"DropDown\"을\n# 공유): pick_trigger의 기존 \"offscreen 제외 후 첫 번째\" 방식은 PuTTY 전용\n# 가정(비활성 패널 컨트롤이 화면 밖으로 밀려남)에 의존한다 — HeidiSQL은 두\n# 콤보가 동시에 진짜로 화면에 떠 있어 이 필터가 아무 것도 못 거른다. 캡처된\n# window-relative Y(relY)를 현재 창의 실제 위치에 매 실행마다 다시 더해\n# 절대 Y로 변환하고, 후보들 중 그 위치에 가장 가까운 rect.top을 가진 걸\n# 고른다 — 저장된 화면 좌표를 클릭에 쓰는 게 아니라(§3 금지 대상과 다름),\n# 이미 구조적으로 찾아낸 후보들 사이에서 어느 걸 고를지 정하는 신호로만\n# 쓴다(§3 2026-07-24 재정의: 매 실행 재계산·즉시 소비·저장 안 함 — 이미\n# 승인된 dynamic ClickablePoint 예외와 같은 성격).\ndef pick_by_position(cands, win_top, rel_y):\n    if not cands:\n        return None\n    if len(cands) == 1 or win_top is None or rel_y is None:\n        return cands[0]\n    target_y = win_top + rel_y\n    best, best_dist = None, None\n    for c in cands:\n        try:\n            r = c.CurrentBoundingRectangle\n            d = abs(r.top - target_y)\n        except Exception:\n            continue\n        if best_dist is None or d < best_dist:\n            best, best_dist = c, d\n    return best if best is not None else cands[0]\n\n\ndef pick_trigger(uia, root, cond, win_top=None, rel_y=None):\n    try:\n        arr = root.FindAll(TreeScope_Subtree, cond)\n    except Exception:\n        arr = None\n    cands = elements_from(arr)\n    if not cands:\n        return None\n    if len(cands) == 1:\n        return cands[0]\n    visible = []\n    for c in cands:\n        try:\n            if not c.CurrentIsOffscreen:\n                visible.append(c)\n        except Exception:\n            visible.append(c)\n    pool = visible or cands\n    if win_top is not None and rel_y is not None:\n        picked = pick_by_position(pool, win_top, rel_y)\n        if picked is not None:\n            print(f\"[osScopedInvoke] trigger selector matched {len(cands)} elements \"\n                  f\"({len(visible)} on screen) — disambiguated by recorded position\")\n            return picked\n    print(f\"[osScopedInvoke] WARN trigger selector matched {len(cands)} elements \"\n          f\"({len(visible)} on screen) — the automationId is reused across \"\n          f\"controls; picking the first on-screen one\")\n    return pool[0]\n\n\n# 2026-07-17: owned 다이얼로그(WAD가 scoped session을 거부하는 창)에 타이핑하기\n# 위한 COM 경로. 기존에는 getWindowSession()이 owned 창을 만나면 WinAppDriver\n# Root 세션 REST로 전체 데스크톱 XPath 검색을 폴백으로 썼는데, 실측(2026-07-17\n# FileZilla 다이얼로그 진단): 이 Root-세션 REST 호출은 쿼리 내용/매치 여부와\n# 무관하게 매번 15~20초가 걸린다(빈 결과조차 15.6초) — WinAppDriver 3.5.2의\n# Root 세션 자체가 모든 element 조회에 고정 비용을 갖는 것으로 보임. hwnd는\n# 이미 EnumWindows로 알고 있으므로, 같은 COM 스택(osScopedInvoke의 클릭 경로와\n# 동일)으로 즉시 타이핑하면 이 15~20초를 완전히 우회한다.\n# 2026-07-24 실측(poc/diag_filezilla_rename.py, FileZilla Site Manager):\n# \"새 사이트(N)\" 직후 뜨는 인라인 이름변경 상자는\n#   - 캡처 시점에는 automationId=\"1\"로 기록되지만\n#   - 재생 시점의 라이브 요소는 automationId=\"\" (name도 \"\")\n# 즉 그 id는 런타임에 변하는 값이라 셀렉터로 절대 매칭되지 않는다(ListItem\n# 슬롯 인덱스와 같은 부류). 대신 이 상자는 **나타나는 순간 항상 키보드 포커스를\n# 가진다**(인라인 rename의 정의상 그렇다) — 좌표를 쓰지 않고 이 상자를 집는\n# 가장 신뢰할 수 있는 방법이다. 타이핑 대상을 못 찾았을 때만, 그리고 포커스\n# 요소가 우리 프로세스의 입력 컨트롤일 때만 쓴다(남의 창에 타이핑 방지).\nINPUT_CONTROL_TYPES = {50004, 50030, 50003}  # Edit, Document, ComboBox\n\n\ndef focused_input(uia, main_pid):\n    try:\n        el = uia.GetFocusedElement()\n    except Exception:\n        return None\n    if not el:\n        return None\n    try:\n        if el.CurrentProcessId != main_pid:\n            return None\n        if el.CurrentControlType not in INPUT_CONTROL_TYPES:\n            return None\n        aid = el.CurrentAutomationId\n        cls = el.CurrentClassName\n    except Exception:\n        return None\n    print(f\"[osScopedInvoke] target not found — falling back to the focused \"\n          f\"input control (id='{aid}' class='{cls}'); an inline rename box \"\n          f\"exposes a runtime-varying AutomationId, so focus is the stable handle\")\n    return el\n\n\ndef type_item(uia, mod, el, text):\n    ensure_visible(uia, mod, el)\n    try:\n        el.SetFocus()\n    except Exception:\n        pass\n    # 2026-07-17 (2차) 실측: 인라인 이름변경 편집 상자에 입력된 값은 물리\n    # 캡처에서 트레일링 개행(\"d\\n\")을 포함한다(사용자가 Enter로 확정) — 이걸\n    # 그대로 SetValue에 넣으면 개행이 리터럴 문자로 들어갈 뿐 Enter 키로\n    # 동작하지 않아 편집 상자가 미확정 상태로 남고, 그 상태에서 같은\n    # 다이얼로그의 다른 서브트리(탭 패널 등)가 UIA 검색에서 안 잡히는 게\n    # 실측으로 확인됨(FileZilla Site Manager). 트레일링 개행은 스트립 —\n    # 실제 Enter 키 주입은 이 상태에서 어디까지 필요한지 GUI 재검증 후 별도로.\n    value = text[:-1] if text.endswith('\\n') else text\n    try:\n        el.GetCurrentPattern(UIA_ValuePatternId).QueryInterface(mod.IUIAutomationValuePattern).SetValue(value)\n        return True\n    except Exception:\n        return False\n\n\ndef main():\n    ap = argparse.ArgumentParser()\n    ap.add_argument(\"--hwnd\", type=int, required=True)\n    ap.add_argument(\"--sel-b64\", required=True)\n    ap.add_argument(\"--trigger-sel-b64\", default=None)\n    ap.add_argument(\"--text-b64\", default=None)\n    # 2026-07-29 (HeidiSQL DropDown 재사용): 구조적으로 여러 후보가 걸릴 때\n    # 어느 걸 고를지에만 쓰는 위치 힌트 — 클릭 좌표 자체가 아니다(§3 참고,\n    # pick_by_position 주석). rel-y/trigger-rel-y는 캡처 시점의 창-상대 Y이고,\n    # 매 실행 현재 창 위치에 다시 더해서만 쓴다.\n    ap.add_argument(\"--rel-y\", type=int, default=None)\n    ap.add_argument(\"--trigger-rel-y\", type=int, default=None)\n    args = ap.parse_args()\n\n    enable_per_monitor_dpi()\n    comtypes.CoInitialize()\n    mod = comtypes.client.GetModule(\"UIAutomationCore.dll\")\n    uia = comtypes.client.CreateObject(\n        \"{ff48dba4-60ef-4201-aa87-54103eef594e}\", interface=mod.IUIAutomation\n    )\n\n    main_h = args.hwnd\n    if not main_h:\n        print(\"osScopedInvoke: --hwnd is required\", file=sys.stderr)\n        sys.exit(2)\n    # 2026-07-24: osExpandCollapse.py와 동일한 방어 — 방금 그 hwnd에\n    # WinAppDriver scoped session이 막 생성된 직후 이 프로세스의 별도\n    # IUIAutomation COM 클라이언트가 ElementFromHandle()을 호출하면 간헐적으로\n    # COMError(-2147220991, 이벤트 구독자 관련)가 실측됨 — 셀렉터/로직 문제가\n    # 아니라 타이밍 레이스이므로 실패 단정 전에 짧게 재시도한다.\n    root = None\n    for attempt in range(4):\n        if attempt > 0:\n            time.sleep(0.3)\n        try:\n            root = uia.ElementFromHandle(main_h)\n        except Exception:\n            root = None\n        if root:\n            break\n    if not root:\n        print(\"osScopedInvoke: ElementFromHandle failed\", file=sys.stderr)\n        sys.exit(2)\n\n    win_top = None\n    try:\n        r = wintypes.RECT()\n        if user32.GetWindowRect(main_h, ctypes.byref(r)):\n            win_top = r.top\n    except Exception:\n        pass\n\n    sel = json.loads(base64.b64decode(args.sel_b64).decode(\"utf-8\"))\n    item_cond = resolve_cond(uia, sel)\n    if item_cond is None:\n        print(\"osScopedInvoke: selector has no usable fields\", file=sys.stderr)\n        sys.exit(2)\n\n    # 트리거(버튼 등)가 있으면 이 실행 안에서 먼저 클릭 — 별도 프로세스로\n    # 쪼개 두 번 호출하지 않아 트리거-검색 사이의 지연(및 그로 인한 드롭다운\n    # 자동-닫힘)을 없앤다.\n    if args.trigger_sel_b64:\n        trigger_sel = json.loads(base64.b64decode(args.trigger_sel_b64).decode(\"utf-8\"))\n        trigger_cond = resolve_cond(uia, trigger_sel)\n        if trigger_cond is not None:\n            trigger = pick_trigger(uia, root, trigger_cond, win_top, args.trigger_rel_y)\n            if trigger:\n                invoke_item(uia, mod, trigger)\n            else:\n                # 트리거를 못 찾으면 드롭다운이 아예 안 열려 이후 아이템\n                # 검색이 원인불명으로 실패하는 것처럼 보인다 — 눈에 보이게\n                # 남긴다 (2026-07-14, 침묵 스킵이 진단을 어렵게 만든 것을 확인).\n                print(f\"[osScopedInvoke] WARN trigger not found (sel={args.trigger_sel_b64}) — dropdown likely never opened\")\n\n    # --text-b64가 있으면 클릭/Invoke 대신 타이핑(ValuePattern.SetValue) —\n    # osScopedType() JS wrapper 전용 (2026-07-17, owned 다이얼로그 안 Edit\n    # 컨트롤에 타이핑하기 위해 도입 — Root 세션 REST 폴백의 15~20초 고정\n    # 비용을 피한다. 검색 로직((a)(b) 둘 다)은 클릭과 완전히 동일).\n    act = (lambda el: type_item(uia, mod, el, base64.b64decode(args.text_b64).decode(\"utf-8\")))         if args.text_b64 else (lambda el: invoke_item(uia, mod, el))\n    verb = 'typed into' if args.text_b64 else 'invoked'\n\n    # 최대 4회 시도(즉시 1회 + 300ms 간격 재시도 3회, 총 최대 ~0.9초) — 2026-07-17\n    # 실측: \"새 사이트(N)\" 클릭 직후 뜨는 인라인 이름변경 상자(automationId=\"1\")를\n    # 즉시 1회만 찾으면 렌더링 레이스로 못 찾는 경우가 실제 GUI에서 재현됨\n    # (FileZilla Site Manager). 기존 REST 경로(_findScoped)는 1초 간격으로 최대\n    # 8초 폴링해 이런 레이스를 자연히 흡수했는데, COM 경로는 단발 시도라 그\n    # 여유가 없었다 — _step()의 범용 Fail-and-Recover(ESC)에 기대면 이름변경\n    # 상자에서 ESC가 변경 자체를 취소시켜 재시도도 함께 실패하므로(esc-recovery\n    # 후 osScopedType 재실패로 실측 확인), 스크립트 자체에 짧은 재시도를 둔다.\n    # 2026-07-24 실측: FileZilla 이름변경 상자는 \"새 사이트(N)\" 클릭 후\n    # **2260ms**만에 나타났다(poc/diag_filezilla_rename.py) — 기존 예산\n    # 4회(~0.9초)로는 구조적으로 못 잡는다. 타이핑은 20회(~6초)까지 기다리고\n    # (REST 경로 _findScoped의 8초 폴링보다는 짧게), 클릭은 10회(~2.7초)로\n    # 둔다. _step()의 ESC 복구는 이 대상에서 오히려 해로워 이제 건너뛰므로\n    # (_escWouldHarm) 재시도 여유는 이 스크립트 안에서 확보해야 한다.\n    attempts = 20 if args.text_b64 else 10\n    main_pid = wintypes.DWORD()\n    user32.GetWindowThreadProcessId(main_h, ctypes.byref(main_pid))\n    for attempt in range(attempts):\n        if attempt > 0:\n            time.sleep(0.3)\n\n        # (a) 메인 창 서브트리. Subtree = 창 자기 자신(root)도 포함해 검색한다 —\n        #     Descendants만 쓰면 캡처된 타겟이 창 자체(예: className=\"#32770\")인\n        #     경우 구조적으로 못 찾는다(2026-07-16 FileZilla 다이얼로그 클릭 확인).\n        # rel_y 힌트가 있으면 FindAll로 모든 후보를 모아 위치로 가려낸다\n        # (2026-07-29, HeidiSQL: 같은 창 안에 automationId=\"DropDown\"을\n        # 공유하는 콤보가 둘 이상 — FindFirst는 항상 같은 하나만 반환).\n        # 힌트가 없으면 기존과 동일하게 FindFirst 한 건만 본다.\n        item = None\n        try:\n            if args.rel_y is not None:\n                cands = elements_from(root.FindAll(TreeScope_Subtree, item_cond))\n                if len(cands) > 1:\n                    picked = pick_by_position(cands, win_top, args.rel_y)\n                    print(f\"[osScopedInvoke] selector matched {len(cands)} elements in main \"\n                          f\"window — disambiguated by recorded position\")\n                    item = picked\n                elif cands:\n                    item = cands[0]\n            else:\n                item = root.FindFirst(TreeScope_Subtree, item_cond)\n        except Exception:\n            item = None\n        if item and act(item):\n            print(f\"[osScopedInvoke] {verb} under main window subtree\")\n            sys.exit(0)\n\n        # (b) 메인 창과 같은 프로세스(PID)가 소유한 다른 최상위 창 서브트리 —\n        #     이미 열려 있는 팝업/드롭다운(예: PuTTY의 ComboLBox, FileZilla\n        #     메뉴)을 잡는다. 새로 뜬 창인지 여부는 따지지 않는다(트리거가\n        #     이미 직전 스텝에서 실행됐으므로 baseline diff 불필요). PID로\n        #     반드시 한정한다 — PID 무관하게 데스크톱 전체를 뒤지면 완전히\n        #     남남인 창을 잘못 클릭할 수 있음을 실측으로 확인(2026-07-15:\n        #     7-Zip에서 \"hansung\"/\"project\" 등 사용자의 실제 폴더명을 검색하다가\n        #     (a)에서 못 찾자 사용자가 실제로 열어둔 탐색기 창(explorer.exe,\n        #     class=CabinetWClass)과 VS Code 창(Code.exe)에서 우연히 같은\n        #     이름을 찾아 그 창을 대신 클릭 — 거짓 성공으로 로그에 \"invoked\"\n        #     찍힘 + 사용자 창에 실제 부작용).\n        for h in top_windows():\n            if h == main_h:\n                continue\n            cand_pid = wintypes.DWORD()\n            user32.GetWindowThreadProcessId(h, ctypes.byref(cand_pid))\n            if cand_pid.value != main_pid.value:\n                continue\n            try:\n                other_root = uia.ElementFromHandle(h)\n                if not other_root:\n                    continue\n                item = other_root.FindFirst(TreeScope_Subtree, item_cond)\n                if item and act(item):\n                    print(f\"[osScopedInvoke] {verb} under other top-level window hwnd={h}\")\n                    sys.exit(0)\n            except Exception:\n                continue\n\n    # 마지막 수단(타이핑 전용): 포커스를 가진 입력 컨트롤. 인라인 이름변경\n    # 상자처럼 automationId가 런타임에 변하는 대상은 셀렉터로는 영원히 못\n    # 찾지만 포커스는 항상 갖고 있다. 클릭에는 적용하지 않는다 — \"포커스된\n    # 무언가를 대신 클릭\"은 엉뚱한 동작을 조용히 수행할 위험이 크다.\n    if args.text_b64:\n        el = focused_input(uia, main_pid.value)\n        if el and act(el):\n            print(f\"[osScopedInvoke] {verb} the focused input control\")\n            sys.exit(0)\n\n    print(f\"osScopedInvoke: target not found under main window or any other top-level window (sel={args.sel_b64})\", file=sys.stderr)\n    sys.exit(2)\n\n\nif __name__ == \"__main__\":\n    main()\n",
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
        console.warn('[osExpandCollapse] failed:', String((e.stderr && e.stderr.toString()) || e.message || e).slice(-1500));
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
function osScopedInvoke(hwnd, target, triggerTarget, relY, triggerRelY) {
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
        const out = execSync(
            `python "${_helperFile('osScopedInvoke.py')}" --hwnd ${hwnd} --sel-b64 "${selB64}" ${triggerArg} ${relYArg} ${triggerRelYArg}`,
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
function osActivate(titleLike) {
    try {
        const args = _appHwnd ? `-hwnd ${_appHwnd}` : `-titleLike "${titleLike}"`;
        execSync(
            `powershell -NoProfile -File "${_helperFile('osActivate.ps1')}" ${args}`,
            { stdio: 'pipe', timeout: 15000 }
        );
    } catch (e) {
        console.warn('[osActivate] failed:', String(e.message || e).substring(0, 100));
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

class HeidiSQLPageByClass {
    async click1() {
        await _clickBySid(_appSid, null, '//SplitButton[@ClassName="TButton" and @Name="신규"]');
    }

    async click2() {
        await _clickBySid(_appSid, null, '//TabItem[@Name="SSH 터널"]');
    }

    async click3() {
        await _clickBySid(_appSid, null, '//TabItem[@Name="설정"]');
    }

    async click4() {
        osScopedInvoke(_appHwnd, {"automationId":"DropDown","className":"","name":""}, null, 85);
    }

    async click5() {
        osScopedInvoke(_appHwnd, {"automationId":"DropDown","className":"","name":""}, null, 85);
    }

    async click6() {
        await _clickBySid(_appSid, null, '//CheckBox[@ClassName="TCheckBox" and @Name="자격 증명 프롬프트"]');
    }

    async click7() {
        osScopedInvoke(_appHwnd, {"automationId":"","className":"TComboBoxEx","name":""}, null, 84);
    }

    async scroll8() {
        osScrollEl(_appHwnd, {"automationId":"2559752","className":"TCheckBox","name":"Windows 인증 사용"}, 11);
    }

    async click9() {
        osExpandCollapse(_appHwnd, {"automationId":"","className":"TComboBox","name":""}, "취소", null, null);
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
    _appSid = await _createSession("C:\\\\Program Files\\\\HeidiSQL\\\\heidisql.exe");
    console.log(`[session] app session ${_appSid} ready`);
    await initAppHwnd();
    normalizeWindowSimple({"left":2991,"top":260,"width":700,"height":490});

        const page = new HeidiSQLPageByClass();
            osActivate("HeidiSQL 12.20.0.7320 - 세션 관리자");
            await _step('1:click 신규', () => page.click1());
            await _step('2:click SSH 터널', () => page.click2());
            await _step('3:click 설정', () => page.click3());
            await _step('4:click 열기', () => page.click4());
            await _step('5:click 닫기', () => page.click5());
            await _step('6:click 자격 증명 프롬프트', () => page.click6());
            await _step('7:click MySQL on RDS', () => page.click7());
            await _step('8:scroll delta=11', () => page.scroll8());
            await _step('9:expandCollapse  -> 취소', () => page.click9());
    } finally {

        if (_appSid) { try { await _appiumFetch(`/session/${_appSid}`, { method: 'DELETE' }, 5000); } catch {} }
        _killSpawnedAppium();
    }
    if (_warnings.length) console.warn('[replay-warnings]', _warnings);
    if (_failures.length) { console.error('[FAIL]', _failures); process.exitCode = 1; }
    else console.log('[PASS] all steps completed');
}

run().catch(e => { console.error('[FATAL]', e); process.exitCode = 1; });
