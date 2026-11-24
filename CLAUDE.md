# CLAUDE.md – notatki dla kolejnych sesji Claude Code

Ten plik jest czytany automatycznie przez Claude Code przy pracy w tym repozytorium.
Zawiera kontekst, którego nie widać w samym kodzie: decyzje, pułapki środowiska i stan prac.
Szczegóły techniczne użytkowe są w `README.md`, protokół w `PROTOCOL.md`.

## Projekt w skrócie

16-kanałowy **cyfrowy** generator sekwencji testowych na płytce **MAX 10 10M08 Evaluation Kit**
(10M08SAE144C8G, 50 MHz). PC (Python) → RS-232 (USB-UART 3,3 V) → **Nios V/c** w FPGA → 16 bloków VHDL
`seq_channel` (1 blok = 1 kanał). Dodatkowe wyjścia BASE_CLK i SYNC wyrównane co do taktu z kanałami.

## Komunikacja z użytkownikiem

- Użytkownik pisze **po polsku** – odpowiadaj po polsku.
- **Aplikacja PC wyłącznie po angielsku** (UI, komunikaty, CLI, docstringi w `pc/`).
- Dokumentacja (`README.md`, `PROTOCOL.md`) po polsku. Komentarze w VHDL/C po polsku bez znaków
  diakrytycznych (ASCII) – zachowaj ten styl.

## Kluczowe decyzje (i dlaczego)

| Decyzja | Powód |
|---|---|
| **Nios V/c**, nie Nios II | Quartus 25.1 nie ma już rdzenia Nios II Gen2 w Platform Designerze. Nios V/m też działa (`build_fpga.ps1 -Cpu niosv_m`), ale zajmuje ~96 % LE – V/c ~56 %. |
| Firmware **bare-metal** (własne `crt0.S`, `link.ld`), bez HAL/BSP | Mieści się w 16 KB RAM (~2,6 KB kodu), brak zależności od narzędzi BSP. V/c nie ma CSR/przerwań → crt0 nie używa CSR, firmware na pollingu. |
| Źródła FPGA w **VHDL** | Wymóg użytkownika (pierwotnie było Verilog). |
| Wyjścia **tylko cyfrowe 0/1** | Wymóg użytkownika; wcześniej był sigma-delta 8-bit – usunięty. Pamięć kanału 1024 × 1 bit. |
| Krok liczony od **wspólnej podstawy 1 µs** (rejestr PERIOD = µs − 1, 20 bit) | Krok 1 µs … 1,048 s. Aplikacja dopuszcza 1 µs … 1 s (pierwotne wymaganie 1 ms … 1 s, rozszerzone na prośbę). |
| START restartuje podstawę 1 µs, BASE_CLK i SYNC | Pierwszy krok trwa dokładnie zadany czas, zbocza BASE_CLK/SYNC = zbocza kanałów. |
| BASE_CLK nadaje **tylko gdy pracuje kanał**; przy START wyjścia BASE_CLK/SYNC schodzą na 1 takt do 0 | Błąd wykryty symulacją: gdy BASE_CLK był w stanie 1 w chwili START, nie było zbocza narastającego. |
| Rejestry PERIOD/LAST/CFG **tylko do zapisu**, firmware trzyma kopie (`shadow`) | Oszczędność ~200+ LE na multiplekserze odczytu. |
| `FAST_OUTPUT_REGISTER` na wszystkich wyjściach kanałów/BASE_CLK/SYNC | Minimalny rozrzut zboczy między pinami; każde wyjście musi pochodzić wprost z rejestru (nie psuj tego). |
| Protokół binarny `A5 5A | CMD | LEN | payload | CRC16-CCITT-FALSE` | Odporny na śmieci na linii; opis w `PROTOCOL.md`. Wersja firmware 2.1. |

## Środowisko (ten komputer użytkownika)

- Quartus Prime Lite **25.1std**: `C:\altera_lite\25.1std` (Questa FSE w `questa_fse`).
  (2026-11-21/22 instalacje się zmieniały: chwilowo był tylko 24.1std w `C:\intelFPGA_lite`, potem odinstalowany.
  Zanim zbudujesz, sprawdź, czy `quartus_sh.exe` istnieje; inną instalację podaj przez `-Quartus`.)
