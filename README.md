# SeqGen16 – 16-kanałowy cyfrowy generator sekwencji (MAX 10 10M08 Eval Kit)

![Aplikacja PC – edycja przebiegów i podgląd wszystkich kanałów](docs/gui_main.png)

## Założenia projektu

1. **16 niezależnych kanałów** wyjściowych; każdy kanał to osobny blok w FPGA.
2. **Wyjścia cyfrowe 0/1** – na tym etapie bez wyjść analogowych. Na własnej płytce poziom wyjść (VCCB buforów TXU)
   ustawiany z PC: 1,8 / 2,5 / 3,3 / 4,0 / 5 V albo wyłączone (Hi-Z, stan po włączeniu zasilania).
3. **Sekwencja co najmniej 1024 zmian** na kanał; po końcu: powtórzenie N razy lub pętla.
4. **Rozdzielczość czasu (krok) od 1 ms do 1 s** – ostatecznie rozszerzona do **1 µs … 1 s**, ustawiana
   osobno dla każdego kanału.
5. **Wspólny start** – wybrane kanały ruszają w tym samym takcie zegara.
6. **Wyjścia synchronizujące:** zegar bazowy **BASE_CLK** zsynchronizowany z wyjściami oraz **SYNC**
   ze zboczem narastającym w chwili wyzwolenia sekwencji.
7. **Platforma:** płytka MAX 10 10M08 Evaluation Kit (10M08SAE144C8G).
8. **Architektura:** aplikacja PC (programator sekwencji) ↔ RS-232 ↔ procesor **Nios** w FPGA ↔ bloki
   generatorów (1 blok = 1 kanał).
9. **Źródła FPGA w VHDL.**
10. **Aplikacja PC po angielsku**, z możliwością **narysowania przebiegu każdego wyjścia** oraz
    importu i eksportu sekwencji do pliku.

## Architektura

![Schemat blokowy systemu SeqGen16](docs/schemat_blokowy.svg)

*Schemat blokowy: [SVG](docs/schemat_blokowy.svg) · [PNG](docs/schemat_blokowy.png)*

- **PC** – aplikacja (GUI / CLI) buduje sekwencje i wysyła je ramkami z CRC16 przez konwerter USB-UART.
- **Nios V/c** w FPGA odbiera ramki przez UART, zapisuje konfigurację i kroki do generatora przez magistralę Avalon-MM.
- **seq_gen16** – 16 identycznych bloków `seq_channel` (każdy z własną pamięcią M9K 1024 kroki × 1 bit,
  sekwencerem i rejestrem wyjściowym), wspólna podstawa czasu 1 µs oraz generator BASE_CLK / SYNC.
- **Wyjścia** – cyfrowe 0/1: CH0–CH15, BASE_CLK, SYNC (piny w sekcji *Podłączenie*). FPGA pracuje w 3,3 V LVCMOS;
  na własnej płytce bufory TXU0104/TXU0102 przenoszą to na poziom VCCB (komenda SET_VOUT, wszystkie wyjścia razem).
- **USB-Blaster** (JTAG) służy tylko do wgrania obrazu FPGA (.sof / .pof).

## Spełnienie wymagań

| Wymaganie | Realizacja |
|---|---|
| 16 kanałów, każdy = 1 blok w FPGA | 16 × `seq_channel.vhd` w `seq_gen16.vhd` |
| rozdzielczość kroku 1 ms … 1 s | krok = N µs, od **1 µs** do 1 s (sprzęt do 1,048 s), wspólna podstawa 1 µs, ustawiany niezależnie dla każdego kanału |
| długość ≥ 1024 zmian | 1 … 1024 kroków na kanał, + powtarzanie 1…65535 razy lub w pętli |
| programowanie z PC | GUI (Tkinter, po angielsku) z edytorem do rysowania przebiegu każdego wyjścia myszą, CLI, projekt w JSON |
| Nios + RS-232 | Nios V/c, UART 115200 8N1, protokół binarny z CRC16 (`PROTOCOL.md`) |
| synchronizacja | kanały z jednej maski START ruszają w tym samym takcie 20 ns |

