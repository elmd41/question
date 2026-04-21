$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$backendDir = Join-Path $root "backend"
$frontendDir = Join-Path $root "frontend"
$modelTargetDir = Join-Path $frontendDir "public\models"
$modelTarget = Join-Path $modelTargetDir "panda-v2.glb"
$modelSource = Join-Path $root "panda-V2.glb"
$backendPort = 4801
$adminPassword = "dlsnjkjg"
$localEnvPath = Join-Path $backendDir ".env.local"

function Import-DotEnvFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    Get-Content -Path $Path | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#")) {
            return
        }

        $parts = $line -split "=", 2
        if ($parts.Count -ne 2) {
            return
        }

        $key = $parts[0].Trim()
        $value = $parts[1].Trim().Trim('"').Trim("'")
        Set-Item -Path ("Env:" + $key) -Value $value
    }
}

function Mask-Value {
    param([string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value)) {
        return "<empty>"
    }

    if ($Value.Length -le 6) {
        return ("*" * $Value.Length)
    }

    return $Value.Substring(0, 2) + "***" + $Value.Substring($Value.Length - 2, 2)
}

New-Item -ItemType Directory -Force -Path $modelTargetDir | Out-Null
if (Test-Path $modelSource) {
    Copy-Item -Path $modelSource -Destination $modelTarget -Force
}
elseif (-not (Test-Path $modelTarget)) {
    throw "Panda model file not found. Expected either $modelSource or $modelTarget"
}

if (Test-Path $localEnvPath) {
    Import-DotEnvFile -Path $localEnvPath
}

$env:UPSTREAM_MODE = "aliyun_split"
$env:UPSTREAM_RESOURCE_ID = if ($env:UPSTREAM_RESOURCE_ID) { $env:UPSTREAM_RESOURCE_ID } else { "volc.speech.dialog" }
$env:DEFAULT_AVATAR_URL = "/models/panda-v2.glb"
$env:DEFAULT_MODEL_FAMILY = "O2.0"
$env:ADMIN_PASSWORD = $adminPassword
$env:SESSION_SECRET = [guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N")
$env:APP_LOG_LEVEL = if ($env:APP_LOG_LEVEL) { $env:APP_LOG_LEVEL } else { "INFO" }

$requiredEnvVars = @(
    "UPSTREAM_APP_ID",
    "UPSTREAM_ACCESS_KEY",
    "UPSTREAM_APP_KEY"
)
$missingEnvVars = $requiredEnvVars | Where-Object {
    [string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($_))
}

if ($missingEnvVars.Count -gt 0) {
    throw "Missing required realtime env vars in ${localEnvPath}: $($missingEnvVars -join ', ')"
}

if (-not (Test-Path (Join-Path $backendDir ".venv\Scripts\python.exe"))) {
    Write-Host "Creating backend virtual environment..."
    Push-Location $backendDir
    try {
        python -m venv .venv
    }
    finally {
        Pop-Location
    }
}

Push-Location $backendDir
try {
    & .\.venv\Scripts\python -m pip install -r requirements.txt
    & .\.venv\Scripts\python .\bootstrap_defaults.py
}
finally {
    Pop-Location
}

if (-not (Test-Path (Join-Path $frontendDir "node_modules"))) {
    Push-Location $frontendDir
    try {
        npm install
    }
    finally {
        Pop-Location
    }
}

Push-Location $frontendDir
try {
    npm run build
}
finally {
    Pop-Location
}

Write-Host ""
Write-Host "Model copied to: $modelTarget"
Write-Host "Visitor page: http://127.0.0.1:$backendPort/"
Write-Host "Admin page:   http://127.0.0.1:$backendPort/admin/login"
Write-Host "Admin password: $adminPassword"
Write-Host "Runtime log:  $backendDir\data\runtime.log"
Write-Host ""
Write-Host "Realtime WS config in use:"
Write-Host "  APP ID       = $(Mask-Value $env:UPSTREAM_APP_ID)"
Write-Host "  Access Token = $(Mask-Value $env:UPSTREAM_ACCESS_KEY)"
Write-Host ""

Start-Process "http://127.0.0.1:$backendPort/"

Push-Location $backendDir
try {
    & .\.venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port $backendPort
}
finally {
    Pop-Location
}
