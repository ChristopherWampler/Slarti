# gateway_watchdog.ps1 — Slarti gateway health check + auto-restart
# Runs every 5 minutes via Windows Task Scheduler.
# Logs to logs/daily/gateway_watchdog-YYYY-MM-DD.log
# Alerts #admin-log via Discord webhook if a restart occurs.

$SlartRoot = 'C:\Openclaw\slarti'
$LogDir    = "$SlartRoot\logs\daily"
$EnvFile   = "$SlartRoot\.env"
$LogFile   = "$LogDir\gateway_watchdog-$(Get-Date -Format 'yyyy-MM-dd').log"

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Force -Path $LogDir | Out-Null }

function Write-Log($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg"
    Add-Content -Path $LogFile -Value $line
}

function Get-WebhookUrl {
    if (-not (Test-Path $EnvFile)) { return $null }
    foreach ($line in Get-Content $EnvFile) {
        if ($line -match '^DISCORD_ADMIN_WEBHOOK=(.+)$') { return $Matches[1].Trim() }
    }
    return $null
}

function Send-DiscordAlert($msg) {
    $url = Get-WebhookUrl
    if (-not $url) { Write-Log "WARNING: DISCORD_ADMIN_WEBHOOK not found in .env -- skipping alert"; return }
    try {
        Invoke-RestMethod -Uri $url -Method Post -ContentType 'application/json' `
            -Body (ConvertTo-Json @{ content = $msg } -Compress) | Out-Null
    } catch {
        Write-Log "WARNING: Discord alert failed: $_"
    }
}

# -- Health check --------------------------------------------------------------

$healthOutput = & openclaw gateway health 2>&1
$healthy = ($LASTEXITCODE -eq 0)

if ($healthy) {
    # Normal -- log nothing (silent on healthy to keep log clean)
    exit 0
}

# -- Gateway is down -- restart ------------------------------------------------

Write-Log "Gateway unhealthy -- restarting. Health output: $healthOutput"

& openclaw gateway start 2>&1 | ForEach-Object { Write-Log "  start: $_" }
Start-Sleep -Seconds 8

$healthOutput2 = & openclaw gateway health 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Log "Gateway restarted successfully: $healthOutput2"
    Send-DiscordAlert "[gateway_watchdog] Gateway was down and has been restarted. $(Get-Date -Format 'HH:mm') CDT"
} else {
    Write-Log "Gateway STILL DOWN after restart attempt: $healthOutput2"
    Send-DiscordAlert "[gateway_watchdog] ALERT: Gateway is down and restart FAILED. Manual intervention needed. $(Get-Date -Format 'HH:mm') CDT"
}
