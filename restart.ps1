[CmdletBinding()]
param([switch]$BackendOnly)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scripts\services.ps1')
Invoke-ProjectServices -Action Restart -BackendOnly:$BackendOnly
