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

# The offline bundle holds compiled packages built for one specific Python
# version. pip's own message for a mismatch ("no matching distribution") does not
# say why, so check it here and say so plainly.
$wheelsPath = Join-Path $here 'wheelhouse'
if (Test-Path $wheelsPath) {
    $bundled = Get-ChildItem $wheelsPath -Filter '*win_amd64.whl' |
        ForEach-Object { if ($_.Name -match 'cp3(\d+)') { "3.$($Matches[1])" } } |
        Sort-Object -Unique
    $running = & python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
    if ($bundled -and ($bundled -notcontains $running)) {
        Write-Host ''
        Write-Host "  This offline bundle is built for Python $($bundled -join ', ')." -ForegroundColor Yellow
        Write-Host "  You are running Python $running." -ForegroundColor Yellow
        Write-Host ''
        Write-Host "  Install Python $($bundled[0]) from python.org (tick 'Add Python to PATH'),"
        Write-Host '  or ask for a bundle rebuilt for your version.'
        Write-Host ''
        throw "Python $($bundled[0]) is required by this bundle"
    }
}

Write-Host '==> Virtual environment'
if (-not (Test-Path '.venv')) { python -m venv .venv }

# If the offline bundle is present, install from it and never touch the network.
# -NoIndex makes pip refuse to contact PyPI at all, so an incomplete bundle fails
# loudly here rather than silently reaching out from a host that is not supposed
# to have internet access.
$wheels = Join-Path $here 'wheelhouse'
$offline = Test-Path $wheels
if ($offline) {
    Write-Host '    offline bundle found -- installing without network access'
    & .\.venv\Scripts\python.exe -m pip install --quiet --no-index --find-links $wheels -r requirements.txt
} else {
    Write-Host '    no offline bundle -- downloading from PyPI'
    & .\.venv\Scripts\python.exe -m pip install --quiet --upgrade pip
    & .\.venv\Scripts\python.exe -m pip install --quiet -r requirements.txt
}
if ($LASTEXITCODE -ne 0) { throw 'dependency installation failed' }

Write-Host '==> Excel automation'
# pywin32 is what lets the log and Daman workbook be recalculated before
# hand-off. Without it both files are still produced correctly, but their
# formulas are left uncalculated until someone opens them.
if ($offline) {
    & .\.venv\Scripts\python.exe -m pip install --quiet --no-index --find-links $wheels pywin32
} else {
    & .\.venv\Scripts\python.exe -m pip install --quiet pywin32
}
if ($LASTEXITCODE -ne 0) {
    Write-Host '    pywin32 unavailable -- the log and Daman workbooks will be written'
    Write-Host '    with formulas intact but uncalculated; Excel calculates them on open.'
}

Write-Host '==> Configuration'
if (-not (Test-Path '.env')) {
    Copy-Item (Join-Path $here '.env.example') '.env'
    Write-Host '    created app\.env'
} else {
    Write-Host '    app\.env already present, left unchanged'
}

# Generate the session signing key rather than asking someone to do it by hand.
# Left as CHANGE_ME it would be a shared, published secret; left unset entirely a
# new key is minted at every start and everyone is signed out on every restart.
if ((Get-Content '.env' -Raw) -match '(?m)^BMS_SECRET_KEY=CHANGE_ME') {
    $key = & .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
    (Get-Content '.env') |
        ForEach-Object { if ($_ -match '^BMS_SECRET_KEY=CHANGE_ME') { "BMS_SECRET_KEY=$key" } else { $_ } } |
        Set-Content '.env' -Encoding UTF8
    Write-Host '    generated a unique BMS_SECRET_KEY'
}

# An earlier .env.example shipped a literal seed password. Neutralise it, so the
# platform generates a strong one and prints it once instead of every install
# sharing the same known credential.
if ((Get-Content '.env' -Raw) -match '(?m)^BMS_SEED_PASSWORD=CHANGE_ME') {
    (Get-Content '.env') |
        ForEach-Object {
            if ($_ -match '^BMS_SEED_PASSWORD=CHANGE_ME') {
                '# BMS_SEED_PASSWORD=   # unset: a strong one is generated and printed once'
            } else { $_ }
        } | Set-Content '.env' -Encoding UTF8
    Write-Host '    removed the placeholder BMS_SEED_PASSWORD (one will be generated)'
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
