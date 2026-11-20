/* =============================================================================
 * main.c - firmware Nios V: most RS-232 <-> 16-kanalowy generator sekwencji
 *
 * Protokol (opis pelny: PROTOCOL.md), wszystkie liczby little-endian:
 *   ramka:  A5 5A | CMD u8 | LEN u16 | PAYLOAD[LEN] | CRC16 u16
 *   CRC16-CCITT (poly 0x1021, init 0xFFFF) liczone z CMD, LEN, PAYLOAD
 *   odpowiedz: CMD|0x80, payload[0] = kod statusu, dalej dane
 * ========================================================================== */
#include <stdint.h>
#include "hw.h"

#define FW_MAJOR 2
#define FW_MINOR 1

#define SYNC0 0xA5
#define SYNC1 0x5A
#define MAX_PAYLOAD (5 + SG_MAXLEN)       /* WRITE: ch, off, cnt + 1024 probek */

enum { CMD_PING = 0x01, CMD_SET_CFG = 0x10, CMD_GET_CFG = 0x11,
       CMD_SET_AUX = 0x12, CMD_GET_AUX = 0x13,
       CMD_WRITE = 0x20, CMD_READ = 0x21,
       CMD_START = 0x30, CMD_STOP = 0x31, CMD_STATUS = 0x32,
       CMD_ERROR = 0x7F };

enum { ST_OK = 0, ST_BAD_CMD = 1, ST_BAD_LEN = 2, ST_BAD_ARG = 3, ST_BAD_CRC = 4 };

static uint8_t  rx[MAX_PAYLOAD];
static uint8_t  tx[MAX_PAYLOAD + 1];

/* kopia rejestrow konfiguracji (w sprzecie sa tylko do zapisu) */
static struct { uint32_t period, last, cfg; } shadow[SG_NCH];
static uint32_t aux_clk_period_us = 1000u;    /* zegar bazowy: okres [us] */
static uint32_t aux_sync_width_us = 1000u;    /* SYNC: szerokosc [us], 0 = dopoki pracuje kanal */

static void aux_apply(void)
{
    SG_BCLK_HALF = (aux_clk_period_us >> 1) - 1u;
    SG_SYNC_W    = aux_sync_width_us;
}

/* gcc moze wstawic wywolania memset/memcpy nawet w kodzie freestanding (-nostdlib) */
void *memset(void *s, int c, unsigned n)            { uint8_t *p = s; while (n--) *p++ = (uint8_t)c; return s; }
void *memcpy(void *d, const void *s, unsigned n)    { uint8_t *a = d; const uint8_t *b = s; while (n--) *a++ = *b++; return d; }

/* ------------------------------------------------------------------ narzedzia */
static uint16_t crc16_update(uint16_t crc, uint8_t b)
{
    crc ^= (uint16_t)b << 8;
    for (int i = 0; i < 8; i++)
        crc = (crc & 0x8000u) ? (uint16_t)((crc << 1) ^ 0x1021u) : (uint16_t)(crc << 1);
    return crc;
}

static uint16_t get16(const uint8_t *p) { return (uint16_t)(p[0] | (p[1] << 8)); }
static uint32_t get32(const uint8_t *p) { return p[0] | (p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24); }
static void put16(uint8_t *p, uint16_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); }
static void put32(uint8_t *p, uint32_t v) { put16(p, (uint16_t)v); put16(p + 2, (uint16_t)(v >> 16)); }


/* ------------------------------------------------------------------ UART */
static void uart_putc(uint8_t c)
{
    while (!(UART_STATUS & UART_ST_TRDY)) { }
    UART_TXDATA = c;
}

/* zwraca -1 gdy brak znaku */
static int uart_getc(void)
{
    uint32_t st = UART_STATUS;
    if (st & UART_ST_ROE) UART_STATUS = 0;        /* kasuj bledy */
    if (!(st & UART_ST_RRDY)) return -1;
    return (int)(UART_RXDATA & 0xFFu);
}

static void send_frame(uint8_t cmd, const uint8_t *payload, uint16_t len)
{
    uint16_t crc = 0xFFFF;
    uint8_t hdr[3] = { cmd, (uint8_t)len, (uint8_t)(len >> 8) };
    uart_putc(SYNC0);
    uart_putc(SYNC1);
    for (int i = 0; i < 3; i++) { uart_putc(hdr[i]); crc = crc16_update(crc, hdr[i]); }
    for (uint16_t i = 0; i < len; i++) { uart_putc(payload[i]); crc = crc16_update(crc, payload[i]); }
    uart_putc((uint8_t)crc);
    uart_putc((uint8_t)(crc >> 8));
}

