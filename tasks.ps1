<#
.SYNOPSIS
    Task runner for the technology-intelligence repository.

.DESCRIPTION
    A mirror of the Makefile for machines without GNU make, which is the common
    case on Windows. Every target runs exactly the command the Makefile runs, so
    the two stay interchangeable.

.EXAMPLE
    .\tasks.ps1
    .\tasks.ps1 test
    .\tasks.ps1 demo -DemoN 200
    .\tasks.ps1 pipeline -InputFile records.txt -Out out/run1
    .\tasks.ps1 pipeline -InputFile records.txt -Specs authors,keywords

.NOTES
    Run `Get-Help .\tasks.ps1 -Detailed` for the parameter reference.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet(
        'help', 'test', 'test-openalex', 'test-techintel', 'test-unittest',
        'demo', 'pipeline', 'networks', 'compile', 'clean'
    )]
    [string]$Task = 'help',

    # WoS/CSV export to analyse. Required by the `pipeline` target.
    [string]$InputFile,

    [string]$Out = 'out',
    [int]$DemoN = 80,
    [string]$Specs = '',
    [string]$Python = 'python',
    [string]$NetworksCsv = 'Networks/acoustic sensor.csv'
)

$ErrorActionPreference = 'Stop'

function Invoke-Python {
    param([string[]]$Arguments)

    Write-Host "> $Python $($Arguments -join ' ')" -ForegroundColor DarkGray
    & $Python @Arguments
    if ($LASTEXITCODE -ne 0) {
        Write-Host "! exited with code $LASTEXITCODE" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

# `--specs` is only appended when the caller supplied a value.
$specsArgs = if ($Specs) { @('--specs', $Specs) } else { @() }

# An empty -InputFile is omitted entirely rather than passed as "": PowerShell
# collapses an empty argument, which would make argparse report "expected one
# argument" instead of the CLI's own "pass exactly one of" message. (The
# Makefile passes a genuine "" through the shell, which the CLI also handles.)
$inputArgs = if ($InputFile) { @('--input', $InputFile) } else { @() }

switch ($Task) {
    'help' {
        Write-Host 'Technology-intelligence repository tasks'
        Write-Host '-------------------------------------------------------------'
        Write-Host '  .\tasks.ps1 test            both test suites via pytest'
        Write-Host '  .\tasks.ps1 test-unittest   both test suites via unittest discover'
        Write-Host '  .\tasks.ps1 demo            pipeline on synthetic data, output in out/demo'
        Write-Host '  .\tasks.ps1 pipeline        pipeline on -InputFile'
        Write-Host '  .\tasks.ps1 networks        Networks/Networks.py on -NetworksCsv'
        Write-Host '  .\tasks.ps1 compile         byte-compile every Python file (syntax check)'
        Write-Host '  .\tasks.ps1 clean           remove caches and generated output'
        Write-Host '-------------------------------------------------------------'
        Write-Host "  -Python $Python  -Out $Out  -DemoN $DemoN  -Specs '$Specs'"
    }

    'test' { Invoke-Python @('-m', 'pytest', 'openalex/tests', 'techintel/tests', '-q') }
    'test-openalex' { Invoke-Python @('-m', 'pytest', 'openalex/tests', '-q') }
    'test-techintel' { Invoke-Python @('-m', 'pytest', 'techintel/tests', '-q') }

    'test-unittest' {
        Invoke-Python @('-m', 'unittest', 'discover', '-s', 'openalex/tests', '-t', '.')
        Invoke-Python @('-m', 'unittest', 'discover', '-s', 'techintel/tests', '-t', '.')
    }

    'demo' {
        Invoke-Python (@('-m', 'techintel.pipeline', '--demo', "$DemoN", '--out', "$Out/demo") + $specsArgs)
    }

    'pipeline' {
        # -InputFile is validated by the CLI, which exits 2 with an actionable
        # message when it is missing.
        Invoke-Python (@('-m', 'techintel.pipeline') + $inputArgs + @('--out', "$Out") + $specsArgs)
    }

    'networks' {
        Invoke-Python @('Networks/Networks.py', '--csv', $NetworksCsv, '--out', "$Out/networks")
    }

    'compile' {
        Invoke-Python @('-m', 'compileall', '-q', 'techintel', 'openalex', 'Networks')
    }

    'clean' {
        Invoke-Python @(
            '-c',
            "import pathlib, shutil; [shutil.rmtree(p, ignore_errors=True) for p in pathlib.Path('.').rglob('__pycache__')]"
        )
        Invoke-Python @(
            '-c',
            "import shutil; [shutil.rmtree(p, ignore_errors=True) for p in ('.pytest_cache', '$Out')]"
        )
        Write-Host 'Cleaned __pycache__, .pytest_cache and ' -NoNewline
        Write-Host $Out -ForegroundColor Yellow
    }
}
