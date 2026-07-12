param(
    [string]$ProcessName = "charmap",
    [string]$ScrollBarId = "204",
    [string]$TargetId = "108"
)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$sig = @'
[StructLayout(LayoutKind.Sequential)]
public struct SCROLLINFO {
    public uint cbSize;
    public uint fMask;
    public int nMin;
    public int nMax;
    public uint nPage;
    public int nPos;
    public int nTrackPos;
}
[DllImport("user32.dll")] public static extern bool GetScrollInfo(IntPtr hwnd, int nBar, ref SCROLLINFO info);
'@
Add-Type -MemberDefinition $sig -Name U32Si -Namespace Poc -ErrorAction SilentlyContinue

function Get-Win($procName) {
    $proc = Get-Process -Name $procName -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $proc) { return $null }
    $root = [System.Windows.Automation.AutomationElement]::RootElement
    $tops = $root.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)
    foreach ($w in $tops) { if ($w.Current.ProcessId -eq $proc.Id) { return $w } }
    return $null
}

function Get-ScrollPos($win, $id) {
    $cond = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::AutomationIdProperty, $id
    )
    $el = $win.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cond)
    if (-not $el) { return $null }
    $hwnd = [IntPtr]$el.Current.NativeWindowHandle
    if ($hwnd -eq [IntPtr]::Zero) { return "no-hwnd" }
    $si = New-Object Poc.U32Si+SCROLLINFO
    $si.cbSize = [System.Runtime.InteropServices.Marshal]::SizeOf([type][Poc.U32Si+SCROLLINFO])
    $si.fMask = 0x1F  # SIF_ALL
    $SB_CTL = 2
    $ok = [Poc.U32Si]::GetScrollInfo($hwnd, $SB_CTL, [ref]$si)
    if (-not $ok) { return "GetScrollInfo-failed" }
    return "pos=$($si.nPos) min=$($si.nMin) max=$($si.nMax) page=$($si.nPage)"
}

$win = Get-Win $ProcessName
if (-not $win) { Write-Host "Window not found"; exit 1 }
$before = Get-ScrollPos $win $ScrollBarId
Write-Host "BEFORE: $before"

powershell -NoProfile -File "$PSScriptRoot\uiaScroll.ps1" -ProcessName $ProcessName -AutomationId $TargetId -Direction down -Amount 5 | Write-Host

Start-Sleep -Milliseconds 300
$win2 = Get-Win $ProcessName
$after = Get-ScrollPos $win2 $ScrollBarId
Write-Host "AFTER:  $after"