static void reply(uint8_t cmd, uint8_t status, uint16_t datalen)
{
    tx[0] = status;
    send_frame((uint8_t)(cmd | 0x80u), tx, (uint16_t)(datalen + 1));
}

/* ------------------------------------------------------------------ komendy */
static void handle(uint8_t cmd, const uint8_t *p, uint16_t len)
{
    uint8_t *d = &tx[1];

    switch (cmd) {

    case CMD_PING:
        put32(d + 0, SG_ID);
        d[4] = FW_MAJOR;
        d[5] = FW_MINOR;
        d[6] = SG_NCH;
        put16(d + 7, SG_MAXLEN);
        put32(d + 9, SG_CLK_HZ);
        put32(d + 13, SG_PERIOD_MAX);
        reply(cmd, ST_OK, 17);
        return;

    case CMD_SET_CFG: {   /* ch u8, period_us u32, length u16, loops u16, idle u8, flags u8 */
        if (len != 11) break;
        uint8_t  ch     = p[0];
        uint32_t per_us = get32(p + 1);
        uint16_t length = get16(p + 5);
        uint16_t loops  = get16(p + 7);
        uint8_t  idle   = p[9];
        uint8_t  flags  = p[10];
        if (ch >= SG_NCH || per_us == 0 || length == 0 || length > SG_MAXLEN) { reply(cmd, ST_BAD_ARG, 0); return; }
        if (per_us > SG_PERIOD_MAX + 1u)                                     { reply(cmd, ST_BAD_ARG, 0); return; }
        shadow[ch].period = per_us - 1u;              /* rejestr: mikrosekundy - 1 */
        shadow[ch].last   = (uint32_t)(length - 1u);
        shadow[ch].cfg    = loops | (idle ? SG_CFG_IDLE : 0u)
                          | ((flags & 2u) ? SG_CFG_IDLE_ON_DONE : 0u);
        SG_PERIOD(ch) = shadow[ch].period;
        SG_LAST(ch)   = shadow[ch].last;
        SG_CFG(ch)    = shadow[ch].cfg;
        reply(cmd, ST_OK, 0);
        return;
    }

    case CMD_GET_CFG: {   /* ch u8 -> cfg (11 B jak SET_CFG) + status u32 */
        if (len != 1) break;
        uint8_t ch = p[0];
        if (ch >= SG_NCH) { reply(cmd, ST_BAD_ARG, 0); return; }
        uint32_t cfg = shadow[ch].cfg;
        d[0] = ch;
        put32(d + 1, shadow[ch].period + 1u);
        put16(d + 5, (uint16_t)(shadow[ch].last + 1u));
        put16(d + 7, (uint16_t)cfg);
        d[9]  = (cfg & SG_CFG_IDLE) ? 1u : 0u;
        d[10] = (cfg & SG_CFG_IDLE_ON_DONE) ? 2u : 0u;
        put32(d + 11, SG_STAT(ch));
        reply(cmd, ST_OK, 15);
        return;
    }

    case CMD_SET_AUX: {   /* clk_period_us u32 (parzysty, 2..2^21), sync_width_us u32 (0..2^20-1) */
        if (len != 8) break;
        uint32_t per = get32(p), sw = get32(p + 4);
        if (per < 2u || (per & 1u) || ((per >> 1) - 1u) > SG_PERIOD_MAX || sw > SG_PERIOD_MAX) {
            reply(cmd, ST_BAD_ARG, 0);
            return;
        }
        aux_clk_period_us = per;
        aux_sync_width_us = sw;
        aux_apply();
        reply(cmd, ST_OK, 0);
        return;
    }

    case CMD_GET_AUX:
        if (len != 0) break;
        put32(d + 0, aux_clk_period_us);
        put32(d + 4, aux_sync_width_us);
        reply(cmd, ST_OK, 8);
        return;

    case CMD_WRITE: {     /* ch u8, offset u16, count u16, data[count] */
        if (len < 5) break;
        uint8_t  ch  = p[0];
        uint16_t off = get16(p + 1);
        uint16_t cnt = get16(p + 3);
        if (len != (uint16_t)(5u + cnt)) break;
        if (ch >= SG_NCH || cnt == 0 || (uint32_t)off + cnt > SG_MAXLEN) { reply(cmd, ST_BAD_ARG, 0); return; }
        for (uint16_t i = 0; i < cnt; i++)
            SG_SAMPLE(ch, off + i) = p[5 + i] ? 1u : 0u;      /* krok = stan wyjscia 0/1 */
        reply(cmd, ST_OK, 0);
        return;
    }

    case CMD_READ: {      /* ch u8, offset u16, count u16 -> data[count] */
        if (len != 5) break;
        uint8_t  ch  = p[0];
        uint16_t off = get16(p + 1);
        uint16_t cnt = get16(p + 3);
        if (ch >= SG_NCH || cnt == 0 || (uint32_t)off + cnt > SG_MAXLEN) { reply(cmd, ST_BAD_ARG, 0); return; }
        for (uint16_t i = 0; i < cnt; i++)
            d[i] = (uint8_t)(SG_SAMPLE(ch, off + i) & 1u);
        reply(cmd, ST_OK, cnt);
        return;
    }

    case CMD_START:       /* mask u16 - wszystkie kanaly z maski startuja w tym samym takcie */
        if (len != 2) break;
        SG_START = get16(p);
        reply(cmd, ST_OK, 0);
        return;

    case CMD_STOP:
        if (len != 2) break;
        SG_STOP = get16(p);
        reply(cmd, ST_OK, 0);
        return;

    case CMD_STATUS: {    /* -> running u16, done u16, outputs u16, index[16] u16 */
        if (len != 0) break;
        uint16_t outs = 0;
        put16(d + 0, (uint16_t)SG_RUNNING);
        put16(d + 2, (uint16_t)SG_DONE);
        for (int ch = 0; ch < SG_NCH; ch++) {
            uint32_t st = SG_STAT(ch);
            if (st & (1u << 24)) outs |= (uint16_t)(1u << ch);
            put16(d + 6 + 2 * ch, (uint16_t)(st & 0x3FFu));
        }
        put16(d + 4, outs);
        reply(cmd, ST_OK, 6 + 2 * SG_NCH);
        return;
    }
        return;

    default:
        reply(cmd, ST_BAD_CMD, 0);
        return;
    }
    reply(cmd, ST_BAD_LEN, 0);
}

