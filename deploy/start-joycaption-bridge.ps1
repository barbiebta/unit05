[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Unit05Host,
    [int]$SshPort = 22,
    [Parameter(Mandatory = $true)][string]$IdentityFile,
    [int]$LocalJoyPort = 8000,
    [int]$RemoteJoyPort = 18000,
    [switch]$Background
)

$ErrorActionPreference = "Stop"
$ScriptPath = $MyInvocation.MyCommand.Path

if ($Background) {
    $arguments = @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $ScriptPath,
        "-Unit05Host", $Unit05Host, "-SshPort", $SshPort,
        "-IdentityFile", $IdentityFile, "-LocalJoyPort", $LocalJoyPort,
        "-RemoteJoyPort", $RemoteJoyPort
    )
    Start-Process -FilePath "powershell.exe" -ArgumentList $arguments -WindowStyle Hidden
    return
}

Invoke-RestMethod -Uri "http://127.0.0.1:$LocalJoyPort/healthz" -TimeoutSec 10 | Out-Null

while ($true) {
    & ssh.exe -N -T `
        -o ExitOnForwardFailure=yes `
        -o ServerAliveInterval=30 `
        -o ServerAliveCountMax=3 `
        -R "127.0.0.1:${RemoteJoyPort}:127.0.0.1:${LocalJoyPort}" `
        -p $SshPort -i $IdentityFile $Unit05Host
    Start-Sleep -Seconds 5
}
