param([string]$titleLike, [string]$hwnd, [int]$left, [int]$top, [int]$width, [int]$height)
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class DpiAware {
  [DllImport("user32.dll")] public static extern bool SetProcessDpiAwarenessContext(IntPtr ctx);
  [DllImport("shcore.dll")] public static extern int SetProcessDpiAwareness(int v);
}
"@ -ErrorAction SilentlyContinue
try {
  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 (Win10 1703+) — same value
  # agent.py passes.
  [DpiAware]::SetProcessDpiAwarenessContext([IntPtr](-4)) | Out-Null
} catch {
  try { [DpiAware]::SetProcessDpiAwareness(2) | Out-Null } catch {}
}
Add-Type @"
using System;
using System.Text;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public class WinMove {
  public delegate bool EnumProc(IntPtr hWnd, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc proc, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern int GetWindowTextLength(IntPtr hWnd);
  [DllImport("user32.dll", CharSet = CharSet.Auto)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder sb, int max);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
  [DllImport("user32.dll")] public static extern bool MoveWindow(IntPtr hWnd, int X, int Y, int nWidth, int nHeight, bool bRepaint);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT r);
  [DllImport("user32.dll")] public static extern bool IsZoomed(IntPtr hWnd);
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
}
"@ -ErrorAction SilentlyContinue
# -hwnd bypasses title matching — see OS_WINRECT_PS1 for why ambiguous title
# substrings (e.g. any two VS Code windows) are unsafe to move/resize by.
#
# 2026-09-02: the handle MUST NOT be stored in a variable named $hWnd. PowerShell
# variable names are case-INSENSITIVE, so $hWnd and the [string]-typed $hwnd
# parameter above are the SAME variable, and the param's type constraint follows
# it: assigning [IntPtr] converts straight back to String. Every later P/Invoke
# then threw "Cannot convert the "68436" value of type System.String to type
# System.IntPtr" — measured on a live window, both the -hwnd and the -titleLike
# path (the latter finds the right handle and dies on the same assignment), so
# this whole helper was a no-op from the day the $hwnd param was added.
# It was invisible three times over: PowerShell method exceptions are
# non-terminating (exit code stays 0), execSync runs with stdio:'pipe', and the
# callers' catch blocks — which push 'moveWindow'/'normalize' to _failures —
# therefore never ran. osActivate.ps1 avoids this by casting inline
# ([WinActivate]::Force([IntPtr]([int64]$hwnd))) with no intermediate variable.
if ($hwnd) {
  $targetHwnd = [IntPtr]([int64]$hwnd)
} else {
  $matches = [WinMove]::Find($titleLike)
  $targetHwnd = [IntPtr]::Zero
  if ($matches.Count -gt 0) {
    $fg = [WinMove]::GetForegroundWindow()
    $targetHwnd = $matches[0]
    foreach ($h in $matches) { if ($h -eq $fg) { $targetHwnd = $h; break } }
  }
}
if ($targetHwnd -ne [IntPtr]::Zero) {
  # Idempotency fast-path: if the window is already at the target geometry,
  # skip ShowWindow(RESTORE)+MoveWindow entirely — avoids a visible
  # restore-then-resize flicker when replay finds the window already in the
  # recorded position (e.g. the "already maximized" case reported 2026-07-07:
  # recorded flow assumes a maximize step is needed, but the window is
  # already there).
  #
  # 2026-09-01 (Medflow 실측): the condition used to also require
  # "-not IsZoomed($targetHwnd)", which excluded the very case the note above says
  # this fast-path exists for. A window recorded WHILE MAXIMIZED stores the
  # maximized rect (Medflow's main window: left/top -9, 1938x1038 — a
  # maximized window overhangs the screen by the invisible resize border), and
  # MedflowMain.hta opens with WINDOWSTATE="maximize", so replay found all four
  # edges already matching and still ran SW_RESTORE + MoveWindow. The user sees
  # the restore button being pressed: same pixels, but the window is no longer
  # maximized — a state change the recording never made.
  #
  # Geometry already equal means there is nothing to do, maximized or not. A
  # window recorded un-maximized whose live rect differs still falls through to
  # the restore+move path below, unchanged.
  $already = New-Object WinMove+RECT
  [WinMove]::GetWindowRect($targetHwnd, [ref]$already) | Out-Null
  $sameW = [math]::Abs(($already.Right - $already.Left) - $width) -le 2
  $sameH = [math]::Abs(($already.Bottom - $already.Top) - $height) -le 2
  $sameL = [math]::Abs($already.Left - $left) -le 2
  $sameT = [math]::Abs($already.Top - $top) -le 2
  if ($sameW -and $sameH -and $sameL -and $sameT) {
    exit
  }
  [WinMove]::ShowWindow($targetHwnd, 9) | Out-Null
  Start-Sleep -Milliseconds 300
  $candW = $width
  $candH = $height
  for ($i = 0; $i -lt 3; $i++) {
    [WinMove]::MoveWindow($targetHwnd, $left, $top, $candW, $candH, $true) | Out-Null
    Start-Sleep -Milliseconds 300
    $r = New-Object WinMove+RECT
    [WinMove]::GetWindowRect($targetHwnd, [ref]$r) | Out-Null
    $actualW = $r.Right - $r.Left
    $actualH = $r.Bottom - $r.Top
    if ([math]::Abs($actualW - $width) -le 2 -and [math]::Abs($actualH - $height) -le 2) { break }
    if ($actualW -le 0 -or $actualH -le 0) { break }
    $candW = [int]([math]::Round(($width * $candW) / [double]$actualW))
    $candH = [int]([math]::Round(($height * $candH) / [double]$actualH))
  }
}
