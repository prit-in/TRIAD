# Windows equivalent of fetch_toolchains.sh. Run once before the first image build:
#   pwsh -File scripts/fetch_toolchains.ps1      (or Windows PowerShell)
[CmdletBinding()]
param(
    [string]$SparkVersion = "4.1.3"
)

$ErrorActionPreference = "Stop"
$dist = "spark-$SparkVersion-bin-hadoop3"
$root = Split-Path -Parent $PSScriptRoot
$out  = Join-Path $root ".triad\toolchains"
$mirrors = @(
    "https://dlcdn.apache.org/spark",
    "https://archive.apache.org/dist/spark"
)

New-Item -ItemType Directory -Force -Path $out | Out-Null

function Get-Artifact {
    param([string]$RelPath, [string]$Destination)
    foreach ($base in $mirrors) {
        $url = "$base/$RelPath"
        Write-Host "fetching $url"
        & curl.exe -fSL --retry 3 --retry-delay 2 -C - -o $Destination $url
        if ($LASTEXITCODE -eq 0) { return }
        Write-Warning "  mirror failed, trying next"
    }
    throw "could not fetch $RelPath from any mirror"
}

$tgz = Join-Path $out "$dist.tgz"
$sum = Join-Path $out "$dist.tgz.sha512"

if (-not (Test-Path $tgz) -or (Get-Item $tgz).Length -eq 0) {
    Get-Artifact -RelPath "spark-$SparkVersion/$dist.tgz" -Destination $tgz
}
Get-Artifact -RelPath "spark-$SparkVersion/$dist.tgz.sha512" -Destination $sum

$expected = ((Get-Content $sum -Raw) -replace '\s', '') -replace 'spark.*$', ''
$expected = $expected.ToLower().Substring(0, 128)
$actual = (Get-FileHash $tgz -Algorithm SHA512).Hash.ToLower()

if ($actual -ne $expected) {
    Write-Error "SHA512 mismatch for $dist.tgz`n  expected $expected`n  actual   $actual"
    exit 1
}
"OK $dist.tgz verified ({0:N0} MB)" -f ((Get-Item $tgz).Length / 1MB)
