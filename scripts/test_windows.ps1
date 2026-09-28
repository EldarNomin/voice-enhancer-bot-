# Command-contract smoke tests. Docker and messenger services are never started.
$ErrorActionPreference = 'Stop'
$global:VoiceDockerCalls = New-Object 'System.Collections.Generic.List[object]'
function global:docker {
    $global:LASTEXITCODE = 0
    $global:VoiceDockerCalls.Add(@($args))
    if ($args[0] -eq 'info') { return 'linux' }
    if ($args[0] -eq 'compose' -and $args[1] -eq 'version') { return '2.39.4' }
}
$TemporaryFolder = Join-Path ([System.IO.Path]::GetTempPath()) ('voice-test-' + [guid]::NewGuid())
New-Item -ItemType Directory -Path $TemporaryFolder | Out-Null
try {
    $SourceFile = Join-Path $TemporaryFolder 'voice sample.wav'
    $ResultFile = Join-Path $TemporaryFolder 'voice result.m4a'
    New-Item -ItemType File -Path $SourceFile | Out-Null
    & $PSScriptRoot/windows.ps1 -Action Check
    & $PSScriptRoot/windows.ps1 -Action Offline -Provider gtcrn -InputFile $SourceFile -OutputFile $ResultFile
    $RunCalls = @($global:VoiceDockerCalls | Where-Object { $_[0] -eq 'run' })
    if ($RunCalls.Count -ne 1) { throw 'Expected one offline container invocation.' }
    $RunArgs = $RunCalls[0]
    if ($RunArgs -notcontains 'none' -or $RunArgs -contains '--env-file') {
        throw 'Offline must have no network or env file.'
    }
    if ($RunArgs -notcontains "type=bind,source=$SourceFile,target=/input/source.wav,readonly") {
        throw 'Input mount must be read-only and preserve spaces.'
    }
    New-Item -ItemType File -Path $ResultFile | Out-Null
    $Rejected = $false
    try {
        & $PSScriptRoot/windows.ps1 -Action Offline -InputFile $SourceFile -OutputFile $ResultFile
    } catch { $Rejected = $_.Exception.Message -match 'already exists' }
    if (-not $Rejected) { throw 'Existing output was not rejected.' }
} finally {
    Remove-Item -LiteralPath $TemporaryFolder -Recurse
    Remove-Item Function:\docker
}
Write-Output 'Windows command-contract tests passed (Docker mocked).'
