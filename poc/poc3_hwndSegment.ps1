<#
PoC 3: HWND-based multi-window segmentation.
Tests whether a SINGLE WinAppDriver session can enumerate/switch between
windows via GET window_handles + POST window (Selenium-style), as an
alternative to spawning a brand-new scoped appTopLevelWindow session per
child window (the current, slow ~15-20s-per-window approach in server.js).
#>
param(
    [string]$WadUrl = "http://127.0.0.1:4723"
)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

function Get-TopHwnd($procName) {
    $proc = Get-Process -Name $procName -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $proc) { return $null }
    $root = [System.Windows.Automation.AutomationElement]::RootElement
    $tops = $root.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)
    foreach ($w in $tops) {
        if ($w.Current.ProcessId -eq $proc.Id -and $w.Current.NativeWindowHandle -ne 0) { return $w }
    }
    return $null
}

function Invoke-Uia($el) {
    $pat = $null
    if ($el.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$pat)) {
        ([System.Windows.Automation.InvokePattern]$pat).Invoke()
        return $true
    }
    return $false
}

$paintWin = Get-TopHwnd "mspaint"
if (-not $paintWin) { Write-Host "Paint window not found"; exit 1 }
$paintHwndDec = $paintWin.Current.NativeWindowHandle
$paintHwndHex = "0x" + [Convert]::ToString($paintHwndDec, 16)
Write-Host "Paint top-level hwnd: $paintHwndHex"

# 1) Create WAD session scoped to Paint's hwnd (matches server.js's existing pattern)
$caps = @{
    desiredCapabilities = @{
        platformName = "Windows"
        deviceName = "WindowsPC"
        appTopLevelWindow = $paintHwndHex
    }
} | ConvertTo-Json -Depth 5
$session = Invoke-RestMethod -Uri "$WadUrl/session" -Method Post -Body $caps -ContentType "application/json"
$sid = $session.sessionId
Write-Host "Session created: $sid"

# 2) Baseline window_handles
$handles1 = Invoke-RestMethod -Uri "$WadUrl/session/$sid/window_handles" -Method Get
Write-Host ("window_handles BEFORE opening dialog: {0}" -f ($handles1.value -join ", "))

# 3) Open the File menu, then click "Open" to spawn a new top-level dialog (owned window)
$fileMenuItem = $paintWin.FindFirst(
    [System.Windows.Automation.TreeScope]::Descendants,
    (New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty, "파일"))
)
if (-not $fileMenuItem) { Write-Host "File menu not found"; exit 1 }
Invoke-Uia $fileMenuItem | Out-Null
Start-Sleep -Milliseconds 500

$root = [System.Windows.Automation.AutomationElement]::RootElement
$openItem = $root.FindFirst(
    [System.Windows.Automation.TreeScope]::Descendants,
    (New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty, "열기"))
)
if (-not $openItem) { Write-Host "Open menu item not found (menu may not have opened)"; exit 1 }
Invoke-Uia $openItem | Out-Null
Start-Sleep -Milliseconds 1200

# 4) window_handles AFTER dialog opens
$handles2 = Invoke-RestMethod -Uri "$WadUrl/session/$sid/window_handles" -Method Get
Write-Host ("window_handles AFTER opening dialog:  {0}" -f ($handles2.value -join ", "))

$newHandles = $handles2.value | Where-Object { $handles1.value -notcontains $_ }
if ($newHandles.Count -gt 0) {
    Write-Host "NEW WINDOW HANDLE DETECTED: $($newHandles -join ', ') -- attempting POST /window switch"
    try {
        $switchBody = @{ handle = $newHandles[0]; name = $newHandles[0] } | ConvertTo-Json
        Invoke-RestMethod -Uri "$WadUrl/session/$sid/window" -Method Post -Body $switchBody -ContentType "application/json" | Out-Null
        Write-Host "RESULT: switchToWindow SUCCEEDED via same session"
    } catch {
        Write-Host "RESULT: switchToWindow FAILED -- $($_.Exception.Message)"
    }
} else {
    Write-Host "RESULT: window_handles did NOT change -- WAD does not expose the child dialog as a separate handle in this session"
}

# 5) Cleanup: close the Open dialog via Escape (safe, no file selection), then delete session, close Paint.
$hwndDlg = $null
$openWin = $null
for ($i = 0; $i -lt 10; $i++) {
    $tops = $root.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)
    foreach ($w in $tops) {
        if ($w.Current.ClassName -eq "#32770" -and $w.Current.ProcessId -eq $paintWin.Current.ProcessId) { $openWin = $w; break }
    }
    if ($openWin) { break }
    Start-Sleep -Milliseconds 300
}
if ($openWin) {
    $sig = '[DllImport("user32.dll")] public static extern bool PostMessage(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);'
    Add-Type -MemberDefinition $sig -Name U32Esc -Namespace Poc3 -ErrorAction SilentlyContinue
    $hwndDlg = [IntPtr]$openWin.Current.NativeWindowHandle
    $WM_CLOSE = 0x0010
    [Poc3.U32Esc]::PostMessage($hwndDlg, $WM_CLOSE, [IntPtr]::Zero, [IntPtr]::Zero) | Out-Null
    Write-Host "Closed Open dialog via WM_CLOSE (no file selected/opened)."
} else {
    Write-Host "Open dialog window not found for cleanup (may have already closed)."
}

try { Invoke-RestMethod -Uri "$WadUrl/session/$sid" -Method Delete | Out-Null } catch {}
Write-Host "Session deleted."
