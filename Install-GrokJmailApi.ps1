<#
.SYNOPSIS
  Run the Grok Jmail HTTP/MCP API at logon so grok.com can message the main PCs.
#>
[CmdletBinding()]
param(
    [string]$Repo = 'C:\AI\Jmail',
    [string]$TaskName = 'GrokJmailApi',
    [int]$Port = 9847,
    [string]$BindHost = '127.0.0.1'
)
$ErrorActionPreference = 'Stop'
$script = Join-Path $Repo 'grok_api.py'
if (-not (Test-Path $script)) { Write-Error "grok_api.py not found at $Repo"; exit 1 }

$py = 'C:\AI\Alfred\.venv\Scripts\python.exe'
if (-not (Test-Path $py)) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $cmd) { Write-Error "No python found."; exit 1 }
    $py = $cmd.Source
}

# The task starts PowerShell so the port and bind host actually reach the process.
$ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$argLine = "-NoProfile -WindowStyle Hidden -Command `"& { `$env:GROK_JMAIL_PORT='$Port'; `$env:GROK_JMAIL_HOST='$BindHost'; & '$py' '$script' }`""
$action = New-ScheduledTaskAction -Execute $ps -Argument $argLine -WorkingDirectory $Repo
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Description 'Grok bot Jmail API (REST + MCP) for messaging Heavy and Command.' `
    -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName
Write-Host "Installed and started '$TaskName' on http://${BindHost}:$Port"
Write-Host "Token file (local only, not in git): $Repo\data\grok-token.txt"
Write-Host "Health: http://${BindHost}:$Port/v1/health"
Write-Host "MCP:    http://${BindHost}:$Port/mcp"
Write-Host "Grok.com needs public HTTPS. Run: tailscale funnel $Port"
exit 0
