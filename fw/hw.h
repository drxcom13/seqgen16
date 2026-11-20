/* hw.h - mapa sprzetu (zgodna z quartus/create_system.tcl) */
#ifndef HW_H
#define HW_H
#include <stdint.h>

#define RAM_BASE      0x00000000u
#define RAM_SIZE      0x4000u
#define UART_BASE     0x00021000u
#define SEQGEN_BASE   0x00040000u

#define REG32(addr)   (*(volatile uint32_t *)(uintptr_t)(addr))

/* ---------------- altera_avalon_uart (RS-232 Serial Port) */
#define UART_RXDATA   REG32(UART_BASE + 0x00)
#define UART_TXDATA   REG32(UART_BASE + 0x04)
#define UART_STATUS   REG32(UART_BASE + 0x08)
#define UART_CONTROL  REG32(UART_BASE + 0x0C)
#define UART_ST_ROE   (1u << 3)
#define UART_ST_TRDY  (1u << 6)
#define UART_ST_RRDY  (1u << 7)

/* ---------------- seq_gen16 (patrz quartus/ip/seq_gen16/seq_gen16.v) */
#define SG_WORD(off)        REG32(SEQGEN_BASE + ((uint32_t)(off) << 2))
#define SG_ID               SG_WORD(0x0000)
#define SG_START            SG_WORD(0x0001)
#define SG_STOP             SG_WORD(0x0002)
#define SG_RUNNING          SG_WORD(0x0003)
#define SG_DONE             SG_WORD(0x0004)
#define SG_CLK_HZ           SG_WORD(0x0005)
#define SG_PARAMS           SG_WORD(0x0006)
#define SG_BCLK_HALF        SG_WORD(0x0007)        /* polowa okresu zegara bazowego [us] - 1 */
#define SG_SYNC_W           SG_WORD(0x0008)        /* szerokosc SYNC [us], 0 = dopoki pracuje kanal */
#define SG_PERIOD(ch)       SG_WORD(0x0040 + (ch) * 4 + 0)
#define SG_LAST(ch)         SG_WORD(0x0040 + (ch) * 4 + 1)
#define SG_CFG(ch)          SG_WORD(0x0040 + (ch) * 4 + 2)
#define SG_STAT(ch)         SG_WORD(0x0040 + (ch) * 4 + 3)
#define SG_SAMPLE(ch, i)    SG_WORD(0x4000 + (ch) * 1024 + (i))

#define SG_ID_VALUE         0x53513136u
#define SG_NCH              16
#define SG_MAXLEN           1024
#define SG_PERIOD_MAX       ((1u << 20) - 1u)       /* mikrosekundy - 1 (1.048 s) */

#define SG_CFG_IDLE         (1u << 16)
#define SG_CFG_IDLE_ON_DONE (1u << 25)

#endif