**Wyjścia:** wszystkie kanały są cyfrowe – w każdym kroku wyjście ma stan 0 lub 1. Po zatrzymaniu
kanał przyjmuje zadany poziom spoczynkowy (idle 0/1); po ostatnim powtórzeniu może trzymać ostatni
krok albo wrócić do poziomu idle.

**Wyjścia synchronizujące:**
- **BASE_CLK** – zegar bazowy o wypełnieniu 50 %, okres ustawiany z PC (2 µs … 2,1 s, domyślnie 1 ms).
  Nadaje tylko wtedy, gdy pracuje dowolny kanał (w spoczynku = 0). Każdy START go restartuje i zaczyna od zbocza
  narastającego, więc jego zbocza wypadają na granicach kroków. Przy ponownym starcie w trakcie pracy BASE_CLK
  i SYNC schodzą na 1 takt (20 ns) do 0, żeby zbocze narastające zawsze wypadło w chwili wyzwolenia.
- **SYNC** – zbocze narastające dokładnie w chwili wyzwolenia sekwencji; impuls o zadanej szerokości
  (domyślnie 1 ms) albo – przy szerokości 0 – stan wysoki, dopóki pracuje dowolny kanał.

Kanały, BASE_CLK i SYNC mają identyczne opóźnienie potokowe, a ich ostatnie rejestry są w komórkach I/O
(FAST_OUTPUT_REGISTER), więc zbocza wypadają w tym samym takcie 50 MHz (rozrzut między pinami to pojedyncze ns).

**Start synchroniczny:** *Start enabled (synchronous)* wysyła jedną komendę START z maską wszystkich włączonych
kanałów, czyli jeden zapis do rejestru – wszystkie kanały z maski ruszają w tym samym takcie zegara. START restartuje
też podstawę czasu 1 µs, więc pierwszy krok trwa dokładnie zadany czas. Kanały uruchamiane osobno (*Start this
channel*) nie są ze sobą zsynchronizowane; START przesuwa też jednorazowo (< 1 µs) podstawę czasu kanałów już pracujących.

## Wyniki kompilacji (Quartus Prime Lite 25.1, sprawdzone)

| Zasób | Użycie |
|---|---|
| Logic elements | 4 547 / 8 064 (56 %) |
| Pamięć | 165 888 / 387 072 bit (43 %) – 16 × M9K na sekwencje + 16 KB RAM Nios |
| Timing @ 50 MHz | setup slack +7,7 ns, hold +0,29 ns (Slow 85 °C) |

Wyniki dla pinów własnej płytki (10M08SAE144C8G, 2026-11-22). Wszystkie 18 wyjść (CH0–15, BASE_CLK, SYNC) ma
ostatni rejestr w komórce I/O (`FAST_OUTPUT_REGISTER`).

Wariant z Nios V/m (debuger JTAG, `build_fpga.ps1 -Cpu niosv_m`) też jest dostępny – zajmuje ok. 1,5–2 tys. LE więcej.

> **Nios II vs Nios V:** Quartus 25.1 nie zawiera już rdzenia Nios II Gen2 w Platform Designerze,
> dlatego użyty jest jego następca Nios V (RISC-V). Architektura systemu jest taka sama.

## Struktura

```
quartus/
  ip/seq_gen16/seq_channel.vhd   blok kanału
  ip/seq_gen16/seq_gen16.vhd     16 kanałów + slave Avalon-MM (mapa rejestrów w nagłówku)
  ip/seq_gen16/seq_gen16_hw.tcl  komponent Platform Designer
  seqgen16_top.vhd               top level (POR, LED-y)
  create_system.tcl              tworzy nios_sys.qsys (Nios V + RAM + UART + generator)
  seqgen16.qsf / .sdc            projekt, piny, timing
  build_fpga.ps1                 pełna budowa
fw/            firmware Nios V (bare-metal C, ~3 KB): main.c, hw.h, crt0.S, link.ld, build.ps1
pc/            seqgen.py (biblioteka), seqgen_gui.py, seqgen_cli.py, patterns.py, emulator.py,
               fw_sim.py (symulator RV32I firmware), test_seqgen.py, test_firmware.py
sim/           tb_seq_gen16.vhd + run_sim.do / run_sim.ps1 (Questa) – testbench samosprawdzający
examples/      demo_16ch.json
pcb/seqgen16/  projekt KiCad własnej płytki (w toku): arkusze FPGA, USB, Power, Outputs
docs/          schemat blokowy (SVG/PNG), zrzuty ekranu aplikacji
CLAUDE.md      notatki dla asystenta AI (Claude Code) – stan projektu, decyzje, pułapki
```

