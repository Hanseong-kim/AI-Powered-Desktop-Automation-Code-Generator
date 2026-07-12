<#
PoC 2: scroll a native UI element via UIA ScrollPattern (or SendInput mouse-wheel
targeted at the element's owning hwnd) -- NO SetCursorPos, NO screen-pixel math.

Usage:
  powershell -File uiaScroll.ps1 -ProcessName mmc -AutomationId 12786 -Direction down -Amount 5
#>
param(
    [string]$ProcessName = "mmc",
    [string]$AutomationId = "12786",
    [ValidateSet("up","down")] [string]$Direction = "down",
    [int]$Amount = 5
)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$proc = Get-Process -Name $ProcessName -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $proc) { Write-Host "Process not found: $ProcessName"; exit 1 }

$root = [System.Windows.Automation.AutomationElement]::RootElement
$topLevels = $root.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)
$win = $null
foreach ($w in $topLevels) {
    if ($w.Current.ProcessId -eq $proc.Id) { $win = $w; break }
}
if (-not $win) { Write-Host "Top-level window not found for PID $($proc.Id)"; exit 1 }

$idCond = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::AutomationIdProperty, $AutomationId
)
$el = $win.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $idCond)
if (-not $el) { Write-Host "Element AutomationId=$AutomationId not found"; exit 1 }
Write-Host ("Target element: ClassName='{0}'" -f $el.Current.ClassName)

$patternObj = $null
$hasScroll = $el.TryGetCurrentPattern([System.Windows.Automation.ScrollPattern]::Pattern, [ref]$patternObj)

if ($hasScroll) {
    Write-Host "ScrollPattern supported -- using native UIA scroll (zero pixel math)."
    $scroll = [System.Windows.Automation.ScrollPattern]$patternObj
    $before = $scroll.Current.VerticalScrollPercent
    $amt = if ($Direction -eq "down") { [System.Windows.Automation.ScrollAmount]::LargeIncrement } else { [System.Windows.Automation.ScrollAmount]::LargeDecrement }
    for ($i = 0; $i -lt $Amount; $i++) {
        $scroll.Scroll([System.Windows.Automation.ScrollAmount]::NoAmount, $amt)
    }
    $after = $scroll.Current.VerticalScrollPercent
    Write-Host ("VerticalScrollPercent: before={0:N1} after={1:N1}" -f $before, $after)
    if ($after -ne $before) { Write-Host "RESULT: PASS (scroll position changed via ScrollPattern)" }
    else { Write-Host "RESULT: NO-OP (already at position, or content not scrollable)" }
} else {
    Write-Host "ScrollPattern NOT supported by this control -- falling back to SendInput mouse-wheel"
    Write-Host "scoped to the element's owning HWND (no SetCursorPos, no screen coordinates)."

    # Resolve the native hwnd that actually owns this UIA element (NativeWindowHandle),
    # walking up to the nearest ancestor that has one if the element itself is a
    # pure UIA leaf without its own hwnd (common for list/tree items).
    $hwnd = [IntPtr]$el.Current.NativeWindowHandle
    if ($hwnd -eq [IntPtr]::Zero) {
        $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
        $cur = $el
        for ($i = 0; $i -lt 6 -and $hwnd -eq [IntPtr]::Zero; $i++) {
            $cur = $walker.GetParent($cur)
            if (-not $cur) { break }
            $hwnd = [IntPtr]$cur.Current.NativeWindowHandle
        }
    }
    if ($hwnd -eq [IntPtr]::Zero) { Write-Host "RESULT: FAIL (no hwnd resolvable for SendInput fallback)"; exit 1 }

    $sig = @'
[DllImport("user32.dll")] public static extern IntPtr SendMessageW(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);
[DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
'@
    Add-Type -MemberDefinition $sig -Name U32Scroll -Namespace Poc -ErrorAction SilentlyContinue
    $WM_MOUSEWHEEL = 0x020E
    $delta = if ($Direction -eq "down") { -120 } else { 120 }
    $wParam = [IntPtr]([int64]$delta -shl 16)
    [Poc.U32Scroll]::SendMessageW($hwnd, $WM_MOUSEWHEEL, $wParam, [IntPtr]::Zero) | Out-Null
    Write-Host ("RESULT: sent WM_MOUSEWHEEL to hwnd=0x{0:X} (no coordinates involved)" -f $hwnd.ToInt64())
}
