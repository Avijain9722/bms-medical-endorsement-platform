<#
.SYNOPSIS
    Verify the installation on this machine.

.DESCRIPTION
    Checks the supplied insurer workbooks are unaltered, then runs the full test
    suite. Test tooling is not part of the runtime install -- a production
    environment should not carry pytest -- so this installs it into the same
    virtual environment first, from the offline bundle when one is present.

    Run it after installing, and again after any upgrade or restore.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File deploy\verify.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$app  = Resolve-Path (Join-Path $here '..\app')
Set-Location $app

$python = Join-Path $app '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    throw 'No virtual environment found. Run deploy\install.ps1 first.'
}

Write-Host '==> Test tooling'
$wheels = Join-Path $here 'wheelhouse'
if (Test-Path $wheels) {
    Write-Host '    installing from the offline bundle'
    & $python -m pip install --quiet --no-index --find-links $wheels -r requirements-dev.txt
} else {
    Write-Host '    downloading from PyPI'
    & $python -m pip install --quiet -r requirements-dev.txt
}
if ($LASTEXITCODE -ne 0) { throw 'could not install test tooling' }

Write-Host ''
Write-Host '==> Source integrity'
# Missing entries under 04_DEVELOPMENT_EXAMPLES are expected: those files are
# advisory and are deliberately excluded from the release package.
& $python tools\verify_sources.py

Write-Host ''
Write-Host '==> Test suite'
& $python -m pytest tests -q
if ($LASTEXITCODE -ne 0) { throw 'the test suite failed -- do not process live requests on this install' }

Write-Host ''
Write-Host 'Verification complete. If both sections passed, this installation is sound.'