- KiCad 10 (`C:\Program Files\KiCad\10.0\bin\kicad-cli.exe` – eksport netlisty do weryfikacji). Projekt własnej
  płytki `pcb/seqgen16`: arkusze FPGA, USB (FT232RNL, USB-C; od 2026-11-24 cała płytka za FB1 = TDK MPZ2012S601AT000
  0805 2 A: strona złącza = sieć `VBUS_USB` (TVS D24 SMF5.0A, symbol Diode:SM6T6V8A, pin 1 = katoda, D_SMF),
  za koralikiem `VBUS_FT` (FT232 C1/C2, ~4,8 µF – limit USB 10 µF),
  dalej klucz U10 TPS22917DBV (ON = VIN, CT C45 2,2n do VIN → narastanie ~5 ms, inrush ~55 mA, QOD NC) → `VBUS`
  dla reszty płytki (~52 µF: C15 47u, C16, VCCB TXU)), Power (**U9 = TI TPSM828302** od 2026-11-23, zamiast
  EP5388QI: moduł buck 2 A z cewką, wariant TPSM828302ARDSR = QFN-FCMOD-9 3×3 mm RDS0009A – jedyny aktywny;
  karta SLVSGM1D; piny 1 VIN, 2 GND, 3 MODE, 4 FB, 5 VOS, 6 VOUT, 7 PG, 8 SW, 9 EN; VFB 0,5 V; dzielnik
  R35 1,43M / R36 255k → 3,304 V (wartości policzone, nie z karty; R36 musi być 200k–600k); MODE = GND (auto
  PSM/PWM); EN z VBUS przez R23/C17; PG i SW niepodłączone; CIN C16 4,7u, COUT C18 47u ≥ 12 µF efektywnie.
  Footprint `seqgen16:TI_RDS0009A_QFN-FCMOD-9_3x3mm` (biblioteka projektu `seqgen16.pretty` + `fp-lib-table`,
  2026-11-23): współrzędne padów wyciągnięte z wektorów rysunku TI 4228326/D (str. 38 karty, skala 25X), pady 6/8
  wg otworu maski (SMD), pozostałe NSMD z maską +0,05 mm, pasta 6/8 −12 %. Symbol w `seqgen16.kicad_sym`.),
  Outputs (TXU0104/TXU0102 na 5 V, 33R + TSD05C, złącza J3 2x16, J4/J5 2x2), Config (oscylator 50 MHz, LED 132/134,
  RESET_N 121, zworki nCONFIG/BOOT_SEL, JTAG 2x5). U1 = **10M08SAE144** (decyzja użytkownika 2026-11-23; zgodne z
  `DEVICE` w `.qsf`). KiCad nie ma symbolu SA – jest w bibliotece projektu `seqgen16.kicad_sym` (kopia SC, piny
  3/4/5 = ANAIN1/REFGND/ADC_VREF; ADC nieużywany: ANAIN1, REFGND → GND, ADC_VREF NC).
  **Piny `.qsf` = etykiety globalne na U1 w `fpga.kicad_sch`** (TXD 70, RXD 69, CH0..15, SYNC 28, BASE_CLK 29) –
  zmieniaj oba miejsca razem. Przypisanie dla płytki ewaluacyjnej: `git show 22c6dcf:quartus/seqgen16.qsf`.
  Arkusze były generowane skryptami Pythona (poza repo) – same etykiety na końcach pinów, bez przewodów.
  **Styl schematów (wymóg użytkownika 2026-11-23): bliskie elementy łączyć przewodami, etykiet używać oszczędnie**
  (etykieta globalna tam, gdzie sieć schodzi na inny arkusz; lokalna tylko do odległego bloku na tym samym arkuszu).
  **Wszystkie opisy na schematach i PCB (notatki, tytuły arkuszy, teksty na płytce) po angielsku** (wymóg 2026-11-23).
  PCB: `pcb/tools/make_board.py` (80x80 mm, 4 warstwy, 4x M3, wyśrodkowana na A4; wyjścia J3/J4/J5 prawa krawędź,
  USB-C lewa; strefy rozmieszczenia na Dwgs.User). Działa tylko na płytce bez innych footprintów.
  **Wzorzec układu = arkusz Config (poprawiony ręcznie przez użytkownika):** format A4, notatki u góry, bloki
  funkcjonalne w rzędach, elementy 2-pinowe pionowo, +3V3 symbolem u góry, GND symbolem na dole (osobny symbol przy
  każdym elemencie), krótkie przewody, opisy Ref/Value po prawej. FPGA i Config układał użytkownik – nie ruszać.
  USB, Power, Outputs są rysowane od nowa skryptami `pcb/tools/layouts/layout_*.py` (biblioteka `pcb/tools/sch_build.py`:
  przesuwa części, kasuje wszystkie przewody/etykiety/symbole +3V3/GND i rysuje je według skryptu; dzieli przewody
  w punktach połączeń i dodaje kropki). Uwaga: KiCad odwraca justify pól dla części obróconych o 90/270 – sch_build
  to uwzględnia. `sch_dump.py` wypisuje piny części z sieciami. Skrypty uruchamiać na kopii, potem net_compare.
  Narzędzia w `pcb/tools/`: `sch_labels_to_wires.py` (bliskie punkty tej samej sieci → przewody, router A* po siatce
  1,27 mm, zostawia jedną etykietę/symbol zasilania na grupę, obraca etykiety leżące na przewodach),
  `relayout_outputs.py` (arkusz Outputs: kanał w rzędzie bufor → R → węzeł z etykietą → TVS → szyna GND),
  `net_compare.py` (porównanie netlist po zbiorach pinów). Procedura: netlista przed (`kicad-cli sch export netlist
  --format kicadsexpr`), narzędzie, netlista po, `net_compare.py` musi dać 0 różnic, ERC bez nowych błędów.
  Pozostałe niedoskonałości z generatora: nachodzące teksty pól (np. GND przy JP1/JP2, +3V3 przy OE buforów),
  rozstrzelone rozmieszczenie na Power (sieć EN). ERC: 69 × pin_not_connected (nieużywane I/O U1) – oczekiwane.
  RESET_N (pin 121) obsłużony w FPGA od 2026-11-23 (`reset_ctrl.vhd`). SC (Compact) odrzucony:
  prawdopodobnie bez inicjalizacji RAM z flash. W `.gitignore`: backupy, `.lck` (zawiera nazwę komputera), `.kicad_prl`.
