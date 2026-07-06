param([string]$b64)
Add-Type -AssemblyName System.Windows.Forms
$text = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($b64))
$special = '+^%~(){}[]'
Start-Sleep -Milliseconds 200
foreach ($ch in $text.ToCharArray()) {
  if ($ch -eq "`n") { [System.Windows.Forms.SendKeys]::SendWait("{ENTER}"); Start-Sleep -Milliseconds 15; continue }
  if ($ch -eq "`r") { continue }
  $s = [string]$ch
  if ($special.IndexOf($ch) -ge 0) { $s = "{$ch}" }
  [System.Windows.Forms.SendKeys]::SendWait($s)
  Start-Sleep -Milliseconds 15
}
