param(
    [string]$ProcessName = "regedit",
    [int]$MaxDepth = 3
)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$proc = Get-Process -Name $ProcessName -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $proc) { Write-Host "Process not found: $ProcessName"; exit 1 }

$root = [System.Windows.Automation.AutomationElement]::RootElement
$all = $root.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)
$win = $null
foreach ($w in $all) {
    if ($w.Current.ProcessId -eq $proc.Id) { $win = $w; break }
}
if (-not $win) { Write-Host "Top-level window not found for PID $($proc.Id)"; exit 1 }

Write-Host ("Found window: Name='{0}' ClassName='{1}' PID={2}" -f $win.Current.Name, $win.Current.ClassName, $proc.Id)

function Walk($el, $depth) {
    if ($depth -gt $MaxDepth) { return }
    $indent = "  " * $depth
    $children = $el.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)
    foreach ($c in $children) {
        $name = $c.Current.Name
        $aid  = $c.Current.AutomationId
        $cls  = $c.Current.ClassName
        $ctrl = $c.Current.ControlType.ProgrammaticName -replace 'ControlType\.', ''
        Write-Host ("{0}[{1}] Name='{2}' AutomationId='{3}' ClassName='{4}'" -f $indent, $ctrl, $name, $aid, $cls)
        Walk $c ($depth + 1)
    }
}
Walk $win 1
