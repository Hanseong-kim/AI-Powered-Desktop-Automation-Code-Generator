param([string]$titleLike, [string]$hwnd, [switch]$listOnly, [switch]$ownerOnly, [string]$siblingOf)
Add-Type @"
using System;
using System.Text;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public class WinEnum {
  public delegate bool EnumProc(IntPtr hWnd, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc proc, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern int GetWindowTextLength(IntPtr hWnd);
  [DllImport("user32.dll", CharSet = CharSet.Auto)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder sb, int max);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT r);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern IntPtr GetWindow(IntPtr hWnd, uint cmd);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
  public static List<IntPtr> Find(string titleLike) {
    var found = new List<IntPtr>();
    EnumWindows((hWnd, lParam) => {
      if (!IsWindowVisible(hWnd) || IsIconic(hWnd)) return true;
      int len = GetWindowTextLength(hWnd);
      if (len == 0) return true;
      var sb = new StringBuilder(len + 1);
      GetWindowText(hWnd, sb, sb.Capacity);
      if (sb.ToString().Contains(titleLike)) found.Add(hWnd);
      return true;
    }, IntPtr.Zero);
    return found;
  }
  // Sibling top-level windows of the same process as hwndOf, excluding
  // hwndOf itself, restricted to ones with a real (non-zero) rect. Used to
  // recover from WinAppDriver/OS binding the "main" window to a hidden 0x0
  // helper window instead of the app's actual visible form (observed with
  // HeidiSQL's Delphi/VCL 'TApplication' window).
  public static List<IntPtr> FindSizedSiblings(IntPtr hwndOf) {
    var found = new List<IntPtr>();
    uint pid;
    GetWindowThreadProcessId(hwndOf, out pid);
    if (pid == 0) return found;
    EnumWindows((hWnd, lParam) => {
      if (hWnd == hwndOf || !IsWindowVisible(hWnd)) return true;
      uint wpid;
      GetWindowThreadProcessId(hWnd, out wpid);
      if (wpid != pid) return true;
      RECT r;
      if (GetWindowRect(hWnd, out r) && (r.Right - r.Left) > 0 && (r.Bottom - r.Top) > 0) {
        found.Add(hWnd);
      }
      return true;
    }, IntPtr.Zero);
    return found;
  }
}
"@ -ErrorAction SilentlyContinue
if ($siblingOf) {
  $sibs = [WinEnum]::FindSizedSiblings([IntPtr]([int64]$siblingOf))
  if ($sibs.Count -gt 0) { Write-Output ([int64]$sibs[0]) }
  exit
}
# -hwnd targets one specific window directly, bypassing title matching entirely.
# Title matching alone is ambiguous whenever more than one window shares a
# substring (e.g. every VS Code window's title ends in "Visual Studio Code") —
# callers that already know their window's handle MUST use -hwnd so replay
# never drifts onto an unrelated window (see launchApp's hwnd tracking).
if ($hwnd) {
  $h = [IntPtr]([int64]$hwnd)
  if ($ownerOnly) {
    # GW_OWNER=4 — nonzero means an owned (dialog-style) window, which
    # WinAppDriver's appTopLevelWindow rejects outright ("not a top level
    # window handle"), so callers skip the scoped-session attempt entirely.
    Write-Output ([int64][WinEnum]::GetWindow($h, 4))
    exit
  }
  $r = New-Object WinEnum+RECT
  if ([WinEnum]::GetWindowRect($h, [ref]$r)) {
    Write-Output ("{0} {1} {2} {3}" -f $r.Left, $r.Top, ($r.Right - $r.Left), ($r.Bottom - $r.Top))
  }
  exit
}
$matches = [WinEnum]::Find($titleLike)
if ($listOnly) {
  foreach ($h in $matches) { Write-Output ([int64]$h) }
  exit
}
if ($matches.Count -gt 0) {
  $fg = [WinEnum]::GetForegroundWindow()
  $hWnd = $matches[0]
  foreach ($h in $matches) { if ($h -eq $fg) { $hWnd = $h; break } }
  $r = New-Object WinEnum+RECT
  [WinEnum]::GetWindowRect($hWnd, [ref]$r) | Out-Null
  Write-Output ("{0} {1} {2} {3}" -f $r.Left, $r.Top, ($r.Right - $r.Left), ($r.Bottom - $r.Top))
}
