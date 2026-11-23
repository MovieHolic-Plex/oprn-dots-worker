# oprn-dots worker installer (Windows PowerShell). One command, no invite code:
#   powershell -ExecutionPolicy Bypass -File install.ps1 [-Name NAME] [-Contact EMAIL] [-Agent codex|claude] [-NoService]
# 1) signs up on the queue and saves the key, 2) picks your agent CLI, 3) registers a Task Scheduler task that runs the
# worker at logon (and restarts it), so it keeps taking jobs after reboots.
param([string]$Name = "", [string]$Contact = "", [string]$Agent = "", [string]$Origin = "", [switch]$NoService, [switch]$Yes)
$ErrorActionPreference = 'Stop'
function Die($m) { Write-Host "ERROR: $m" -ForegroundColor Red; exit 1 }
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$W = Join-Path $Here 'dots_worker.py'
$Svc = if ($env:DOTS_SERVICE_NAME) { $env:DOTS_SERVICE_NAME } else { 'oprn-dots-worker' }

$Py = $null
foreach ($c in @('py', 'python', 'python3')) { $cmd = Get-Command $c -ErrorAction SilentlyContinue; if ($cmd) { $Py = $cmd.Source; break } }
if (-not $Py) { Die "Python 3.8+ is required (https://www.python.org/downloads/). Install it and run this again." }
$PyArgs = @(); if ((Split-Path -Leaf $Py) -like 'py*.exe' -and (Split-Path -Leaf $Py) -notlike 'python*') { $PyArgs = @('-3') }
if (-not $Name) { $Name = "$env:USERNAME@$env:COMPUTERNAME" }

Write-Host "== 1/3 sign up as `"$Name`""
$a = @('join', '--name', $Name)
if ($Contact) { $a += @('--contact', $Contact) }; if ($Agent) { $a += @('--agent', $Agent) }; if ($Origin) { $a += @('--origin', $Origin) }
& $Py @PyArgs $W @a; if ($LASTEXITCODE) { Die "signup failed" }
$CfgDir = Join-Path $env:APPDATA 'oprn-dots-worker'

Write-Host "== 2/3 check the server and the agent CLI"
& $Py @PyArgs $W check
if ($LASTEXITCODE) { Write-Host "WARNING: no usable agent CLI yet. Install codex or claude (logged in), or set agent_command in $CfgDir\config.json. The task is registered anyway." -ForegroundColor Yellow }

if ($NoService) { Write-Host "== 3/3 skipped (-NoService). Run:  $Py $W run"; exit 0 }
Write-Host "== 3/3 background task (Task Scheduler, at logon)"
$Pyw = Join-Path (Split-Path -Parent $Py) 'pythonw.exe'; if (-not (Test-Path $Pyw)) { $Pyw = $Py }
$Action = New-ScheduledTaskAction -Execute $Pyw -Argument (($PyArgs + @("`"$W`"", 'run')) -join ' ') -WorkingDirectory $Here
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $Svc -Action $Action -Trigger $Trigger -Settings $Settings -Description 'oprn-dots background worker' -Force | Out-Null
Start-ScheduledTask -TaskName $Svc
Write-Host "task $Svc registered and started."
Write-Host ""
Write-Host "Done. The worker now takes jobs by itself. Log: $CfgDir\worker.log"
Write-Host "Your key is in $CfgDir\config.json - keep it private; if it leaks, tell the operator."
Write-Host "status: Get-ScheduledTask $Svc   stop: Unregister-ScheduledTask $Svc"
