# Buduje firmware Nios V (bare-metal) i wpisuje go do obrazu FPGA.
#   .\build.ps1                 -> build\fw.elf + ..\quartus\onchip_ram.hex
#   .\build.ps1 -UpdateSof      -> dodatkowo aktualizuje RAM w .sof/.pof bez ponownej kompilacji
# Wymaga toolchaina RISC-V z "Ashling RiscFree IDE for Altera FPGAs" (darmowy).
param(
    [switch]$UpdateSof,
    [string]$Quartus = "C:\altera_lite\25.1std"
)
$ErrorActionPreference = "Continue"   # narzedzia Quartus pisza info na stderr; bledy sprawdzamy przez $LASTEXITCODE
Set-Location $PSScriptRoot

$candidates = @(@(
    $env:RISCV_TOOLCHAIN,
    "$Quartus\riscfree\toolchain\riscv32-unknown-elf\bin",
    "$Quartus\riscfree\toolchain\bin"
) | Where-Object { $_ -and (Test-Path "$_\riscv32-unknown-elf-gcc.exe") })
if (-not $candidates) {
    $g = Get-Command riscv32-unknown-elf-gcc -ErrorAction SilentlyContinue
    if (-not $g) { throw "Nie znaleziono riscv32-unknown-elf-gcc. Zainstaluj RiscFree lub ustaw RISCV_TOOLCHAIN." }
    $tc = Split-Path $g.Source
} else { $tc = $candidates[0] }

$gcc = "$tc\riscv32-unknown-elf-gcc.exe"
$objcopy = "$tc\riscv32-unknown-elf-objcopy.exe"
$size = "$tc\riscv32-unknown-elf-size.exe"

New-Item -ItemType Directory -Force build | Out-Null
& $gcc -march=rv32i_zicsr -mabi=ilp32 -Os -std=c99 -Wall -Wextra `
    -ffreestanding -fno-builtin -nostdlib -nostartfiles -ffunction-sections '-Wl,--gc-sections,--no-warn-rwx-segments,-Map=build\fw.map' `
    -T link.ld crt0.S main.c -lgcc -o build\fw.elf
if ($LASTEXITCODE) { throw "Kompilacja nieudana" }
& $size build\fw.elf
& $objcopy -O binary build\fw.elf build\fw.bin
python mkhex.py build\fw.bin ..\quartus\onchip_ram.hex 12288

if ($UpdateSof) {
    Push-Location ..\quartus
    & "$Quartus\quartus\bin64\quartus_cdb.exe" seqgen16 -c seqgen16 --update_mif
    & "$Quartus\quartus\bin64\quartus_asm.exe" seqgen16 -c seqgen16
    Pop-Location
}
