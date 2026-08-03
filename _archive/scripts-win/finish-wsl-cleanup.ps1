#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Final WSL teardown: remove leftover scheduled tasks and optionally disable WSL features.

.DESCRIPTION
    Ubuntu and docker-desktop distros are already unregistered.
    Run this once from an elevated PowerShell window to delete boot-time WSL tasks
    and optionally turn off the WSL Windows features (reboot required).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File C:\project-root\startup\finish-wsl-cleanup.ps1
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File C:\project-root\startup\finish-wsl-cleanup.ps1 -DisableWslFeatures
#>
param(
    [switch]$DisableWslFeatures
)

$ErrorActionPreference = 'Stop'

$tasks = @('Boot WSL Ubuntu', 'wsl')
foreach ($name in $tasks) {
    $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if ($task) {
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
        Write-Host "Deleted scheduled task: $name"
    } else {
        Write-Host "Scheduled task not found (already removed): $name"
    }
}

Write-Host ""
Write-Host "WSL distros:"
wsl -l -v

if ($DisableWslFeatures) {
    Write-Host ""
    Write-Host "Disabling WSL Windows features (reboot required)..."
    dism.exe /online /disable-feature /featurename:Microsoft-Windows-Subsystem-Linux /norestart
    dism.exe /online /disable-feature /featurename:VirtualMachinePlatform /norestart
    Write-Host "Features disabled. Restart the computer to finish."
} else {
    Write-Host ""
    Write-Host "WSL features left enabled (Docker Desktop can recreate docker-desktop distro later)."
    Write-Host "To disable entirely, re-run with -DisableWslFeatures"
}
