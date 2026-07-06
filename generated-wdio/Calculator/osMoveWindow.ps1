param([string]$titleLike, [string]$hwnd, [int]$left, [int]$top, [int]$width, [int]$height)
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
if ($hwnd) {
  $hWnd = [IntPtr]([int64]$hwnd)
} else {
  $matches = [WinMove]::Find($titleLike)
  $hWnd = [IntPtr]::Zero
  if ($matches.Count -gt 0) {
    $fg = [WinMove]::GetForegroundWindow()
    $hWnd = $matches[0]
    foreach ($h in $matches) { if ($h -eq $fg) { $hWnd = $h; break } }
  }
}
if ($hWnd -ne [IntPtr]::Zero) {
  [WinMove]::ShowWindow($hWnd, 9) | Out-Null
  Start-Sleep -Milliseconds 300
  $candW = $width
  $candH = $height
  for ($i = 0; $i -lt 3; $i++) {
    [WinMove]::MoveWindow($hWnd, $left, $top, $candW, $candH, $true) | Out-Null
    Start-Sleep -Milliseconds 300
    $r = New-Object WinMove+RECT
    [WinMove]::GetWindowRect($hWnd, [ref]$r) | Out-Null
    $actualW = $r.Right - $r.Left
    $actualH = $r.Bottom - $r.Top
    if ([math]::Abs($actualW - $width) -le 2 -and [math]::Abs($actualH - $height) -le 2) { break }
    if ($actualW -le 0 -or $actualH -le 0) { break }
    $candW = [int]([math]::Round(($width * $candW) / [double]$actualW))
    $candH = [int]([math]::Round(($height * $candH) / [double]$actualH))
  }
}
