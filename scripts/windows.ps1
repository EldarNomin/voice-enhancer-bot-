# Compatible with Windows PowerShell 5.1 and PowerShell 7.
[CmdletBinding()]
param(
    [ValidateSet('Init', 'Check', 'Start', 'Stop', 'Status', 'Offline')]
    [string]$Action = 'Check',
    [ValidateSet('telegram', 'max', 'both')][string]$Channel = 'telegram',
    [ValidateSet('ffmpeg', 'deepfilter', 'gtcrn')][string]$Provider = 'ffmpeg',
    [string]$InputFile,
    [string]$OutputFile,
    [ValidateSet('natural', 'studio', 'reels', 'podcast')][string]$Preset = 'natural'
)
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $ProjectRoot
try {
    if ($Action -eq 'Init') {
        if (-not (Test-Path -LiteralPath '.env')) {
            # No overwrite and no secret values printed.
            Copy-Item -LiteralPath '.env.example' -Destination '.env' -ErrorAction Stop
            Write-Output 'Created .env. Add messenger credentials locally when ready.'
        } else { Write-Output 'Existing .env preserved.' }
        return
    }
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw 'Install Docker Desktop with WSL 2 first. See docs/WINDOWS.md.'
    }
    $DockerOS = & docker info --format '{{.OSType}}' 2>$null
    if ($LASTEXITCODE -ne 0 -or $DockerOS -ne 'linux') {
        throw 'Start Docker Desktop and select Linux containers (WSL 2).'
    }
    $ComposeVersion = & docker compose version --short 2>$null
    if ($LASTEXITCODE -ne 0 -or $ComposeVersion -notmatch '^v?(\d+\.\d+\.\d+)') {
        throw 'Docker Compose is unavailable.'
    }
    if ([version]$Matches[1] -lt [version]'2.24.4') {
        throw 'Update Docker Compose to 2.24.4 or newer.'
    }
    if ($Action -eq 'Check') {
        Write-Output 'Docker Linux engine and Compose are ready. No tokens checked.'
        return
    }
    if ($Action -eq 'Offline') {
        if (-not $InputFile -or -not $OutputFile) {
            throw 'Offline requires -InputFile and -OutputFile (new .m4a or .mp4).'
        }
        $SourcePath = (Resolve-Path -LiteralPath $InputFile).Path
        if (-not (Test-Path -LiteralPath $SourcePath -PathType Leaf)) {
            throw 'Input must be a file.'
        }
        $OutputPath = [System.IO.Path]::GetFullPath($OutputFile)
        if (Test-Path -LiteralPath $OutputPath) { throw 'Output already exists; choose a new name.' }
        $OutputFolder = Split-Path -Parent $OutputPath
        if (-not (Test-Path -LiteralPath $OutputFolder -PathType Container)) {
            throw 'Create the output folder first.'
        }
        if ($SourcePath.Contains(',') -or $OutputFolder.Contains(',')) {
            throw 'Docker mount paths must not contain commas.'
        }
        $Target = if ($Provider -eq 'ffmpeg') { 'standard' } else { $Provider }
        $Image = "voice-enhancer-offline:$Provider"
        & docker build --target $Target --tag $Image .
        if ($LASTEXITCODE -ne 0) { throw 'Offline image build failed.' }
        $ContainerInput = '/input/source' + [System.IO.Path]::GetExtension($SourcePath)
        $ContainerOutput = '/output/' + [System.IO.Path]::GetFileName($OutputPath)
        # No .env, credentials or messenger services. Input is read-only.
        & docker run --rm --network none --mount "type=bind,source=$SourcePath,target=$ContainerInput,readonly" --mount "type=bind,source=$OutputFolder,target=/output" $Image python -m voice_enhancer.offline $ContainerInput $ContainerOutput --provider $Provider --preset $Preset
        if ($LASTEXITCODE -ne 0) { throw 'Offline processing failed.' }
        return
    }
    if (-not (Test-Path -LiteralPath '.env')) { throw 'Run -Action Init first.' }
    $ComposeArgs = @('compose', '-f', 'docker-compose.yml')
    if ($Channel -eq 'max') { $ComposeArgs += @('-f', 'compose.max-only.yml') }
    if ($Provider -ne 'ffmpeg') { $ComposeArgs += @('-f', "compose.$Provider.yml") }
    if ($Channel -ne 'telegram') { $ComposeArgs += @('--profile', 'max') }
    if ($Action -eq 'Start') {
        # Capture expanded configuration in memory; never display credential values.
        $ConfigText = & docker @ComposeArgs config --format json 2>$null
        if ($LASTEXITCODE -ne 0) { throw 'Invalid Compose configuration.' }
        $Config = ($ConfigText -join "`n") | ConvertFrom-Json
        if ($Channel -ne 'max') {
            foreach ($Key in @('BOT_TOKEN', 'TELEGRAM_API_ID', 'TELEGRAM_API_HASH')) {
                if (-not $Config.services.bot.environment.$Key) { throw "Missing setting: $Key" }
            }
        }
        if ($Channel -ne 'telegram') {
            if (-not $Config.services.'max-ingress'.environment.MAX_BOT_TOKEN) {
                throw 'Missing setting: MAX_BOT_TOKEN'
            }
            if ($Config.services.'max-ingress'.environment.MAX_WEBHOOK_SECRET.Length -lt 32) {
                throw 'MAX_WEBHOOK_SECRET must contain at least 32 characters.'
            }
        }
        if ($Config.services.worker.environment.ENHANCEMENT_PROVIDER -ne $Provider) {
            throw 'ENHANCEMENT_PROVIDER conflicts with -Provider. Update .env or choose the matching provider.'
        }
        & docker @ComposeArgs up -d --build
    } elseif ($Action -eq 'Stop') {
        # Stop only; do not delete containers, volumes or media.
        & docker @ComposeArgs stop
    } else {
        & docker @ComposeArgs exec -T worker python -m voice_enhancer.diagnostics
    }
    if ($LASTEXITCODE -ne 0) { throw 'Operation failed or diagnostics reported not ready.' }
} finally {
    Pop-Location
}
