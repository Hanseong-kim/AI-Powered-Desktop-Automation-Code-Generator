param([string]$titleLike, [string]$hwnd)
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes -ErrorAction SilentlyContinue
Add-Type @"
using System;
using System.Text;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public class PopupWin {
  public delegate bool EnumProc(IntPtr hWnd, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc proc, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool IsWindow(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern int GetWindowTextLength(IntPtr hWnd);
  [DllImport("user32.dll", CharSet = CharSet.Auto)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder sb, int max);
  [DllImport("user32.dll", CharSet = CharSet.Auto)] public static extern int GetClassName(IntPtr hWnd, StringBuilder sb, int max);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
  [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);
  public static List<IntPtr> AllTop() {
    var f = new List<IntPtr>();
    EnumWindows((h, l) => { if (IsWindowVisible(h)) f.Add(h); return true; }, IntPtr.Zero);
    return f;
  }
  public static string ClassOf(IntPtr h) {
    var sb = new StringBuilder(256);
    GetClassName(h, sb, sb.Capacity);
    return sb.ToString();
  }
  public static string TitleOf(IntPtr h) {
    int len = GetWindowTextLength(h);
    if (len == 0) return "";
    var sb = new StringBuilder(len + 1);
    GetWindowText(h, sb, sb.Capacity);
    return sb.ToString();
  }
  public static uint PidOf(IntPtr h) {
    uint pid; GetWindowThreadProcessId(h, out pid); return pid;
  }
}
"@ -ErrorAction SilentlyContinue

$mainHwnd = [IntPtr]::Zero
if ($hwnd) { $mainHwnd = [IntPtr]([int64]$hwnd) }
elseif ($titleLike) {
  foreach ($h in [PopupWin]::AllTop()) {
    if ([PopupWin]::TitleOf($h).Contains($titleLike)) { $mainHwnd = $h; break }
  }
}
$targetPid = 0
if ($mainHwnd -ne [IntPtr]::Zero) { $targetPid = [PopupWin]::PidOf($mainHwnd) }

$candidates = New-Object System.Collections.Generic.List[IntPtr]
foreach ($h in [PopupWin]::AllTop()) {
  if ($h -eq $mainHwnd) { continue }
  $cls = [PopupWin]::ClassOf($h)
  $isDialogClass = ($cls -eq '#32770')
  $isOwnedByTarget = ($targetPid -ne 0 -and [PopupWin]::PidOf($h) -eq $targetPid)
  if ($isDialogClass -or $isOwnedByTarget) { $candidates.Add($h) }
}

# Conservative order: no-side-effect dismissal first (Cancel/No/Close), only
# reach for an affirmative (OK/Yes) if nothing safer matched — a wrong click
# here should never silently accept a destructive action.
$preferred = @('취소', '아니요', '닫기', 'Cancel', 'No', 'Close', '확인', 'OK', '예', 'Yes')

$dismissed = $false
foreach ($h in $candidates) {
  if ($dismissed) { break }
  try {
    $el = [System.Windows.Automation.AutomationElement]::FromHandle($h)
    if (-not $el) { continue }
    $cond = New-Object System.Windows.Automation.PropertyCondition(
      [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
      [System.Windows.Automation.ControlType]::Button)
    $buttons = $el.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cond)
    foreach ($btnName in $preferred) {
      if ($dismissed) { break }
      foreach ($b in $buttons) {
        if ($b.Current.Name -ne $btnName) { continue }
        $clicked = $false
        try {
          $inv = $b.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
          $inv.Invoke()
          $clicked = $true
        } catch {
          try {
            $legacy = $b.GetCurrentPattern([System.Windows.Automation.LegacyIAccessiblePattern]::Pattern)
            $legacy.DoDefaultAction()
            $clicked = $true
          } catch {
            # Owner-drawn / non-standard control — last resort: BM_CLICK via SendMessage.
            try {
              $bh = [IntPtr]$b.Current.NativeWindowHandle
              if ($bh -ne [IntPtr]::Zero) { [PopupWin]::SendMessage($bh, 0x00F5, [IntPtr]::Zero, [IntPtr]::Zero) | Out-Null; $clicked = $true }
            } catch {}
          }
        }
        if ($clicked) {
          $deadline = (Get-Date).AddSeconds(3)
          while ((Get-Date) -lt $deadline -and [PopupWin]::IsWindow($h)) { Start-Sleep -Milliseconds 100 }
          Write-Output "DISMISSED|$btnName|$([PopupWin]::TitleOf($h))"
          $dismissed = $true
        }
        break
      }
    }
  } catch {}
}
if (-not $dismissed) { Write-Output "NONE" }
