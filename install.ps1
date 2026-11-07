# Daku installer for native Windows.
#
# Creates the virtual environment, builds the SPA when Node is available, writes a first
# config, and registers an at-logon Scheduled Task. Re-running is safe: it never
# overwrites an existing config or database.
#
#   powershell -ExecutionPolicy Bypass -File .\install.ps1
#
# Optional switches:
#   -SkipWebBuild   use the shipped webdist instead of rebuilding
#   -NoService      do not register the Scheduled Task
#   -NoStart        register the task but do not start it now
[CmdletBinding()]
param(
    [switch]$SkipWebBuild,
    [switch]$NoService,
    [switch]$NoStart
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

function Write-Step([string]$Message) {
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Warn([string]$Message) {
    Write-Warning $Message
}

function Test-Command([string]$Name) {
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Install-WithWinget([string]$Id) {
    if (-not (Test-Command "winget")) {
        throw "winget is unavailable. Install $Id manually, then re-run this script."
    }
    & winget install --id $Id -e --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) {
        throw "winget could not install $Id (exit $LASTEXITCODE)."
    }
}

Write-Host ""
Write-Host "Daku installer" -ForegroundColor White
Write-Host "Repository: $Root"
Write-Host ""

Write-Step "Checking dependencies"
$uvLocalBin = Join-Path $HOME ".local\bin"
$env:Path = "$uvLocalBin;$env:Path"

if (-not (Test-Command "uv")) {
    Write-Step "Installing uv"
    Install-WithWinget "astral-sh.uv"
    $env:Path = "$uvLocalBin;$env:Path"
}
if (-not (Test-Command "uv")) {
    throw "uv is still not on PATH. Open a new terminal and re-run install.ps1."
}

$optional = @{
    "ffmpeg"     = "Gyan.FFmpeg"
    "ffprobe"    = "Gyan.FFmpeg"
    "aria2c"     = "aria2.aria2"
    "mpv"        = "mpv.net"
}
foreach ($name in $optional.Keys) {
    if (-not (Test-Command $name)) {
        Write-Warn "$name was not found. Install it with: winget install $($optional[$name])"
    }
}
Write-Warn "qBittorrent is a desktop app: install it with 'winget install qBittorrent.qBittorrent'"
Write-Warn "and enable its Web UI on 127.0.0.1:8080."

Write-Step "Creating Python environment"
if (Test-Path (Join-Path $Root "uv.lock")) {
    & uv sync --frozen --project $Root
} else {
    & uv sync --project $Root
}
if ($LASTEXITCODE -ne 0) {
    throw "uv sync failed."
}

$webIndex = Join-Path $Root "src\daku\webdist\index.html"
if (-not $SkipWebBuild) {
    Write-Step "Building the web UI"
    $built = $false
    if (-not (Test-Command "pnpm") -and (Test-Command "corepack")) {
        try {
            & corepack enable | Out-Null
            & corepack prepare pnpm@latest --activate | Out-Null
        } catch {
            Write-Warn "corepack could not enable pnpm."
        }
    }
    if (Test-Command "pnpm") {
        & pnpm -C (Join-Path $Root "web") install --frozen-lockfile
        if ($LASTEXITCODE -eq 0) {
            & pnpm -C (Join-Path $Root "web") build
            $built = ($LASTEXITCODE -eq 0)
        }
    } elseif (Test-Command "npm") {
        & npm --prefix (Join-Path $Root "web") install
        if ($LASTEXITCODE -eq 0) {
            & npm --prefix (Join-Path $Root "web") run build
            $built = ($LASTEXITCODE -eq 0)
        }
    }
    if (-not $built) {
        if (Test-Path $webIndex) {
            Write-Warn "Web build failed; using the shipped webdist."
        } else {
            throw "No pnpm/npm and no shipped webdist. Install Node, then re-run."
        }
    }
} elseif (-not (Test-Path $webIndex)) {
    throw "-SkipWebBuild was set but src\daku\webdist\index.html does not exist."
}

Write-Step "Creating directories"
$configDir = Join-Path $HOME ".config\daku"
$dataDir = Join-Path $HOME ".local\share\daku"
$downloadsDir = Join-Path $HOME "Downloads\Daku"
$moviesDir = Join-Path $HOME "Media\Movies"
$tvDir = Join-Path $HOME "Media\TV"
$animeDir = Join-Path $HOME "Media\Anime"
foreach ($dir in @($configDir, $dataDir, (Join-Path $dataDir "logs"), $downloadsDir, $moviesDir, $tvDir, $animeDir)) {
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
}

$configFile = Join-Path $configDir "config.json"
if (-not (Test-Path $configFile)) {
    Write-Step "Writing $configFile"
    $config = [ordered]@{
        host          = "0.0.0.0"
        port          = 8420
        downloads_dir = $downloadsDir.Replace("\", "/")
        movies_dir    = $moviesDir.Replace("\", "/")
        tv_dir        = $tvDir.Replace("\", "/")
        anime_dir     = $animeDir.Replace("\", "/")
        organize_mode = "copy"
        qbt_url       = "http://127.0.0.1:8080"
        qbt_user      = "admin"
        aria2_rpc     = "http://127.0.0.1:6800/jsonrpc"
        mpv_enabled   = $true
    }
    $json = $config | ConvertTo-Json
    # Write without a UTF-8 BOM so Python's json reader accepts the file.
    [System.IO.File]::WriteAllText($configFile, $json, (New-Object System.Text.UTF8Encoding($false)))
} else {
    Write-Step "Keeping the existing $configFile"
}

$secretsFile = Join-Path $configDir "secrets.json"
if (-not (Test-Path $secretsFile)) {
    Write-Step "Writing $secretsFile"
    $secrets = [ordered]@{
        qbt_pass           = ""
        aria2_secret       = ""
        jellyfin_api_key   = ""
        telegram_bot_token = ""
        tvdb_api_key       = ""
        omdb_api_key       = ""
    }
    $json = $secrets | ConvertTo-Json
    [System.IO.File]::WriteAllText($secretsFile, $json, (New-Object System.Text.UTF8Encoding($false)))
}

$dakuExe = Join-Path $Root ".venv\Scripts\daku.exe"
if (-not (Test-Path $dakuExe)) {
    throw "The virtual environment did not produce $dakuExe."
}

if (-not $NoService) {
    Write-Step "Registering the at-logon Scheduled Task"
    try {
        $action = New-ScheduledTaskAction -Execute $dakuExe -WorkingDirectory $Root
        $trigger = New-ScheduledTaskTrigger -AtLogOn
        $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
        Register-ScheduledTask -TaskName "Daku" -Action $action -Trigger $trigger -Settings $settings -Description "Daku media librarian" -Force | Out-Null
        Write-Host "Registered Scheduled Task 'Daku'."
        if (-not $NoStart) {
            Start-ScheduledTask -TaskName "Daku"
            Start-Sleep -Seconds 2
        }
    } catch {
        Write-Warn "Could not register the Scheduled Task: $($_.Exception.Message)"
        Write-Warn "Start Daku manually with: $dakuExe"
        Write-Warn "Or install NSSM and register it as a service."
    }
}

$ips = @()
try {
    $ips = Get-NetIPAddress -AddressFamily IPv4 |
        Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
        Select-Object -ExpandProperty IPAddress
} catch {
    $ips = @()
}
$lanIp = if ($ips.Count -gt 0) { $ips[0] } else { "<pc-ip>" }

Write-Host ""
Write-Host "Daku is installed." -ForegroundColor White
Write-Host ""
Write-Host "  Web UI      http://localhost:8420"
Write-Host "  LAN         http://${lanIp}:8420"
Write-Host "  Config      $configFile"
Write-Host "  Secrets     $secretsFile"
Write-Host "  Data        $dataDir"
Write-Host "  Downloads   $downloadsDir"
Write-Host "  Start now   $dakuExe"
Write-Host ""
Write-Host "Put the qBittorrent password in secrets.json, then run:"
Write-Host "  & '$dakuExe' doctor"
Write-Host ""
Write-Host "To allow other LAN devices (run PowerShell as Administrator):"
Write-Host "  New-NetFirewallRule -DisplayName 'Daku' -Direction Inbound -Protocol TCP -LocalPort 8420 -Action Allow"
Write-Host ""
Write-Warn "Daku has no authentication. Keep it on a trusted private network and never expose port 8420 to the internet."
