<#
.SYNOPSIS
  Install the Jmail watcher as a scheduled task on THIS node.

.DESCRIPTION
  Registers JmailWatch: every 2 minutes, poll the Jmail bus for operator mail
  with a BUILD: or TASK: subject addressed to this node's Claude id (default
  claude-heavy) and run the job via headless Claude Code. One instance at a
  time; long jobs are allowed to finish. The repetition does not expire.

  Intended for HEAVY. Re-running replaces the task definition.
  Python: Alfred's venv when present (C:\AI\Alfred\.venv), otherwise python on
  PATH. The watcher is stdlib-only, so either works.
#>
[CmdletBinding()]
param(
    [string]$Repo = 'C:\AI\Jmail',
    [string]$TaskName = 'JmailWatch'
)
$ErrorActionPreference = 'Stop'

$script = Join-Path $Repo 'claude_mail_watch.py'
if (-not (Test-Path $script)) {
    Write-Error "claude_mail_watch.py not found at $Repo -- clone the Jmail repo there first."
    exit 1
}

$py = 'C:\AI\Alfred\.venv\Scripts\python.exe'
if (-not (Test-Path $py)) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $cmd) { Write-Error "No python found (Alfred venv or PATH)."; exit 1 }
    $py = $cmd.Source
}

$claude = Get-Command claude -ErrorAction SilentlyContinue
if (-not $claude) {
    Write-Warning "claude CLI not found on PATH. Install Claude Code, or set CLAUDE_CLI to its full path in Alfred's .env. The watcher will install, and jobs will mail operator that they did not start."
}

$action = New-ScheduledTaskAction -Execute $py -Argument "`"$script`"" -WorkingDirectory $Repo
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 2)
# An empty duration on a once-trigger means the repetition does not stop after a day.
$trigger.Repetition.Duration = ''
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Description 'Poll Jmail for operator BUILD:/TASK: mail and run headless Claude Code jobs.' `
    -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName
Write-Host "Installed and started '$TaskName' (every 2 min, single instance, no expiry, no time limit)."
Write-Host "Python: $py"
Write-Host "Mail contract: from operator, subject 'BUILD: <title>' or 'TASK: <title>', body = instructions. Optional first line: repo: C:\path"
exit 0