## Budowanie

> Quartus nie obsługuje ścieżek > 260 znaków – skopiuj projekt do krótkiej ścieżki, np. `C:\proj\seqgen16`.

1. **Toolchain firmware** – zainstaluj darmowy *Ashling RiscFree IDE for Altera FPGAs*
   (zawiera `riscv32-unknown-elf-gcc`). Jeśli nie jest w `C:\altera_lite\25.1std\riscfree`,
   ustaw `RISCV_TOOLCHAIN` na katalog `bin`.
2. Firmware → `quartus\onchip_ram.hex`:
   ```powershell
   .\fw\build.ps1
   ```
3. FPGA (Platform Designer + kompilacja, ~3 min):
   ```powershell
   .\quartus\build_fpga.ps1
   ```
4. Programowanie: `output_files\seqgen16.sof` (SRAM, do testów) lub `seqgen16.pof` (flash, zostaje po
   wyłączeniu zasilania) przez USB-Blaster na złączu JTAG J10.
   Późniejsze zmiany samego firmware bez ponownej kompilacji FPGA: `.\fw\build.ps1 -UpdateSof`.

## Podłączenie

Przypisanie pinów dla **własnej płytki** (`pcb/seqgen16`). Nazwy w kolumnie „Sieć” to etykiety globalne na
schemacie (`fpga.kicad_sch`); przy zmianie pinów aktualizuj schemat i `quartus/seqgen16.qsf` razem.

| Sygnał | Pin FPGA | Sieć (KiCad) |
|---|---|---|
| clk_50 | 27 (CLK0p) | oscylator 50 MHz |
| reset_n (przycisk RESET, aktywny 0) | 121 (DEV_CLRn jako zwykłe wejście) | RESET_N |
| uart_txd (→ RX mostka USB) | 70 | TXD |
| uart_rxd (← TX mostka USB) | 69 | RXD |
| ch_out[0..7] | 41, 38, 43, 39, 46, 44, 47, 45 | CH0…CH7 |
| ch_out[8..15] | 55, 50, 56, 52, 59, 57, 60, 58 | CH8…CH15 |
| sync_out (SYNC) | 28 (CLK1n) | SYNC |
| clk_base (BASE_CLK) | 29 (CLK1p) | BASE_CLK |
| led_n[0] | 132 | LED1_N – LED1: heartbeat (~1,5 Hz) |
| led_n[1] | 134 | LED2_N – LED2: pracuje dowolny kanał |
| led_n[2..4] | 135, 140, 141 | na płytce niepodłączone – w FPGA na stałe wyłączone (stan 1) |

Kanały są w banku 3, UART w banku 4, SYNC/BASE_CLK w banku 2 (wszystkie VCCIO = 3,3 V).

**Reset:** wciśnięcie przycisku RESET (stan niski na `reset_n`) resetuje cały system – Nios (firmware startuje
od początku), UART i generator (kanały zatrzymane, wyjścia 0, konfiguracja domyślna, BASE_CLK/SYNC = 0).
Wejście jest synchronizowane, a reset trwa jeszcze 2,6 ms po zwolnieniu przycisku (odbicia styku są
filtrowane). Pin ma włączony wewnętrzny pull-up, więc niepodłączony nie blokuje pracy (np. na płytce
ewaluacyjnej). Logika: `quartus/reset_ctrl.vhd`, test: `sim/tb_reset_ctrl.vhd`.

