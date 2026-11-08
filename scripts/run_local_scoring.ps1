# Screen and score waiting jobs through Claude Code on this machine.
#
# Registered with Task Scheduler to run at logon (see README, "Scoring on your laptop
# with Claude Code"). Safe to run more than once a day: scoring/claude_code.py keeps a
# daily call budget across runs. Output is appended to output/local_scoring/<date>.log.

$ErrorActionPreference = "Continue"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

$logDir = Join-Path $repo "output\local_scoring"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ((Get-Date -Format "yyyy-MM-dd") + ".log")

"=== $(Get-Date -Format s) local scoring run ===" | Out-File -FilePath $log -Append -Encoding utf8
$env:PYTHONIOENCODING = "utf-8"
& python -m scoring.score_jobs --local *>> $log
$code = $LASTEXITCODE
"=== exit $code ===" | Out-File -FilePath $log -Append -Encoding utf8
exit $code
