$ErrorActionPreference = "Stop"

$statusPath = Join-Path $PSScriptRoot "unit05-delivery-key-status.txt"
$authorizedKeysPath = Join-Path $env:ProgramData "ssh\administrators_authorized_keys"
$deliveryKey = 'restrict,command="internal-sftp" ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAII6Xk84BamUMMMq55jBxePF20yr36Tenw0Y7fcNr/1BU unit05-output'

try {
    "STARTED $(Get-Date -Format o)" | Set-Content -LiteralPath $statusPath

    New-Item -ItemType Directory -Path "D:\incomingfrom05" -Force | Out-Null
    New-Item -ItemType Directory -Path (Split-Path $authorizedKeysPath) -Force | Out-Null

    $existing = if (Test-Path -LiteralPath $authorizedKeysPath) {
        @(Get-Content -LiteralPath $authorizedKeysPath | Where-Object { $_ -notmatch 'unit05-output$' })
    }
    else {
        @()
    }
    @($existing + $deliveryKey) |
        Set-Content -LiteralPath $authorizedKeysPath -Encoding ascii

    & icacls.exe $authorizedKeysPath /inheritance:r | Out-Null
    & icacls.exe $authorizedKeysPath /grant:r '*S-1-5-32-544:F' '*S-1-5-18:F' | Out-Null

    $unit05Rule = Get-NetFirewallRule -Name "Unit05-SFTP-Tailscale" -ErrorAction Stop
    $unit05Rule | Set-NetFirewallRule -Enabled True -Direction Inbound -Action Allow -Profile Any
    $unit05Rule | Get-NetFirewallAddressFilter |
        Set-NetFirewallAddressFilter -RemoteAddress "100.64.0.0/10"

    Restart-Service -Name sshd
    "COMPLETE $(Get-Date -Format o)" | Set-Content -LiteralPath $statusPath
}
catch {
    "FAILED $(Get-Date -Format o)`r`n$($_ | Out-String)" |
        Set-Content -LiteralPath $statusPath
    throw
}
