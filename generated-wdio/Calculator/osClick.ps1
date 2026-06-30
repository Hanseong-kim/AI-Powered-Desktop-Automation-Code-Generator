param([int]$x, [int]$y)
$sig = '[DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y); [DllImport("user32.dll")] public static extern void mouse_event(int f, int dx, int dy, int d, int e);'
Add-Type -MemberDefinition $sig -Name WinMouse -Namespace U -ErrorAction SilentlyContinue
[U.WinMouse]::SetCursorPos($x, $y) | Out-Null
Start-Sleep -Milliseconds 150
[U.WinMouse]::mouse_event(2, 0, 0, 0, 0)
Start-Sleep -Milliseconds 50
[U.WinMouse]::mouse_event(4, 0, 0, 0, 0)
