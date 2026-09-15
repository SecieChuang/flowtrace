$ErrorActionPreference = 'Stop'

$taskNames = @(
    'Flowtrace Weekly Report',
    'Flowtrace Yesterday Reminder',
    'Flowtrace Screenshot Summary'
)
# 'Flowtrace Weekly Report' / 'Flowtrace Yesterday Reminder' are legacy entries kept for cleanup on old installs.

foreach ($taskName in $taskNames) {
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-Host "Removed: $taskName"
    }
    else {
        Write-Host "Not found: $taskName"
    }
}
