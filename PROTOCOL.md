# Protokół PC ↔ FPGA (RS-232, 115200 8N1, bez kontroli przepływu)

Wszystkie liczby wielobajtowe: **little-endian**.

## Ramka

| Pole    | Rozmiar | Opis |
|---------|---------|------|
| SYNC    | 2       | `A5 5A` |
| CMD     | 1       | kod komendy (odpowiedź: `CMD | 0x80`) |
| LEN     | 2       | długość PAYLOAD (max 1029) |
| PAYLOAD | LEN     | dane |
| CRC     | 2       | CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF) liczone z CMD, LEN, PAYLOAD |

Odpowiedź: `PAYLOAD[0]` = status, dalej dane.
Status: `0` OK, `1` nieznana komenda, `2` zła długość, `3` zły argument, `4` błąd CRC.
PC wysyła następną komendę dopiero po odpowiedzi (firmware działa na pollingu UART).
Przerwa > ~0,3 s w środku ramki resetuje parser.

## Komendy

| CMD  | Nazwa     | Payload żądania | Dane odpowiedzi |
|------|-----------|-----------------|-----------------|
| 0x01 | PING      | – | `id u32` (0x53513136), `fw_major u8`, `fw_minor u8`, `nch u8`, `max_len u16`, `clk_hz u32`, `period_max u32` (µs − 1) |
| 0x10 | SET_CFG   | `ch u8`, `period_us u32`, `length u16` (1..1024), `loops u16` (0 = ∞), `idle u8` (0/1), `flags u8` (bit1 = po końcu → idle, inaczej trzymaj ostatni krok) | – |
| 0x11 | GET_CFG   | `ch u8` | jak SET_CFG (11 B) + `status u32` (`[9:0]` krok, `[16]` run, `[17]` done, `[24]` stan wyjścia) |
| 0x12 | SET_AUX   | `clk_period_us u32` (parzysty, 2 … 2 097 152), `sync_width_us u32` (0 … 1 048 575; 0 = SYNC wysoki dopóki pracuje dowolny kanał) | – |
| 0x13 | GET_AUX   | – | `clk_period_us u32`, `sync_width_us u32` |
| 0x14 | SET_VOUT  | `level u8`: 0 = wyłączone (wyjścia Hi-Z), 1 = 1,8 V, 2 = 2,5 V, 3 = 3,3 V, 4 = 4,0 V, 5 = 5 V (≈ VBUS) | – |
| 0x15 | GET_VOUT  | – | `level u8` |
| 0x20 | WRITE     | `ch u8`, `offset u16`, `count u16`, `data[count]` (1 bajt na krok, ≠0 → 1) | – |
| 0x21 | READ      | `ch u8`, `offset u16`, `count u16` | `data[count]` (0/1) |
| 0x30 | START     | `mask u16` | – (wszystkie kanały z maski startują w tym samym takcie zegara) |
| 0x31 | STOP      | `mask u16` | – (wyjście → wartość idle) |
| 0x32 | STATUS    | – | `running u16`, `done u16`, `outputs u16` (aktualne stany wyjść), `index[16] u16` |
| 0x40 | PT_CONFIG | `ctrl u16` (rejestr CTRL testera), `div u32` | – (kasuje kolejki testera) |
| 0x41 | PT_GET_CONFIG | – | `ctrl u16`, `div u32`, `level u8` |
| 0x42 | PT_LEVEL  | `level u8` (kody jak SET_VOUT) – napięcie logiki złącza J6 (PVCC) | – |
| 0x43 | PT_XFER   | `flags u8` (bit0 = odrzuć stare dane RX), `wait_ms u16` (0…5000), `count u16` (≤ 512), `entry[count] u16` | `flags u8` (bit0 przepełnienie RX, bit1 błąd magistrali I2C, bit2 timeout 1 s), `count u16`, `entry[count] u16` |
| 0x44 | PT_READ   | `max u16` | jak PT_XFER – wpisy odebrane od ostatniej transakcji (np. UART) |
| 0x45 | PT_PINS   | – | `pins u8`: bity 0..7 = SCK, MOSI, CS, MISO, TX, RX, SDA, SCL |

