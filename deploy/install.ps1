<#
.SYNOPSIS
    Install the BMS endorsement platform on a Windows host.

.DESCRIPTION
    Windows is the recommended host: it is the only platform where Excel can
    evaluate the BMS log's formulas and the Daman workbook's macro before
    hand-off. Safe to re-run.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File deploy\install.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$app  = Resolve-Path (Join-Path $here '..\app')
Set-Location $app

Write-Host '==> Python'
$version = (python --version) 2>&1
Write-Host "    $version"
python -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)"
if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 or newer is required' }

Write-Host '==> Virtual environment'
if (-not (Test-Path '.venv')) { python -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install --quiet --upgrade pip
& .\.venv\Scripts\python.exe -m pip install --quiet -r requirements.txt

Write-Host '==> Excel automation'
# pywin32 is what lets the log and Daman workbook be recalculated before
# hand-off. Without it both files are still produced correctly, but their
# formulas are left uncalculated until someone opens them.
& .\.venv\Scripts\python.exe -m pip install --quiet pywin32

Write-Host '==> Configuration'
if (-not (Test-Path '.env')) {
    Copy-Item (Join-Path $here '.env.example') '.env'
    Write-Host '    created app\.env -- edit it before starting (BMS_SECRET_KEY is required)'
} else {
    Write-Host '    app\.env already present, left unchanged'
}

Write-Host '==> Database'
Get-Content '.env' | Where-Object { $_ -match '^\s*[^#].*=' } | ForEach-Object {
    $name, $value = $_ -split '=', 2
    [Environment]::SetEnvironmentVariable($name.Trim(), $value.Trim(), 'Process')
}
& .\.venv\Scripts\python.exe -m alembic upgrade head

Write-Host '==> Host capabilities'
& .\.venv\Scripts\python.exe -c @"
from bms.intake import scanning
from bms.ocr.text import TextPipeline
from bms.outputs import recalc
print('    OCR engines        :', ', '.join(TextPipeline().available_engines()) or 'NONE')
print('    Virus scanning     :', scanning.describe_host()['engine'] or 'NONE')
calc = recalc.describe_host()
print('    Excel recalculation:', 'yes' if calc['excel_recalculation'] else 'no -- ' + calc['reason'])
"@

Write-Host ''
Write-Host 'Install complete.'
Write-Host '  Start: .\.venv\Scripts\uvicorn.exe bms.web.app:app --host 127.0.0.1 --port 8000'
Write-Host '  As a service, see deploy\WINDOWS_SERVICE.md'
