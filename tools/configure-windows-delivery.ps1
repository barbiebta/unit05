$ErrorActionPreference = "Stop"

$statusPath = Join-Path $PSScriptRoot "windows-delivery-status.txt"

try {
    "STARTED $(Get-Date -Format o)" | Set-Content -LiteralPath $statusPath

    $capability = Get-WindowsCapability -Online -Name "OpenSSH.Server~~~~0.0.1.0"
    if ($capability.State -ne "Installed") {
        Add-WindowsCapability -Online -Name "OpenSSH.Server~~~~0.0.1.0" | Out-Null
    }

    Set-Service -Name sshd -StartupType Automatic
    Start-Service -Name sshd

    Get-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -ErrorAction SilentlyContinue |
        Disable-NetFirewallRule
    $unit05Rule = Get-NetFirewallRule -Name "Unit05-SFTP-Tailscale" -ErrorAction SilentlyContinue
    if ($unit05Rule) {
        $unit05Rule | Set-NetFirewallRule -Enabled True -Direction Inbound -Action Allow -Profile Any
        $unit05Rule | Get-NetFirewallPortFilter |
            Set-NetFirewallPortFilter -Protocol TCP -LocalPort 22
        $unit05Rule | Get-NetFirewallAddressFilter |
            Set-NetFirewallAddressFilter -RemoteAddress "100.64.0.0/10"
    }
    else {
        New-NetFirewallRule `
            -Name "Unit05-SFTP-Tailscale" `
            -DisplayName "Unit05 SFTP from Tailscale" `
            -Enabled True `
            -Direction Inbound `
            -Protocol TCP `
            -LocalPort 22 `
            -Action Allow `
            -RemoteAddress "100.64.0.0/10" `
            -Profile Any | Out-Null
    }

    "COMPLETE $(Get-Date -Format o)" | Set-Content -LiteralPath $statusPath
}
catch {
    "FAILED $(Get-Date -Format o)`r`n$($_ | Out-String)" |
        Set-Content -LiteralPath $statusPath
    throw
}