**Płytka MAX 10 10M08 Evaluation Kit:** poprzednie przypisanie (UART na Arduino D0/D1, CH0–11 na Arduino D2–D13,
CH12–15 / BASE_CLK / SYNC na J8) jest w historii git – `git show 22c6dcf:quartus/seqgen16.qsf` i
`git show 22c6dcf:README.md`.

## Obsługa aplikacji PC (interfejs po angielsku)

```powershell
pip install pyserial
python pc\seqgen_gui.py                                    # GUI; port "EMU" = emulator bez sprzętu
```

![Okno główne aplikacji](docs/gui_main.png)

Okno składa się z czterech części:

| Obszar | Co zawiera |
|---|---|
| **Pasek górny** | wybór portu (`COMx` lub `EMU`), *Connect / Disconnect*, informacja o urządzeniu; *New / Open / Save / Save As* projektu |
| **Tabela kanałów (lewo)** | nazwa, włączenie (kliknij **On**), krok, liczba kroków, powtórzenia i stan na żywo (`RUN n` = aktualny krok, `DONE`, `stop`); *Enable all / Disable all*; panel **Sync outputs (J8)** z okresem BASE_CLK i szerokością SYNC; panel **Output level** (off / 1,8 … 5 V – wysyłany przy uploadzie i od razu po zmianie, gdy połączono) |
| **Edytor kanału (środek)** | ustawienia wybranego kanału i jego przebieg do rysowania myszą, narzędzia edycji i generatory |
| **Podgląd (dół)** | wszystkie 16 kanałów oraz BASE_CLK i SYNC na wspólnej osi czasu (t = 0 to chwila startu); zielone diody = bieżący stan wyjść; kliknięcie ścieżki wybiera kanał |

### Typowa praca

1. Wybierz port konwertera USB-UART (lub `EMU`, żeby pracować bez płytki) i kliknij **Connect**.
2. Wybierz kanał w tabeli, ustaw **Step** (z jednostką µs / ms / s), **Length**, **Repeat** i poziom **Idle**.
3. Narysuj przebieg w edytorze albo wygeneruj go (*Clock*, *Pulse*, *Random*) lub wczytaj (*Import file…*).
4. Włącz potrzebne kanały (kolumna **On**) i ustaw BASE_CLK / SYNC.
5. **Upload all enabled** – wysyła ustawienia i sekwencje oraz sprawdza je odczytem z urządzenia.
6. **▶ Start enabled (synchronous)** – wszystkie włączone kanały ruszają w tym samym takcie; **■ Stop all** zatrzymuje.
7. **Save** zapisuje cały projekt (JSON), **Export file…** – sekwencję jednego kanału (.txt / .csv).

### Edytor przebiegu

![Edytor przebiegu: zaznaczony zakres i znacznik bieżącego kroku](docs/gui_editor.png)

Niebieski obszar to zaznaczenie (tu kroki 20…35), czerwona linia – krok, który urządzenie właśnie odtwarza.
Nad wykresem: numery kroków i czas od początku sekwencji; przerywana linia na końcu – koniec sekwencji
i co dzieje się dalej (`loop`, `x3, then hold` itp.).

| Akcja | Jak |
|---|---|
| rysowanie stanu 1 / 0 | przeciągnij lewym / prawym przyciskiem myszy po krokach |
| zaznaczenie zakresu | Shift + przeciągnięcie (Esc – odznacz, Ctrl+A – wszystko) |
| przewijanie / powiększenie | kółko myszy / Ctrl + kółko, przyciski Zoom +/−, Fit |
| cofnij | Ctrl+Z lub *Undo* (200 kroków wstecz) |
| operacje na zaznaczeniu (lub całości) | *Set 1*, *Set 0*, *Invert*, *◀ Shift / Shift ▶* (rotacja), *Copy / Paste* |
| generatory | *Clock* (High/Low/Phase w krokach), *Pulse* (Start/Width), *Random* (gęstość) |
| import / eksport | *Import file…* / *Export file…*: tekst `0101…` (.txt) lub CSV `level,time_ms` (.csv) – eksport wczytuje się z powrotem bez zmian; przy imporcie CSV liczy się pierwsza kolumna (≠ 0 → 1), linie `#` to komentarze; *Paste text*, *Copy as text* |
| kopiowanie na inne kanały | *Copy to channels…* (opcjonalnie z ustawieniami czasu) |

