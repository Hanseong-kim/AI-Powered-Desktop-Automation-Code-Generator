param([string]$titleLike, [string]$hwnd)
Add-Type @"
using System;
using System.Text;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public class WinActivate {
  public delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc p, IntPtr l);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern int GetWindowTextLength(IntPtr h);
  [DllImport("user32.dll", CharSet=CharSet.Auto)] public static extern int GetWindowText(IntPtr h, StringBuilder sb, int m);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, IntPtr pid);
  [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint a, uint b, bool f);
  [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
  [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint flags);
  public static List<IntPtr> Find(string t) {
    var f = new List<IntPtr>();
    EnumWindows((h,l) => { if(!IsWindowVisible(h)) return true; int n=GetWindowTextLength(h); if(n==0) return true;
      var sb=new StringBuilder(n+1); GetWindowText(h,sb,sb.Capacity); if(sb.ToString().Contains(t)) f.Add(h); return true; }, IntPtr.Zero);
    return f;
  }
  public static void Force(IntPtr h) {
    // SW_RESTORE only to UN-MINIMIZE. 2026-09-01 (Medflow 실측): this used to
    // be unconditional, and SW_RESTORE on a MAXIMIZED window un-maximizes it —
    // so every window activation silently un-maximized the app. Visible at the
    // step5->step6 switch: the recording never pressed the restore button, the
    // replay did. _ensureDialog() calls normalizeWindow() then osActivate(),
    // and this is the call that actually moved the window state (the
    // osMoveWindow.ps1 helper next to it is a no-op — see its $hwnd/$hWnd
    // note). A window that is not minimized needs no show-state change at all;
    // BringWindowToTop/SetForegroundWindow below raise it either way.
    if (IsIconic(h)) ShowWindow(h, 9); // SW_RESTORE
    uint fg = GetWindowThreadProcessId(GetForegroundWindow(), IntPtr.Zero);
    uint me = GetCurrentThreadId();
    if (fg != me) AttachThreadInput(me, fg, true);
    BringWindowToTop(h); SetForegroundWindow(h);
    SetWindowPos(h, (IntPtr)(-1), 0,0,0,0, 0x0003); // TOPMOST, NOMOVE|NOSIZE
    SetWindowPos(h, (IntPtr)(-2), 0,0,0,0, 0x0003); // NOTOPMOST
    if (fg != me) AttachThreadInput(me, fg, false);
  }
}
"@ -ErrorAction SilentlyContinue
if ($hwnd) {
  [WinActivate]::Force([IntPtr]([int64]$hwnd)); Start-Sleep -Milliseconds 250
} else {
  $m = [WinActivate]::Find($titleLike)
  if ($m.Count -gt 0) { [WinActivate]::Force($m[0]); Start-Sleep -Milliseconds 250 }
}
