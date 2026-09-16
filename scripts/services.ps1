# Shared Windows service control. Entry points live in the project root.
$script:ProjectRoot = Split-Path -Parent $PSScriptRoot
$script:RuntimeDir = Join-Path $script:ProjectRoot '.runtime'
$script:StatePath = Join-Path $script:RuntimeDir 'services.json'
$script:Settings = Import-PowerShellDataFile (Join-Path $script:ProjectRoot 'services.local.psd1')
$script:Containers = @('industry_postgres','industry_redis','industry_etcd','industry_minio','industry_milvus')

function Read-ServiceState {
    $state = @{ backend = $null; frontend = $null }
    if (Test-Path -LiteralPath $script:StatePath) {
        $saved = Get-Content -LiteralPath $script:StatePath -Raw | ConvertFrom-Json
        $state.backend = $saved.backend
        $state.frontend = $saved.frontend
    }
    return $state
}

function Save-ServiceState($State) {
    $State | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $script:StatePath -Encoding UTF8
}

function Get-OwnedProcess($Record) {
    if ($null -eq $Record) { return $null }
    $candidate = Get-CimInstance Win32_Process -Filter "ProcessId = $($Record.pid)" -ErrorAction SilentlyContinue
    if ($candidate -and $candidate.CommandLine -eq $Record.command -and
        $candidate.CreationDate.ToUniversalTime().Ticks.ToString() -eq $Record.created) {
        return $candidate
    }
    return $null
}

function Stop-App($State, [string]$Kind) {
    $proc = Get-OwnedProcess $State[$Kind]
    if ($proc) {
        & taskkill.exe /PID $proc.ProcessId /T /F | Out-Null
        if ($LASTEXITCODE -ne 0 -and (Get-OwnedProcess $State[$Kind])) {
            throw "Could not stop $Kind (PID $($proc.ProcessId))."
        }
        Write-Host "Stopped $Kind."
    }
    $State[$Kind] = $null
    Save-ServiceState $State
}

function Assert-FreePort([int]$Port) {
    $listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    if ($listeners.Count) {
        $owners = ($listeners.OwningProcess | Sort-Object -Unique) -join ', '
        throw "Port $Port is occupied by PID(s) $owners. No unrelated process was stopped."
    }
}

function Assert-Tools {
    foreach ($path in @($script:Settings.CondaActivate, (Join-Path $script:Settings.CondaEnvironment 'python.exe'),
        (Join-Path $script:ProjectRoot 'backend\.env'), (Join-Path $script:ProjectRoot 'frontend\node_modules\vite\bin\vite.js'))) {
        if (-not (Test-Path -LiteralPath $path)) { throw "Missing prerequisite: $path. No dependencies were installed." }
    }
    Get-Command node.exe,docker.exe -ErrorAction Stop | Out-Null
    & docker info --format '{{.ServerVersion}}' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Start Docker Desktop first.' }
    & docker image inspect industry-research-sandbox:local --format '{{.Id}}' 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Research sandbox image is missing. See README; no image was downloaded.' }
}

function Start-Dependencies {
    $existing = @(& docker ps -a --format '{{.Names}}')
    if ($LASTEXITCODE -ne 0) { throw 'Cannot read Docker containers.' }
    $missing = @($script:Containers | Where-Object { $_ -notin $existing })
    if ($missing.Count) {
        & docker compose -p $script:Settings.ComposeProject -f (Join-Path $script:ProjectRoot 'docker-compose.yml') up -d --pull never --no-build
    } else {
        # Keep the existing containers and volumes; avoid unnecessary recreation.
        & docker start @script:Containers | Out-Null
    }
    if ($LASTEXITCODE -ne 0) { throw 'Could not start dependencies. Check Docker logs and port conflicts.' }
    $deadline = (Get-Date).AddSeconds(150)
    do {
        $pending = @()
        foreach ($name in $script:Containers) {
            $health = & docker inspect --format '{{if .State.Running}}{{if .State.Health}}{{.State.Health.Status}}{{else}}running{{end}}{{else}}stopped{{end}}' $name
            if ($LASTEXITCODE -ne 0) { throw "Cannot inspect $name." }
            if ($health -notin @('healthy','running')) { $pending += "$name=$health" }
        }
        if (-not $pending.Count) { Write-Host 'All 6 dependencies are healthy.'; return }
        Write-Host ('Waiting: ' + ($pending -join ', '))
        Start-Sleep -Seconds 5
    } while ((Get-Date) -lt $deadline)
    throw 'Dependency health check timed out. Applications were not started.'
}

