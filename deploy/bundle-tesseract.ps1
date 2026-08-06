<#
.SYNOPSIS
    Copy Tesseract INTO the project, so OCR travels with the platform.

.DESCRIPTION
    A locked-down BMS host may not permit an installer to run, or may have no
    internet access. This takes a Tesseract already installed on a machine you
    do control and assembles a self-contained copy under vendor\tesseract\,
    which the platform then finds automatically -- no BMS_TESSERACT_CMD, no
    administrator rights on the target machine.

    Copy the whole project folder to the BMS host afterwards.

    On Windows this is a straight folder copy: the DLLs sit beside
    tesseract.exe and Windows loads them from there, so no wrapper is needed.

.PARAMETER Source
    The Tesseract installation to copy. Defaults to the usual install location.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File deploy\bundle-tesseract.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File deploy\bundle-tesseract.ps1 -Source "D:\tools\Tesseract-OCR"
#>
[CmdletBinding()]
param(
    [string]$Source = "$env:ProgramFiles\Tesseract-OCR"
)

$ErrorActionPreference = 'Stop'
$here   = Split-Path -Parent $MyInvocation.MyCommand.Path
$vendor = Join-Path (Split-Path -Parent $here) 'vendor\tesseract'

if (-not (Test-Path (Join-Path $Source 'tesseract.exe'))) {
    Write-Host "No tesseract.exe under: $Source" -ForegroundColor Red
    Write-Host ''
    Write-Host 'Install it first on a machine that has internet access:'
    Write-Host '  https://github.com/UB-Mannheim/tesseract/wiki'
    Write-Host ''
    Write-Host 'During setup, tick the Arabic language pack under'
    Write-Host '"Additional language data" -- BMS documents need it.'
    Write-Host ''
    Write-Host 'Then re-run this script, or pass -Source with the install path.'
    exit 1
}

Write-Host '==> Source'
Write-Host "    $Source"
& (Join-Path $Source 'tesseract.exe') --version 2>&1 | Select-Object -First 1 | ForEach-Object { Write-Host "    $_" }

Write-Host '==> Copying into the project'
if (Test-Path $vendor) { Remove-Item $vendor -Recurse -Force }
New-Item -ItemType Directory -Path $vendor -Force | Out-Null
Copy-Item -Path (Join-Path $Source '*') -Destination $vendor -Recurse -Force

$size = (Get-ChildItem $vendor -Recurse | Measure-Object -Property Length -Sum).Sum / 1MB
Write-Host ("    copied {0:N0} MB into vendor\tesseract\" -f $size)

Write-Host '==> Language data'
$tessdata = Join-Path $vendor 'tessdata'
if (-not (Test-Path $tessdata)) {
    Write-Host '    WARNING: no tessdata folder was copied. OCR will not work.' -ForegroundColor Yellow
    Write-Host '    Re-run the Tesseract installer and include the language data.' -ForegroundColor Yellow
} else {
    $langs = Get-ChildItem $tessdata -Filter '*.traineddata' | ForEach-Object { $_.BaseName }
    Write-Host "    $($langs -join ', ')"
    if ($langs -notcontains 'ara') {
        Write-Host '    NOTE: Arabic (ara) is missing. Arabic documents will not be read.' -ForegroundColor Yellow
        Write-Host '    Re-run the Tesseract installer and tick Arabic under the language data.' -ForegroundColor Yellow
    }
}

Write-Host ''
Write-Host '==> Checking the bundled copy'
& (Join-Path $vendor 'tesseract.exe') --list-langs 2>&1 | ForEach-Object { Write-Host "    $_" }

Write-Host ''
Write-Host 'Bundled into vendor\tesseract\. The platform finds it automatically.'
Write-Host 'Confirm after starting it:  curl http://127.0.0.1:8000/health'
