param([string]$titleLike, [string]$hwnd, [switch]$listOnly, [switch]$ownerOnly, [string]$siblingOf, [switch]$pidOf, [string]$siblingOfPid, [string]$pidByImage)
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
    return FindSizedSiblingsByPid(pid, hwndOf);
  }
  // 2026-08-14 (VS splash-window race): FindSizedSiblings derives the PID
  // from hwndOf via GetWindowThreadProcessId, which fails (returns 0) once
  // hwndOf has already been destroyed -- no good for recovering from a
  // window that died between being grabbed and being used. Callers that
  // captured the PID earlier, while the window was still alive, can search
  // directly by PID instead, sidestepping that dead-handle lookup entirely.
  public static List<IntPtr> FindSizedSiblingsByPid(uint pid, IntPtr exclude) {
    var found = new List<IntPtr>();
    if (pid == 0) return found;
    EnumWindows((hWnd, lParam) => {
      if (hWnd == exclude || !IsWindowVisible(hWnd)) return true;
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
# 2026-08-17 (VS splash race, real GUI re-run): -pidOf derives the PID from
# the CURRENT session hwnd via GetWindowThreadProcessId, which needs that
# hwnd to still be alive at call time. Measured live: it can already be gone
# by the very first call after session creation (pidOut came back empty,
# GetWindowThreadProcessId returned 0) — the splash can die faster than this
# script can even ask for its owner. Resolve by PROCESS IMAGE NAME instead,
# which needs nothing about any particular window's lifetime — the process
# itself is guaranteed alive (it just launched) — whatever window it
# currently owns still belongs to it.
if ($pidByImage) {
  $procName = [System.IO.Path]::GetFileNameWithoutExtension($pidByImage)
  $p = Get-Process -Name $procName -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($p) { Write-Output $p.Id }
  exit
}
if ($siblingOf) {
  $sibs = [WinEnum]::FindSizedSiblings([IntPtr]([int64]$siblingOf))
  if ($sibs.Count -gt 0) { Write-Output ([int64]$sibs[0]) }
  exit
}
# 2026-08-14: search by an already-captured PID instead of re-deriving it
# from a (possibly by-now-destroyed) hwnd -- see FindSizedSiblingsByPid.
if ($siblingOfPid) {
  $sibs = [WinEnum]::FindSizedSiblingsByPid([uint32]$siblingOfPid, [IntPtr]::Zero)
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
  if ($pidOf) {
    # 2026-08-14 (VS splash-window race): capture the PID while the window
    # is still known-alive, so a later liveness check that finds it gone can
    # still search for a replacement via -siblingOfPid (GetWindowThreadProcessId
    # fails on an already-destroyed handle, so this must happen up front).
    [uint32]$capturedPid = 0
    [WinEnum]::GetWindowThreadProcessId($h, [ref]$capturedPid) | Out-Null
    if ($capturedPid) { Write-Output $capturedPid }
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
  # $targetHwnd, NOT $hWnd: PowerShell variable names are case-insensitive, so
  # $hWnd would be the [string]-typed $hwnd parameter and the assignment would
  # convert the handle back to String — measured 2026-09-02, this exact bug
  # made THIS branch (the -titleLike rect lookup) throw on every call while the
  # -hwnd branch above, which uses the untyped local $h, kept working. Same
  # root cause as osMoveWindow.ps1; see its note and CLAUDE.md §5.
  $fg = [WinEnum]::GetForegroundWindow()
  $targetHwnd = $matches[0]
  foreach ($h in $matches) { if ($h -eq $fg) { $targetHwnd = $h; break } }
  $r = New-Object WinEnum+RECT
  [WinEnum]::GetWindowRect($targetHwnd, [ref]$r) | Out-Null
  Write-Output ("{0} {1} {2} {3}" -f $r.Left, $r.Top, ($r.Right - $r.Left), ($r.Bottom - $r.Top))
}
