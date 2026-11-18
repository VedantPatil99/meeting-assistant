# Builds dist\MeetingAssistant\MeetingAssistant.exe and a zip to share.
# Run from anywhere:  powershell -ExecutionPolicy Bypass -File packaging\build.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
Set-Location $PSScriptRoot

New-Item -ItemType Directory -Force build | Out-Null
& $python make_icon.py build\icon.ico
if ($LASTEXITCODE -ne 0) { throw "Icon step failed" }

& $python -m PyInstaller meeting_assistant.spec --noconfirm --clean --distpath "$root\dist" --workpath build\pyinstaller
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

Copy-Item "$PSScriptRoot\FOR_TESTERS.txt" "$root\dist\MeetingAssistant\READ ME FIRST.txt"
$version = (Select-String -Path "$root\pyproject.toml" -Pattern '^version = "(.+)"').Matches[0].Groups[1].Value
$zip = "$root\dist\MeetingAssistant-$version-windows.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path "$root\dist\MeetingAssistant" -DestinationPath $zip
Write-Host "Built $zip ($([math]::Round((Get-Item $zip).Length / 1MB)) MB)"