- Toolchain RISC-V (RiscFree): `C:\altera_lite\25.1std\riscfree\toolchain\riscv32-unknown-elf\bin` – `fw/build.ps1` go znajduje.
- Licencja Questa: plik `*License*.dat` w `C:\altera_lite` (`sim/run_sim.ps1` szuka go sam, sprawdza że plik
  zawiera linie INCREMENT/FEATURE). Zmienna użytkownika `SALT_LICENSE_SERVER` mogła wskazywać stary pusty plik.
  **Nie wpisuj numeru licencji ani ścieżek z nazwą użytkownika do plików w repo** (repo jest publiczne).
- Python 3.13 + pyserial; brak PIL. Git for Windows (jego `ssh`, `ssh-keyscan`, `pdftotext` w `C:\Program Files\Git\...`).
- Programator: USB-Blaster na JTAG J10 (`quartus_pgm -c "USB-Blaster [USB-0]" -m jtag -o "p;output_files\seqgen16.sof"`).
- Schemat płytki (źródło pinów J8): `Altera_10M08S_E144_eval_schematic_REV_1_0.pdf` w dokumentach użytkownika
  (`Documents\AlteraProj\max10_10m08sae144_fpga_22.1std\`), arkusz 6.
- GitHub: `https://github.com/drxcom13/seqgen16` (push przez HTTPS, logowanie przez Git Credential Manager w
  przeglądarce – w nieinteraktywnej powłoce `git push` może wymagać uruchomienia w terminalu użytkownika).
  Klucz SSH użytkownika nie jest dodany do GitHuba.

## Pułapki (sprawdzone w praktyce)

- **Quartus: limit 260 znaków ścieżki** – generowane pliki `nios_sys/synthesis/submodules/...` są długie.
  Projekt trzymaj w krótkiej ścieżce. Nie generuj w katalogach typu AppData/Temp z długimi nazwami.
- **Quartus w folderze OneDrive** (`...\OneDrive\Projekty pc\seqgen16`): `quartus_sh --flow compile` kończy się
  `Error (297009): Can't open project -- you do not have permission to write ... database directory`, choć
  qsys-generate działa. Obejście (2026-11-22): skopiuj `quartus/` (z wygenerowanym `nios_sys/`) do krótkiej ścieżki
  poza OneDrive, np. `C:\PROJ\sg16_build\quartus`, tam `quartus_sh --flow compile seqgen16`, potem usuń kopię.
- **PowerShell 5.1**: `$ErrorActionPreference = "Stop"` + narzędzia Quartusa piszące info na stderr = fałszywy błąd.
  Skrypty używają `Continue` i sprawdzają `$LASTEXITCODE`.
- PowerShell: przecinek w argumencie (`-Wl,--gc-sections`) dzieli go na tablicę – trzeba cytować.
  `@(...)` przy wyniku z jednym elementem, inaczej `[0]` zwraca pierwszy znak stringa.
- PowerShell: polskie cudzysłowy „ ” są traktowane jak cudzysłowy – edytując pliki z nimi używaj narzędzia Edit,
  nie `.Replace()` w skrypcie PowerShell.
