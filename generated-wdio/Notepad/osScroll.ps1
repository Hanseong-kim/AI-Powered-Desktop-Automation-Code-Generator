param([string]$hwnd, [string]$selB64, [int]$delta)
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes -ErrorAction SilentlyContinue
$sig = '[DllImport("user32.dll")] public static extern bool PostMessageW(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);'
Add-Type -MemberDefinition $sig -Name WheelMsg -Namespace U -ErrorAction SilentlyContinue
if (-not $hwnd -or [int64]$hwnd -eq 0) { Write-Error 'osScroll: -hwnd is required'; exit 2 }
$root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]([int64]$hwnd))
if (-not $root) { Write-Error 'osScroll: AutomationElement.FromHandle failed'; exit 2 }

# selB64 = base64(UTF-8 JSON { automationId, className, name }) — 캡처 시점에
# agent.py가 ScrollPattern 보유 조상으로 걸어 올라가 기록한 컨테이너 셀렉터.
$sel = $null
if ($selB64) {
  try {
    $json = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($selB64))
    $sel = $json | ConvertFrom-Json
  } catch {}
}

$target = $null
if ($sel) {
  $conds = @()
  if ($sel.automationId) {
    $conds += New-Object System.Windows.Automation.PropertyCondition(
      [System.Windows.Automation.AutomationElement]::AutomationIdProperty, [string]$sel.automationId)
  }
  if ($sel.className) {
    $conds += New-Object System.Windows.Automation.PropertyCondition(
      [System.Windows.Automation.AutomationElement]::ClassNameProperty, [string]$sel.className)
  }
  if ($sel.name) {
    $conds += New-Object System.Windows.Automation.PropertyCondition(
      [System.Windows.Automation.AutomationElement]::NameProperty, [string]$sel.name)
  }
  foreach ($c in $conds) {
    try { $target = $root.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $c) } catch {}
    if ($target) { break }
  }
}
if (-not $target) { $target = $root }

# 1차: 대상(또는 가장 가까운 스크롤 가능 조상)의 ScrollPattern.
$walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
$cur = $target
$scroll = $null
for ($i = 0; $i -lt 10 -and $cur; $i++) {
  $p = $null
  if ($cur.TryGetCurrentPattern([System.Windows.Automation.ScrollPattern]::Pattern, [ref]$p)) {
    $sp = [System.Windows.Automation.ScrollPattern]$p
    if ($sp.Current.VerticallyScrollable) { $scroll = $sp; break }
  }
  try { $cur = $walker.GetParent($cur) } catch { break }
}
if ($scroll) {
  $before = $scroll.Current.VerticalScrollPercent
  # 휠 업(양수 delta) = 콘텐츠 위로 = SmallDecrement. 노치당 약 3줄.
  $dir = if ($delta -gt 0) { [System.Windows.Automation.ScrollAmount]::SmallDecrement }
         else { [System.Windows.Automation.ScrollAmount]::SmallIncrement }
  $n = [math]::Abs($delta) * 3
  for ($i = 0; $i -lt $n; $i++) {
    $scroll.Scroll([System.Windows.Automation.ScrollAmount]::NoAmount, $dir)
  }
  Start-Sleep -Milliseconds 150
  $after = $scroll.Current.VerticalScrollPercent
  Write-Output "[osScroll] ScrollPattern $before -> $after (delta=$delta)"
  exit 0
}

# 2차: hwnd-scoped WM_MOUSEWHEEL (PostMessageW — 비동기, SendMessage 금지).
$postH = [IntPtr]([int64]$hwnd)
$cur = $target
for ($i = 0; $i -lt 10 -and $cur; $i++) {
  try {
    $nh = $cur.Current.NativeWindowHandle
    if ($nh) { $postH = [IntPtr]$nh; break }
    $cur = $walker.GetParent($cur)
  } catch { break }
}
$cx = 0; $cy = 0
try {
  $r = $target.Current.BoundingRectangle
  $cx = [int]($r.X + $r.Width / 2)
  $cy = [int]($r.Y + $r.Height / 2)
} catch {}
$wp = [IntPtr]([int64]($delta * 120) -shl 16)
$lp = [IntPtr]([int64]((($cy -band 0xFFFF) -shl 16) -bor ($cx -band 0xFFFF)))
[U.WheelMsg]::PostMessageW($postH, 0x020A, $wp, $lp) | Out-Null
Write-Output "[osScroll] PostMessageW WM_MOUSEWHEEL hwnd=$postH delta=$delta (ScrollPattern unavailable)"
