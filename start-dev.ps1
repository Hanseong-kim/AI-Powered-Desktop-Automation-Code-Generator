# Launches the three dev processes (server, ui, agent) each in its own
# PowerShell window, so you don't have to open 3 terminals by hand.
# agent.py needs Administrator rights (UIA/COM capture) — that window pops
# its own UAC prompt, same as running it manually.
$root = $PSScriptRoot

Start-Process powershell -ArgumentList '-NoExit', '-Command', "Set-Location '$root\server'; node server.js"
Start-Process powershell -ArgumentList '-NoExit', '-Command', "Set-Location '$root\ui'; npm run dev"
Start-Process powershell -Verb RunAs -ArgumentList '-NoExit', '-Command', "Set-Location '$root\agent'; python agent.py"
