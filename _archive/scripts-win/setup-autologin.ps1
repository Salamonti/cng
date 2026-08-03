#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Enables Windows auto-login for NoteGenerator and locks the screen 30s after boot.
    This ensures Docker Desktop, openclaw, and all services start without manual login.
#>

$ErrorActionPreference = 'Stop'
$username = 'NoteGenerator'
$reg      = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'

Write-Host ""
Write-Host "=== Auto-Login Setup ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "This will configure Windows to log in automatically on boot," -ForegroundColor Yellow
Write-Host "then lock the screen after 30 seconds. Your password is required." -ForegroundColor Yellow
Write-Host ""

# Prompt for password securely (never shown on screen)
$cred     = Get-Credential -UserName $username -Message "Enter your Windows login password"
$password = $cred.GetNetworkCredential().Password

if ([string]::IsNullOrEmpty($password)) {
    Write-Host "ERROR: Password cannot be empty." -ForegroundColor Red
    exit 1
}

# ── Step 1: Set auto-login registry keys ─────────────────────────────────────
Write-Host "[1/3] Configuring auto-login in registry..." -ForegroundColor Cyan

Set-ItemProperty -Path $reg -Name 'AutoAdminLogon'   -Value '1'               -Type String
Set-ItemProperty -Path $reg -Name 'DefaultUserName'  -Value $username         -Type String
Set-ItemProperty -Path $reg -Name 'DefaultPassword'  -Value $password         -Type String
Set-ItemProperty -Path $reg -Name 'DefaultDomainName'-Value $env:COMPUTERNAME -Type String

# Remove ForceAutoLogon if set (can cause login loops)
Remove-ItemProperty -Path $reg -Name 'ForceAutoLogon' -ErrorAction SilentlyContinue

Write-Host "    OK: Auto-login enabled for $username" -ForegroundColor Green

# ── Step 2: Scheduled task to lock screen 30s after login ────────────────────
Write-Host "[2/3] Creating auto-lock task (locks screen 30s after login)..." -ForegroundColor Cyan

$action   = New-ScheduledTaskAction -Execute 'rundll32.exe' -Argument 'user32.dll,LockWorkStation'
$trigger  = New-ScheduledTaskTrigger -AtLogOn -User $username
$trigger.Delay = 'PT30S'
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId $username -LogonType Interactive -RunLevel Highest

Register-ScheduledTask `
    -TaskName  'AutoLockAfterBoot' `
    -Action    $action `
    -Trigger   $trigger `
    -Principal $principal `
    -Settings  $settings `
    -Force | Out-Null

Write-Host "    OK: Screen will lock 30s after each login" -ForegroundColor Green

# ── Step 3: Verify ────────────────────────────────────────────────────────────
Write-Host "[3/3] Verifying..." -ForegroundColor Cyan

$autoLogon = Get-ItemProperty $reg -Name 'AutoAdminLogon' | Select-Object -ExpandProperty 'AutoAdminLogon'
$autoUser  = Get-ItemProperty $reg -Name 'DefaultUserName' | Select-Object -ExpandProperty 'DefaultUserName'
$lockTask  = Get-ScheduledTask -TaskName 'AutoLockAfterBoot' -ErrorAction SilentlyContinue

Write-Host "    AutoAdminLogon : $autoLogon"
Write-Host "    DefaultUserName: $autoUser"
Write-Host "    AutoLockAfterBoot task: $(if ($lockTask) { 'exists' } else { 'MISSING' })"

Write-Host ""
Write-Host "=== Done! ===" -ForegroundColor Green
Write-Host ""
Write-Host "WHAT HAPPENS ON NEXT REBOOT:" -ForegroundColor Yellow
Write-Host "  1. Windows boots"
Write-Host "  2. Auto-logs in as $username (no interaction needed)"
Write-Host "  3. Docker Desktop, openclaw, Cloudflare all start normally"
Write-Host "  4. Screen locks automatically after 30 seconds"
Write-Host "  5. Services remain running behind the lock screen"
Write-Host ""
Write-Host "TO DISABLE AUTO-LOGIN LATER:" -ForegroundColor Yellow
Write-Host '  Set-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" -Name AutoAdminLogon -Value "0"'
Write-Host ""
