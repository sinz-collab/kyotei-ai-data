param(
    [string]$Python = "python",
    [string]$TargetDate = ""
)

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$script = Join-Path $root "automation\player_course_6m.py"
$arguments = @($script, "daily", "--soft-fail")
if ($TargetDate) {
    $arguments += @("--date", $TargetDate)
}

& $Python @arguments
if ($LASTEXITCODE -ne 0) {
    Write-Warning "player course foundation failed; the previous cache is retained and morning processing continues"
}
exit 0
