param(
    [string]$Tag = "weitai-agent:offline",
    [string]$OutputDir = "release"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (docker info 2>$null)) {
    throw "Docker engine is unavailable. Start Docker Desktop and run this script again."
}

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
docker build --file Dockerfile.offline --tag $Tag .
docker save --output (Join-Path $OutputDir "weitai-agent.tar") $Tag
Copy-Item infra/docker-compose.offline.yml $OutputDir
Copy-Item .env.example (Join-Path $OutputDir ".env.production.example")

docker image inspect $Tag --format "image={{.Id}}" | Out-File (Join-Path $OutputDir "IMAGE.txt") -Encoding utf8
Get-FileHash (Join-Path $OutputDir "weitai-agent.tar") -Algorithm SHA256 |
    Format-List | Out-File (Join-Path $OutputDir "SHA256.txt") -Encoding utf8

Write-Host "Offline API image exported to $OutputDir\weitai-agent.tar"
Write-Host "Copy .env.production.example to .env.production and fill production secrets before delivery."
