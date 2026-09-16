[CmdletBinding()]
param([Parameter(Mandatory=$true)][ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$')][string]$ModelId)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scripts\services.ps1')
Invoke-ProjectServices -Action SetModel -ModelId $ModelId -BackendOnly