/* ------------------------------------------------------------------ parser ramek */
enum { P_SYNC0, P_SYNC1, P_CMD, P_LEN0, P_LEN1, P_DATA, P_CRC0, P_CRC1 };

#define RX_TIMEOUT_POLLS 2000000u   /* ~ kilkaset ms przerwy w ramce -> reset parsera */

int main(void)
{
    int      state = P_SYNC0;
    uint8_t  cmd = 0;
    uint16_t len = 0, pos = 0, crc = 0, rcrc = 0;
    uint32_t idle = 0;


    /* stan po resecie sprzetu: 1 ms, 1024 kroki, petla, idle 0 */
    for (int ch = 0; ch < SG_NCH; ch++) {
        shadow[ch].period = 999u;                 /* 1 ms */
        shadow[ch].last   = SG_MAXLEN - 1u;
        shadow[ch].cfg    = 0;
        SG_PERIOD(ch) = shadow[ch].period;
        SG_LAST(ch)   = shadow[ch].last;
        SG_CFG(ch)    = 0;
    }
    SG_STOP = 0xFFFFu;
    aux_apply();
    UART_STATUS = 0;

    for (;;) {
        int c = uart_getc();
        if (c < 0) {
            if (state != P_SYNC0 && ++idle > RX_TIMEOUT_POLLS) state = P_SYNC0;
            continue;
        }
        idle = 0;
        uint8_t b = (uint8_t)c;

        switch (state) {
        case P_SYNC0: if (b == SYNC0) state = P_SYNC1; break;
        case P_SYNC1: state = (b == SYNC1) ? P_CMD : (b == SYNC0 ? P_SYNC1 : P_SYNC0); break;
        case P_CMD:   cmd = b; crc = crc16_update(0xFFFF, b); state = P_LEN0; break;
        case P_LEN0:  len = b; crc = crc16_update(crc, b); state = P_LEN1; break;
        case P_LEN1:
            len |= (uint16_t)b << 8; crc = crc16_update(crc, b); pos = 0;
            if (len > MAX_PAYLOAD) { reply(CMD_ERROR, ST_BAD_LEN, 0); state = P_SYNC0; }
            else state = len ? P_DATA : P_CRC0;
            break;
        case P_DATA:
            rx[pos++] = b; crc = crc16_update(crc, b);
            if (pos == len) state = P_CRC0;
            break;
        case P_CRC0:  rcrc = b; state = P_CRC1; break;
        case P_CRC1:
            rcrc |= (uint16_t)b << 8;
            if (rcrc == crc) handle(cmd, rx, len);
            else             reply(cmd, ST_BAD_CRC, 0);
            state = P_SYNC0;
            break;
        }
    }
}
