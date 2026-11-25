# Pelna budowa FPGA: system Platform Designer -> HDL -> kompilacja Quartus
#   .\build_fpga.ps1              (Nios V/c, domyslnie)
#   .\build_fpga.ps1 -Cpu niosv_m (z debugerem JTAG, ~96 % LE)
#   .\build_fpga.ps1 -Program     (dodatkowo wgraj .sof przez USB-Blaster)
# UWAGA: Quartus wymaga sciezek < 260 znakow - trzymaj projekt np. w C:\proj\seqgen16
param(
    [ValidateSet("niosv_c", "niosv_m")] [string]$Cpu = "niosv_c",
    [switch]$Program,
    [string]$Quartus = "C:\altera_lite\25.1std"
)
$ErrorActionPreference = "Continue"   # narzedzia Quartus pisza info na stderr; bledy sprawdzamy przez $LASTEXITCODE
Set-Location $PSScriptRoot
$qb = "$Quartus\quartus"

if ((Get-Location).Path.Length -gt 120) {
    Write-Warning "Sciezka projektu jest dluga ($((Get-Location).Path.Length) zn.) - generowane pliki moga przekroczyc limit 260 znakow."
}

if (-not (Test-Path onchip_ram.hex)) {
    Write-Warning "Brak onchip_ram.hex - zbuduj firmware (..\fw\build.ps1). Tworze pusty plik."
    python ..\fw\mkhex.py NUL onchip_ram.hex 12288 2>$null
}

& "$qb\sopc_builder\bin\qsys-script.exe" --script=create_system.tcl --cmd="set CPU $Cpu"
if ($LASTEXITCODE) { throw "qsys-script nieudany" }
& "$qb\sopc_builder\bin\qsys-generate.exe" nios_sys.qsys --synthesis=VHDL --part=10M08SAE144C8G
if ($LASTEXITCODE) { throw "qsys-generate nieudany" }
& "$qb\bin64\quartus_sh.exe" --flow compile seqgen16
if ($LASTEXITCODE) { throw "kompilacja Quartus nieudana" }
Get-Content output_files\seqgen16.fit.summary

if ($Program) {
    & "$qb\bin64\quartus_pgm.exe" -m jtag -o "p;output_files\seqgen16.sof"
}
