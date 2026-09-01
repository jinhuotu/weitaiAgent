# Copy local Word CJK fonts into the OnlyOffice bind-mount directory.
$ErrorActionPreference = "Stop"
$src = Join-Path $env:WINDIR "Fonts"
$dst = Join-Path $PSScriptRoot "onlyoffice-fonts"
New-Item -ItemType Directory -Force -Path $dst | Out-Null

$want = @(
    "simsun.ttc", "simsunb.ttf", "simhei.ttf", "simkai.ttf", "simfang.ttf",
    "msyh.ttc", "msyhbd.ttc", "msyhl.ttc", "msyh.ttf",
    "simli.ttf", "simyou.ttf",
    "times.ttf", "timesbd.ttf", "arial.ttf", "arialbd.ttf"
)

$copied = 0
Get-ChildItem -Path $src -File | Where-Object {
    $want -contains $_.Name.ToLowerInvariant()
} | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $dst $_.Name) -Force
    $copied += 1
    Write-Host "copied $($_.Name)"
}

if ($copied -eq 0) {
    Write-Warning "No SimSun/YaHei fonts found in $src"
    exit 1
}
Write-Host "done: $copied files -> $dst"
Write-Host "next: docker compose -f infra/docker-compose.yml up -d onlyoffice"
