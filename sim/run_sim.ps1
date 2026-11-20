# Uruchamia testbench VHDL w Questa (Altera Starter Edition).
#   .\run_sim.ps1                         licencja ze zmiennej SALT_LICENSE_SERVER,
#                                         a gdy jej brak - pierwszy plik *License*.dat w C:\altera_lite
#   .\run_sim.ps1 -License D:\lic\moja.dat
# Zmienne licencji ustawiane sa tylko dla tego procesu.
param([string]$License = "")
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot

# plik licencji musi zawierac co najmniej jedna linie INCREMENT/FEATURE (nie same komentarze)
function Test-LicenseFile([string]$p) {
    $p -and (Test-Path $p) -and (Select-String -Path $p -Pattern '^\s*(INCREMENT|FEATURE)\s' -Quiet)
}
if (-not $License -and (Test-LicenseFile $env:SALT_LICENSE_SERVER)) {
    $License = $env:SALT_LICENSE_SERVER
}
if (-not $License) {
    $found = Get-ChildItem C:\altera_lite -Filter "*License*.dat" -ErrorAction SilentlyContinue |
             Where-Object { Test-LicenseFile $_.FullName } | Select-Object -First 1
    if ($found) { $License = $found.FullName }
}
if (-not $License -or -not (Test-Path $License)) {
    throw "Nie znaleziono licencji Questa - podaj -License <plik.dat> lub ustaw SALT_LICENSE_SERVER"
}
$env:SALT_LICENSE_SERVER = $License
$env:LM_LICENSE_FILE = $License
$out = vsim -c -do run_sim.do 2>&1 | ForEach-Object { "$_" }
$out | Select-String "\*\* (Error|Warning)|TEST PASSED|TEST FAILED" | ForEach-Object { $_.Line }
if ($out -match "TEST PASSED") { exit 0 } else { exit 1 }
