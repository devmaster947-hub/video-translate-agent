# Run from Windows PowerShell 5.1 or PowerShell 7. No credential arguments.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
try {
    $pythonCommand = Get-Command python -ErrorAction Stop
    & $pythonCommand.Source -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
    if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.10+ and add python to PATH.' }
    foreach ($tool in @('ffmpeg', 'ffprobe')) {
        $null = Get-Command $tool -ErrorAction Stop
        & $tool -version *> $null
        if ($LASTEXITCODE -ne 0) { throw "$tool could not run. Check PATH." }
    }
    Push-Location -LiteralPath $PSScriptRoot
    try {
        & $pythonCommand.Source -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed.' }
        $venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
        & $venvPython -m pip install -e '.[test]'
        if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
        & $venvPython scripts/sttn_setup.py
        $credentialStatusRaw = & $venvPython scripts/video_translate.py credential-status --json
        $credentialStatus = ($credentialStatusRaw -join "`n") | ConvertFrom-Json
        if ($credentialStatus.stored_providers -notcontains 'elevenlabs') {
            Write-Host 'Opening the secure ElevenLabs setup wizard. The key stays in Windows Credential Manager.'
            & $venvPython scripts/video_translate.py credential-setup --json
            if ($LASTEXITCODE -ne 0) {
                Write-Warning 'Credential setup was not completed. It will open again when video translation starts.'
            }
        }
        $rawReport = & $venvPython scripts/video_translate.py preflight --json
        $preflightExit = $LASTEXITCODE
        $report = ($rawReport -join "`n") | ConvertFrom-Json
        $rawReport | Write-Output
        if ($preflightExit -ne 0) {
            $otherErrors = @($report.errors | Where-Object { $_ -ne 'ELEVENLABS_CREDENTIAL_REQUIRED' })
            if ($preflightExit -eq 1 -and $report.errors -contains 'ELEVENLABS_CREDENTIAL_REQUIRED' -and $otherErrors.Count -eq 0) {
                Write-Warning 'Dependencies installed; complete the ElevenLabs browser setup before translating.'
            } else { throw 'Preflight failed. Resolve the reported dependency/configuration errors.' }
        } else { Write-Host 'Installation and local preflight passed. Remote provider access is not verified.' }
    } finally { Pop-Location }
} catch {
    Write-Error $_
    exit 1
}
exit 0
