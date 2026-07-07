param([int]$x1, [int]$y1, [int]$x2, [int]$y2, [int]$steps = 15)
Add-Type -AssemblyName System.Windows.Forms -ErrorAction SilentlyContinue
$sig = '[DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y); [DllImport("user32.dll")] public static extern void mouse_event(int f, int dx, int dy, int d, int e);'
Add-Type -MemberDefinition $sig -Name WinMouse -Namespace U -ErrorAction SilentlyContinue
[U.WinMouse]::SetCursorPos($x1, $y1) | Out-Null
Start-Sleep -Milliseconds 150
$landed = [System.Windows.Forms.Cursor]::Position
Write-Output "[osDrag-diag] requested=($x1,$y1)->($x2,$y2) landed=($($landed.X),$($landed.Y))"
[U.WinMouse]::mouse_event(2, 0, 0, 0, 0)   # MOUSEEVENTF_LEFTDOWN
Start-Sleep -Milliseconds 80
for ($i = 1; $i -le $steps; $i++) {
  $ix = [int]($x1 + ($x2 - $x1) * $i / $steps)
  $iy = [int]($y1 + ($y2 - $y1) * $i / $steps)
  [U.WinMouse]::SetCursorPos($ix, $iy) | Out-Null
  Start-Sleep -Milliseconds 12
}
Start-Sleep -Milliseconds 80
[U.WinMouse]::mouse_event(4, 0, 0, 0, 0)   # MOUSEEVENTF_LEFTUP