`period_us`: 1 … 1 048 576 µs (wymaganie 1 ms … 1 s mieści się z zapasem; aplikacja PC pilnuje zakresu 1–1000 ms).
Krok: 1 bit – 0 = 0 V, 1 = poziom wyjść VCCB ustawiony komendą SET_VOUT. Wersja firmware: 2.3.

SET_VOUT steruje stabilizatorami LDO zasilającymi bufory wyjściowe (VCCB, wszystkie wyjścia jednakowo:
CH0..15, BASE_CLK, SYNC). Po włączeniu zasilania i po resecie poziom = 0 (wyjścia w Hi-Z) – PC musi go
ustawić. Każda zmiana poziomu wyłącza LDO na 2,5 ms (rozładowanie wyjścia), dopiero potem włącza nowe
napięcie; ten sam poziom wysłany ponownie niczego nie zmienia.

START (dowolna maska) restartuje wspólną podstawę czasu 1 µs, zegar BASE_CLK (zaczyna od zbocza
narastającego) i impuls SYNC, więc ich zbocza wypadają w tym samym takcie co zmiany wyjść kanałów.

## Tester protokołów (złącze J6, firmware 2.3)

Moduł `quartus/ip/proto_io` (adres 0x0003_0000) z kolejkami TX (512 wpisów, 10 bit) i RX (512 wpisów, 9 bit).
Tryby (CTRL `[2:0]`): 0 = wyłączony (wszystkie linie J6 wejściami), 1 = SPI master, 2 = I2C master, 3 = UART.
Tryby slave i podsłuch magistrali są przewidziane w kolejnych etapach (kody 4…7 zarezerwowane).

| CTRL | Znaczenie |
|---|---|
| `[2:0]` | tryb |
| `[3]` / `[4]` | CPOL / CPHA (SPI) |
| `[5]` | SPI: najpierw bit najmłodszy (LSB first) |
| `[7:6]` | UART: parzystość 0 = brak, 1 = parzysta, 2 = nieparzysta |
| `[8]` | UART: 2 bity stopu |
| `[11:9]` | SPI: opóźnienie próbkowania MISO w taktach 20 ns (0…7, domyślnie 1) – kompensuje translatory i kabel |

`div`: SPI – połowa okresu SCK w taktach − 1 (SCK = 50 MHz / (2·(div+1)), maks. 12,5 MHz przy div = 1);
I2C – jednostka czasu bitu w taktach − 1, SCL = 50 MHz / (5 × (DIV + 1)), stan niski 3/5 okresu (100 kHz: 99, 400 kHz: 24, 1 MHz: 9); UART – przyrost akumulatora
fazy = round(16 · baud · 2³² / 50 MHz) (maks. 3,125 Mbd, błąd prędkości < 0,01 %).

Wpisy TX (`entry`):

| Tryb | Wpis | Odpowiedź (wpis RX) |
|---|---|---|
| SPI | `[7:0]` bajt, `[8]` = ostatni bajt (po nim CS = 1) | bajt odczytany z MISO |
| I2C | `0x000`–`0x0FF` zapis bajtu | `[8]` = 1: NACK |
| I2C | `0x100` odczyt z ACK, `0x200` odczyt z NACK | odczytany bajt |
| I2C | `0x301` START (lub powtórzony START), `0x302` STOP | – |
| UART | `[7:0]` bajt | każdy odebrany bajt: `[7:0]`, `[8]` = błąd ramki lub parzystości |

PT_XFER ładuje całą transakcję do kolejki przy wstrzymanym silniku (HOLD), więc bajty idą bez przerw,
czeka na koniec (maks. 1 s), potem jeszcze `wait_ms` zbiera dane z RX (odpowiedzi UART) i zwraca wszystkie
wpisy RX. SPI: transakcja dłuższa niż 512 bajtów = kilka PT_XFER, tylko ostatni bajt ma `[8]`, CS pozostaje
w stanie niskim między nimi. PT_LEVEL działa jak SET_VOUT (zmiana = 2,5 ms wyłączenia). Po włączeniu
i po resecie poziom J6 = 0: translatory nie sterują linii J6.
