$found = $false
Get-CimInstance Win32_Process -Filter "Name='node.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -match 'openclaw' -and $_.CommandLine -match 'gateway' } |
    ForEach-Object {
        $found = $true
        Write-Host "Killing orphan gateway node PID $($_.ProcessId)"
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
if (-not $found) { Write-Host 'No orphan gateway node processes found.' }
Write-Host '--- port 18789 ---'
Get-NetTCPConnection -LocalPort 18789 -ErrorAction SilentlyContinue | Select-Object State, OwningProcess
