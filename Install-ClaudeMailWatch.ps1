<#
.SYNOPSIS
  Install the Agent-Mail watcher as a scheduled task on THIS node.

.DESCRIPTION
  Registers AlfredClaudeMailWatch: every 2 minutes, poll the Command hub's
  Messages bus for operator mail with a BUILD:/TASK: subject addressed to this
  node's Claude id (default claude-heavy) and run the job via headless
  Claude Code. One instance at a time; long jobs are allowed to finish.

  Intended for HEAVY. Idempotent -- re-running replaces the task definition.
  Python: uses Alfred's venv when present (C:\AI\Alfred\.venv), otherwise the
  first python.exe on PATH -- the watcher is stdlib-only, so either works.
#>
[CmdletBinding()]
param(
    [string]$Repo = 'C:\AI\Agent-Mail',
    [string]$TaskName = 'AlfredClaudeMailWatch'
)
$ErrorActionPreference = 'Stop'

$script = Join-Path $Repo 'claude_mail_watch.py'
if (-not (Test-Path $script)) { Write-Error "claude_mail_watch.py not found at $Repo -- clone the Agent-Mail repo there first."; exit 1 }

$py = 'C:\AI\Alfred\.venv\Scripts\python.exe'
if (-not (Test-Path $py)) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $cmd) { Write-Error "No python found (Alfred venv or PATH)."; exit 1 }
    $py = $cmd.Source
}

# Claude Code CLI must exist on this node for jobs to run.
$claude = Get-Command claude -ErrorAction SilentlyContinue
if (-not $claude) {
    Write-Warning "claude CLI not found on PATH. Install Claude Code on this node, or set CLAUDE_CLI=<full path> in C:\AI\Alfred\.env. The watcher will install fine but jobs will fail-fast with a mail back to operator."
}

$action = New-ScheduledTaskAction -Execute $py -Argument "`"$script`"" -WorkingDirectory $Repo
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 2)
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Description 'Poll Alfred Messages for operator BUILD:/TASK: mail and run headless Claude Code jobs.' `
    -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName
Write-Host "Installed + started task '$TaskName' (every 2 min, single instance, no time limit)."
Write-Host "Trigger mail contract: from operator, subject 'BUILD: <title>' or 'TASK: <title>', body = instructions (optional first line 'repo: C:\path')."
exit 0
