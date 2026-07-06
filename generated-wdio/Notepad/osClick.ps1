param([int]$x, [int]$y, [string]$button = 'left', [int]$clicks = 1)
Add-Type -AssemblyName System.Windows.Forms -ErrorAction SilentlyContinue
$sig = '[DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y); [DllImport("user32.dll")] public static extern void mouse_event(int f, int dx, int dy, int d, int e);'
Add-Type -MemberDefinition $sig -Name WinMouse -Namespace U -ErrorAction SilentlyContinue
[U.WinMouse]::SetCursorPos($x, $y) | Out-Null
Start-Sleep -Milliseconds 150
# DIAGNOSTIC: requested vs landed cursor position — reveals DPI-scaling drift
# when DPI-aware getRect() coordinates get fed into DPI-unaware SetCursorPos.
# Write-Output (not Write-Host) so the Node caller's execSync stdout actually
# captures it.
$landed = [System.Windows.Forms.Cursor]::Position
Write-Output "[osClick-diag] requested=($x,$y) landed=($($landed.X),$($landed.Y))"
$down = if ($button -eq 'right') { 8 } else { 2 }
$up   = if ($button -eq 'right') { 16 } else { 4 }
for ($i = 0; $i -lt $clicks; $i++) {
  [U.WinMouse]::mouse_event($down, 0, 0, 0, 0)
  Start-Sleep -Milliseconds 50
  [U.WinMouse]::mouse_event($up, 0, 0, 0, 0)
  if ($i -lt ($clicks - 1)) { Start-Sleep -Milliseconds 80 }
}