Parametry kanału: nazwa, włączony, czas kroku (1 µs … 1 s, rozdzielczość 1 µs; pole z wyborem jednostki
µs / ms / s – tak samo dla BASE_CLK i SYNC), długość (1…1024 kroki), liczba powtórzeń (0 = w pętli),
poziom idle, zachowanie po ostatnim powtórzeniu (*hold last step* / *go to idle level*).

Projekt JSON (wersja 3): `clock_period_ms`, `sync_width_ms` oraz dla każdego kanału `name`, `enabled`, `period_ms`, `loops`, `idle`, `idle_on_done`,
`steps` (lista 0/1). Upload weryfikuje zawartość pamięci odczytem; pełne 16 × 1024 kroków ≈ 2 s przy 115200 bd.

### Linia poleceń (CLI)

```powershell
python pc\seqgen_cli.py ports
python pc\seqgen_cli.py -p COM5 upload examples\demo_16ch.json --start
python pc\seqgen_cli.py -p COM5 status
python pc\seqgen_cli.py -p COM5 stop all
python pc\seqgen_cli.py -p COM5 vout 3.3                  # poziom wyjść: off, 1.8, 2.5, 3.3, 4.0, 5.0 (bez wartości = odczyt)
python pc\seqgen_cli.py show examples\demo_16ch.json       # podgląd sekwencji jako 0/1
```

Testy: `cd pc ; python -m unittest test_seqgen test_firmware -v` (`test_firmware` uruchamia zbudowany `fw/build/fw.bin`).

## Stan weryfikacji

- ✅ Pełna kompilacja Quartus (synteza, fitter, timing, .sof/.pof) dla VHDL + Nios V/c.
- ✅ Firmware 2.2: zbudowany toolchainem RiscFree (GCC 13.2, RV32I) – 2,7 KB kodu + 2,3 KB danych w 16 KB RAM, wpisany do `seqgen16.sof/.pof`.
- ✅ Firmware wykonany w symulatorze zestawu instrukcji RV32I (`pc/fw_sim.py`) z modelami UART i rejestrów generatora: 18 testów (`pc/test_firmware.py`) przez prawdziwą bibliotekę PC – protokół, konfiguracja rejestrów, zapis/odczyt 1024 kroków, pętle, błędne CRC, śmieci na linii, nieznane komendy. Obsługa jednego odebranego bajtu to ~115 instrukcji, czyli kilkukrotny zapas względem 86,8 µs na bajt przy 115200 bd.
- ✅ Aplikacja PC: 40 testów (biblioteka na emulatorze + te same scenariusze na firmware, m.in. krok 1 µs, eksport/import)
  + automatyczny test GUI (rysowanie, zaznaczanie, generator, undo, włączanie kanałów w tabeli, pola czasu µs/ms/s,
  BASE_CLK/SYNC, eksport/import pliku, upload, start) i CLI na emulatorze.
- ✅ Symulacja RTL w Questa: `sim/run_sim.ps1` → **TEST PASSED** (zapis/odczyt 1024 kroków, stany i czasy kroków,
  pętle i zakończenie, synchronizacja kanałów co do taktu, STOP, poziom idle, krok 1 ms = dokładnie 50 000 taktów, krok 1 µs = 1000 ns,
  zbocza SYNC / BASE_CLK w tym samym czasie co zmiany kanałów – także przy ponownym starcie). Symulacja wykryła
  i pomogła usunąć błąd: BASE_CLK będący w stanie 1 w chwili START nie dawał zbocza narastającego.
- ⚠️ Nie testowano na sprzęcie.