- `qsys-script` ma stary Tcl: zamiast `$a eq "b"` używaj `[string equal $a b]`.
- MAX 10, 3.3-V LVCMOS: `CURRENT_STRENGTH_NEW 8MA` nie jest dozwolone (Fitter error).
- Questa w testbenchu: sprawdzanie „ten sam moment” przez `'last_event = 0 ns` daje fałszywe błędy (delty z
  przypisań portów). Testbench zapisuje czasy zboczy (`t_ch5`, `t_bclk`, `t_sync`) i porównuje czasy symulacji.
- Tkinter: wywołania UI z wątku roboczego idą przez kolejkę (`App._ui` / `_pump`) – nie wołaj Tk z wątków.
- `App.load_project()` ustawia `cur = -1`, żeby pola starego kanału nie nadpisały nowego projektu.

## Weryfikacja – jak uruchomić

```powershell
cd pc ; python -m unittest test_seqgen test_firmware -v   # 37 testów; test_firmware wymaga fw/build/fw.bin
.\sim\run_sim.ps1                                          # Questa, oczekiwane "TEST PASSED"
.\fw\build.ps1 [-UpdateSof]                                # firmware -> quartus\onchip_ram.hex (+ obraz FPGA)
.\quartus\build_fpga.ps1                                   # Platform Designer + pełna kompilacja (~3 min)
```

- `pc/emulator.py` – model zachowania płytki w Pythonie (port „EMU” w GUI/CLI).
- `pc/fw_sim.py` – symulator RV32I wykonujący **prawdziwy** `fw/build/fw.bin` z modelami UART i rejestrów
  `seq_gen16`. `test_firmware.py` puszcza te same scenariusze co na emulatorze. Przy zmianie rejestrów/protokołu
  aktualizuj **wszystkie trzy**: VHDL, `fw/main.c` + `fw/hw.h`, `emulator.py` + `fw_sim.py` (+ `PROTOCOL.md`).
- Testy GUI były robione skryptami spoza repo (sterowanie `App` + zrzut ekranu przez System.Drawing). Zrzuty do
  README: `docs/gui_main.png`, `docs/gui_editor.png`.

## Stan na koniec ostatniej sesji

Zrobione i zweryfikowane: kompilacja FPGA (56 % LE, timing +6,2 ns), firmware 2.1, 37 testów, symulacja VHDL
TEST PASSED, czysty klon z GitHuba buduje się i przechodzi testy, README ze schematem blokowym i zrzutami.

**Otwarte / do zrobienia:**
0. **2026-11-22: do flash płytki ewaluacyjnej wgrany `.pof` z pinoutem WŁASNEJ płytki** (commit 20b6f6f) –
   UART na Arduino D12/D13 (piny 69/70, który jest który – sprawdzić), CH0–15/SYNC/BASE_CLK na J8. Programowanie
   i weryfikacja OK. **`ping` przez COM12 (konwerter CH343) działa** – fw 2.1, 16 kanałów, start z flash OK.
   Upload `examples/demo_16ch.json` OK. **Użytkownik potwierdził: aplikacja działa z płytką.**
   pyserial doinstalowany (Python 3.13 ze Sklepu MS).
1. ~~Test na sprzęcie~~ – zrobiony 2026-11-22 (pkt 0). Poniżej pierwotny opis, dla historii.
   Płytka była raz zaprogramowana (.sof, przed poprawką BASE_CLK).
   Brakowało konwertera USB-UART (na PC tylko porty Bluetooth COM7/COM8). Kolejność: wgrać aktualny `.sof`,
   podłączyć USB-UART (TX→D0, RX→D1, GND), `seqgen_cli.py ports`, `ping`, upload `examples/demo_16ch.json`,
   start, sprawdzić oscyloskopem; potem ewentualnie `.pof` do flash (trwałe – zapytaj użytkownika).
2. ~~J8 pin 7 / 9~~ – nieaktualne: piny przeniesione pod własną płytkę (2026-11-22). Test na płytce ewaluacyjnej
   wymaga przywrócenia starego przypisania z commita 22c6dcf.
3. Pin zegara 27 i LED-y 132–141 – z user guide / projektu społeczności; potwierdzi pierwsze uruchomienie (D1 miga).
4. Możliwe rozszerzenia omawiane z użytkownikiem: krok poniżej 1 µs (licznik w taktach 50 MHz, 26-bit, ewentualnie
   PLL) – nie zrobione; opis repozytorium na GitHubie ma literówki („sequance”, „base on”) – ustawia go użytkownik.