function Start-App($State, [string]$Kind) {
    if (Get-OwnedProcess $State[$Kind]) { Write-Host "$Kind is already running."; return }
    $port = if ($Kind -eq 'backend') { 8000 } else { 5183 }
    Assert-FreePort $port
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
    $outPath = Join-Path $script:RuntimeDir "$stamp-$Kind.out.log"
    $errPath = Join-Path $script:RuntimeDir "$stamp-$Kind.err.log"
    if ($Kind -eq 'backend') {
        $entry = Join-Path $script:ProjectRoot 'backend\app\app_main.py'
        $arguments = '/d /c call "{0}" "{1}" && python -X utf8 -u "{2}"' -f $script:Settings.CondaActivate,$script:Settings.CondaEnvironment,$entry
        $cmdPath = Join-Path ([Environment]::GetFolderPath('System')) 'cmd.exe'
        $proc = Start-Process -FilePath $cmdPath -ArgumentList $arguments -WorkingDirectory (Join-Path $script:ProjectRoot 'backend') -WindowStyle Hidden -RedirectStandardOutput $outPath -RedirectStandardError $errPath -PassThru
    } else {
        $entry = Join-Path $script:ProjectRoot 'frontend\node_modules\vite\bin\vite.js'
        $proc = Start-Process -FilePath (Get-Command node.exe).Source -ArgumentList ('"{0}" --host 0.0.0.0 --strictPort' -f $entry) -WorkingDirectory (Join-Path $script:ProjectRoot 'frontend') -WindowStyle Hidden -RedirectStandardOutput $outPath -RedirectStandardError $errPath -PassThru
    }
    $identity = Get-CimInstance Win32_Process -Filter "ProcessId = $($proc.Id)"
    if (-not $identity) { throw "$Kind exited during startup. See $errPath" }
    $State[$Kind] = @{ pid=$proc.Id; created=$identity.CreationDate.ToUniversalTime().Ticks.ToString(); command=$identity.CommandLine; stdout=$outPath; stderr=$errPath }
    Save-ServiceState $State
    Write-Host "Started $Kind (PID $($proc.Id))."
}

function Wait-Http([string]$Url) {
    $deadline = (Get-Date).AddSeconds(90)
    do {
        try {
            $request = [System.Net.HttpWebRequest]::Create($Url)
            $request.Proxy = $null
            $request.Timeout = 2000
            $response = $request.GetResponse()
            try { if ([int]$response.StatusCode -eq 200) { Write-Host "OK $Url"; return } }
            finally { $response.Close() }
        } catch { }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    throw "HTTP check failed: $Url. See $script:RuntimeDir"
}

function Stop-Dependencies {
    & docker info --format '{{.ServerVersion}}' 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Docker is unavailable; applications stopped but container status could not be verified.' }
    $running = @(& docker ps --format '{{.Names}}')
    $targets = @($script:Containers | Where-Object { $_ -in $running })
    if ($targets.Count) {
        & docker stop @targets | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Some dependency containers could not be stopped.' }
    }
    Write-Host 'Project dependencies stopped; data volumes retained.'
}

function Invoke-ProjectServices {
    param([ValidateSet('Start','Stop','Restart','SetModel')][string]$Action, [switch]$BackendOnly, [string]$ModelId)
    New-Item -ItemType Directory -Path $script:RuntimeDir -Force | Out-Null
    $lockPath = Join-Path $script:RuntimeDir 'control.lock'
    try { $controlLock = [System.IO.File]::Open($lockPath,'OpenOrCreate','ReadWrite','None') }
    catch { throw 'Another project start/stop operation is in progress.' }
    try {
        $state = Read-ServiceState
        if ($Action -eq 'SetModel') {
            $envPath = Join-Path $script:ProjectRoot 'backend\.env'
            $text = [System.IO.File]::ReadAllText($envPath)
            $pattern = '(?m)^OPENAI_MODEL=.*$'
            if ([regex]::IsMatch($text,$pattern)) { $text = [regex]::Replace($text,$pattern,"OPENAI_MODEL=$ModelId") }
            else { $text += "`r`nOPENAI_MODEL=$ModelId`r`n" }
            [System.IO.File]::WriteAllText($envPath,$text,(New-Object System.Text.UTF8Encoding($false)))
            Write-Host "Saved OPENAI_MODEL=$ModelId (API key unchanged)."
        }
        if ($Action -in @('Stop','Restart','SetModel')) {
            Stop-App $state 'backend'
            if (-not $BackendOnly) { Stop-App $state 'frontend' }
            if ($Action -eq 'Stop' -and -not $BackendOnly) { Stop-Dependencies }
        }
        if ($Action -in @('Start','Restart','SetModel')) {
            Assert-Tools
            if (-not (Get-OwnedProcess $state.backend)) { Assert-FreePort 8000 }
            if (-not $BackendOnly -and -not (Get-OwnedProcess $state.frontend)) { Assert-FreePort 5183 }
            Start-Dependencies
            Start-App $state 'backend'
            Wait-Http 'http://127.0.0.1:8000/hello'
            if (-not $BackendOnly) {
                Start-App $state 'frontend'
                Wait-Http 'http://127.0.0.1:5183/'
                Wait-Http 'http://127.0.0.1:5183/api/hello'
            }
            Write-Host 'Frontend: http://127.0.0.1:5183/'
        }
    } finally { $controlLock.Dispose() }
}
