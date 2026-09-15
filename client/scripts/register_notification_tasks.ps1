$ErrorActionPreference = 'Stop'

$clientDir = (Split-Path -Parent $MyInvocation.MyCommand.Path) + '\..'
$clientDir = (Resolve-Path $clientDir).Path
$configPath = Join-Path $clientDir 'config\config.json'
$runnerPath = Join-Path $clientDir 'src\run_scheduled_job.py'

if (-not (Test-Path $configPath)) {
    throw "config.json not found: $configPath"
}
if (-not (Test-Path $runnerPath)) {
    throw "run_scheduled_job.py not found: $runnerPath"
}

function Get-ConfigValue($config, $name, $default = $null) {
    if ($config.PSObject.Properties.Name -contains $name) {
        return $config.$name
    }
    return $default
}

function Parse-Time($value, $fieldName) {
    if ($value -notmatch '^(?:[01]\d|2[0-3]):[0-5]\d$') {
        throw "$fieldName must be HH:mm, got: $value"
    }
    return [datetime]::ParseExact($value, 'HH:mm', $null)
}

function Parse-Days($value, $fieldName) {
    $allowed = @('MON','TUE','WED','THU','FRI','SAT','SUN')
    $days = @()
    foreach ($item in ($value -split ',')) {
        $day = $item.Trim().ToUpperInvariant()
        if (-not $day) { continue }
        if ($allowed -notcontains $day) {
            throw "$fieldName contains invalid day: $day"
        }
        if ($days -notcontains $day) {
            $days += $day
        }
    }
    if ($days.Count -eq 0) {
        throw "$fieldName cannot be empty"
    }
    return $days
}

$config = Get-Content $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
$pythonExe = [string](Get-ConfigValue $config 'automation_python_executable' '')
if (-not $pythonExe) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCommand) {
        throw 'Cannot find python executable. Set automation_python_executable in config.json.'
    }
    $pythonExe = $pythonCommand.Source
}
if (-not (Test-Path $pythonExe)) {
    throw "Python executable not found: $pythonExe"
}

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType S4U -RunLevel Limited

$legacyTaskNames = @('Flowtrace Weekly Report', 'Flowtrace Yesterday Reminder')
foreach ($legacyName in $legacyTaskNames) {
    if (Get-ScheduledTask -TaskName $legacyName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $legacyName -Confirm:$false
        Write-Host "Removed legacy task: $legacyName"
    }
}

$screenshotEnabled = [bool](Get-ConfigValue $config 'screenshot_summary_schedule_enabled' $true)
$screenshotDays = [string](Get-ConfigValue $config 'screenshot_summary_schedule_days' 'MON,TUE,WED,THU,FRI,SAT,SUN')
$screenshotTime = [string](Get-ConfigValue $config 'screenshot_summary_schedule_time' '23:30')
$screenshotTaskName = 'Flowtrace Screenshot Summary'

if ($screenshotEnabled) {
    $screenshotAt = Parse-Time $screenshotTime 'screenshot_summary_schedule_time'
    $screenshotDaysParsed = Parse-Days $screenshotDays 'screenshot_summary_schedule_days'
    $screenshotAction = New-ScheduledTaskAction -Execute $pythonExe -Argument ('"{0}" --job screenshot-summary --config "{1}"' -f $runnerPath, $configPath)
    $screenshotTrigger = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek $screenshotDaysParsed -At $screenshotAt
    Register-ScheduledTask -TaskName $screenshotTaskName -Action $screenshotAction -Trigger $screenshotTrigger -Settings $settings -Principal $principal -Force | Out-Null
    Write-Host "Registered: $screenshotTaskName ($screenshotDays at $screenshotTime)"
}
elseif (Get-ScheduledTask -TaskName $screenshotTaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $screenshotTaskName -Confirm:$false
    Write-Host "Removed disabled task: $screenshotTaskName"
}
