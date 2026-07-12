param(
    [string]$TitleSubstring = "System32"
)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$root = [System.Windows.Automation.AutomationElement]::RootElement
$clsCond = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::ClassNameProperty, "CabinetWClass"
)
$cabinets = $root.FindAll([System.Windows.Automation.TreeScope]::Children, $clsCond)
$win = $null
foreach ($w in $cabinets) {
    if ($w.Current.Name -like "*$TitleSubstring*") { $win = $w; break }
}
if (-not $win) { Write-Host "No CabinetWClass window matching '*$TitleSubstring*'"; exit 1 }
Write-Host ("Matched window: Name='{0}'" -f $win.Current.Name)

# Find the ListView (DirectUIHWND-hosted, modern Explorer)
$listCond = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::ClassNameProperty, "UIItemsView"
)
$list = $win.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $listCond)
if (-not $list) {
    Write-Host "UIItemsView not found, trying ControlType=List..."
    $listCond2 = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
        [System.Windows.Automation.ControlType]::List
    )
    $list = $win.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $listCond2)
}
if (-not $list) { Write-Host "No list control found in window"; exit 1 }
Write-Host ("List element: ClassName='{0}' ControlType='{1}'" -f $list.Current.ClassName, $list.Current.ControlType.ProgrammaticName)

$patternObj = $null
$hasScroll = $list.TryGetCurrentPattern([System.Windows.Automation.ScrollPattern]::Pattern, [ref]$patternObj)
if ($hasScroll) {
    $scroll = [System.Windows.Automation.ScrollPattern]$patternObj
    $before = $scroll.Current.VerticalScrollPercent
    Write-Host "ScrollPattern SUPPORTED. VerticalScrollPercent BEFORE: $before"
    $scroll.Scroll([System.Windows.Automation.ScrollAmount]::NoAmount, [System.Windows.Automation.ScrollAmount]::LargeIncrement)
    Start-Sleep -Milliseconds 300
    $after = $scroll.Current.VerticalScrollPercent
    Write-Host "VerticalScrollPercent AFTER:  $after"
    if ($after -ne $before) { Write-Host "RESULT: PASS -- scrolled via pure UIA ScrollPattern.Scroll(), zero pixels, zero SetCursorPos." }
    else { Write-Host "RESULT: NO-OP (list may already be fully scrolled, or too short to scroll)" }
} else {
    Write-Host "RESULT: ScrollPattern not supported on this control"
}
