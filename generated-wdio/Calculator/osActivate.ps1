param([string]$titleLike)
Add-Type @"
using System;
using System.Text;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public class WinActivate {
  public delegate bool EnumProc(IntPtr hWnd, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc proc, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern int GetWindowTextLength(IntPtr hWnd);
  [DllImport("user32.dll", CharSet = CharSet.Auto)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder sb, int max);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
  public static List<IntPtr> Find(string titleLike) {
    var found = new List<IntPtr>();
    EnumWindows((hWnd, lParam) => {
      if (!IsWindowVisible(hWnd)) return true;
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
$matches = [WinActivate]::Find($titleLike)
if ($matches.Count -gt 0) {
  $hWnd = $matches[0]
  [WinActivate]::ShowWindow($hWnd, 9) | Out-Null
  [WinActivate]::SetForegroundWindow($hWnd) | Out-Null
  Start-Sleep -Milliseconds 200
}
