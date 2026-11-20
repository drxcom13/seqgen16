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
| 0x20 | WRITE     | `ch u8`, `offset u16`, `count u16`, `data[count]` (1 bajt na krok, ≠0 → 1) | – |
| 0x21 | READ      | `ch u8`, `offset u16`, `count u16` | `data[count]` (0/1) |
| 0x30 | START     | `mask u16` | – (wszystkie kanały z maski startują w tym samym takcie zegara) |
| 0x31 | STOP      | `mask u16` | – (wyjście → wartość idle) |
| 0x32 | STATUS    | – | `running u16`, `done u16`, `outputs u16` (aktualne stany wyjść), `index[16] u16` |

`period_us`: 1 … 1 048 576 µs (wymaganie 1 ms … 1 s mieści się z zapasem; aplikacja PC pilnuje zakresu 1–1000 ms).
Krok: 1 bit – 0 = 0 V, 1 = VCCIO (3,3 V LVCMOS). Wersja firmware: 2.1.

START (dowolna maska) restartuje wspólną podstawę czasu 1 µs, zegar BASE_CLK (zaczyna od zbocza
narastającego) i impuls SYNC, więc ich zbocza wypadają w tym samym takcie co zmiany wyjść kanałów.
