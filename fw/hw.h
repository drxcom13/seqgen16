/* hw.h - mapa sprzetu (zgodna z quartus/create_system.tcl) */
#ifndef HW_H
#define HW_H
#include <stdint.h>

#define RAM_BASE      0x00000000u
#define RAM_SIZE      0x4000u
#define UART_BASE     0x00021000u
#define PROTO_BASE    0x00030000u
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
#define SG_VCCB             SG_WORD(0x0009)        /* [0]=en [1]=2V5 [2]=3V3 [3]=5V (zmiana: 2,5 ms przerwy) */
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

/* ---------------- proto_io - tester SPI / I2C / UART (patrz quartus/ip/proto_io/proto_io.vhd) */
#define PT_WORD(off)        REG32(PROTO_BASE + ((uint32_t)(off) << 2))
#define PT_ID               PT_WORD(0)
#define PT_CTRL             PT_WORD(1)            /* [2:0] tryb, [3] CPOL [4] CPHA [5] LSB [7:6] parz. [8] 2 stop [11:9] sdel */
#define PT_DIV              PT_WORD(2)
#define PT_TXD              PT_WORD(3)
#define PT_RXD              PT_WORD(4)            /* [8:0] wpis, [31] = byl wpis */
#define PT_STATUS           PT_WORD(5)            /* R: [9:0] TX [19:10] RX [20] zajety [21] przepeln. [22] blad I2C; W: [0] HOLD */
#define PT_PVCC             PT_WORD(6)            /* [0]=en [1]=2V5 [2]=3V3 [3]=5V */
#define PT_PINS             PT_WORD(7)            /* [7:0] linie J6, [31:8] licznik us */

#define PT_ID_VALUE         0x50544F31u
#define PT_FIFO             512u
#define PT_ST_TXCNT(s)      ((s) & 0x3FFu)
#define PT_ST_RXCNT(s)      (((s) >> 10) & 0x3FFu)
#define PT_ST_BUSY          (1u << 20)
#define PT_ST_RXOVF         (1u << 21)
#define PT_ST_BUSERR        (1u << 22)
#define PT_ST_TXFULL        (1u << 23)

#endif
