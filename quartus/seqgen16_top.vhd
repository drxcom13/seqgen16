-- =============================================================================
-- seqgen16_top.vhd - top level (wlasna plytka pcb/seqgen16, MAX 10 10M08SAE144)
--   Nios V/c + UART + 16 blokow generatora sekwencji + BASE_CLK / SYNC
--
-- Reset systemu:
--   * power-on reset po wlaczeniu zasilania / konfiguracji,
--   * wejscie reset_n (przycisk RESET, aktywne stanem niskim, pin 121).
--   reset_n jest synchronizowany (2 przerzutniki) i "rozciagany": system pozostaje
--   w resecie, dopoki reset_n = 0, oraz jeszcze ~2,6 ms po jego zwolnieniu.
--   Kazde odbicie styku w tym czasie zaczyna odliczanie od nowa (debouncing).
--   Reset obejmuje Nios (firmware startuje od poczatku), UART i generator:
--   kanaly zatrzymane, wyjscia = 0, konfiguracja domyslna, BASE_CLK / SYNC = 0,
--   VCCB wylaczone (wyjscia buforow TXU w Hi-Z).
--
-- Poziom wyjsc (VCCB buforow, LDO U11/U12 na arkuszu Power):
--   vccb_off  (pin 61) = 1 -> LDO wylaczone (tez podczas konfiguracji - slabe pull-upy),
--   vsel_*_n  (piny 62/64/65) open-drain: '0' dolacza rezystor dzielnika FB, 'Z' = odlaczony.
--   brak sel -> 1,8 V, 2V5 -> 2,5 V, 3V3 -> 3,3 V, 2V5+3V3 -> 4,0 V, 5V -> ~VBUS.
--
-- Tester protokolow (zlacze J6, arkusz Protocol tester): translatory SN74LVC1T45, A = FPGA.
--   pt_sck / pt_mosi / pt_cs / pt_miso / pt_tx - dwukierunkowe: FPGA steruje pinem tylko gdy
--   odpowiedni pt_dir_* = 1 (translator FPGA -> J6), inaczej 'Z' (wejscie).
--   pt_rx, pt_sda_in, pt_scl_in - wejscia (translator J6 -> FPGA na stale).
--   pt_sda_drv / pt_scl_drv = 1 -> 2N7002 sciaga SDA / SCL do 0.
--   pvcc_off / psel_*_n - LDO U13 (PVCC), jak VCCB.
-- =============================================================================
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity seqgen16_top is
    port (
        clk_50   : in  std_logic;                      -- 50 MHz
        reset_n  : in  std_logic;                      -- przycisk RESET, aktywny stanem niskim
        uart_rxd : in  std_logic;                      -- z mostka USB-UART
        uart_txd : out std_logic;                      -- do mostka USB-UART
        ch_out   : out std_logic_vector(15 downto 0);  -- wyjscia kanalow
        clk_base : out std_logic;                      -- zegar bazowy zsynchronizowany z wyjsciami
        sync_out : out std_logic;                      -- zbocze narastajace przy wyzwoleniu sekwencji
        vccb_off   : out std_logic;                    -- 1 = LDO VCCB wylaczone (Q1 -> EN = 0)
        vsel_2v5_n : out std_logic;                    -- open-drain: '0' = 2,5 V
        vsel_3v3_n : out std_logic;                    -- open-drain: '0' = 3,3 V
        vsel_5v_n  : out std_logic;                    -- open-drain: '0' = ~5 V (LDO w dropout)
        -- tester protokolow (J6)
        pt_sck      : inout std_logic;
        pt_mosi     : inout std_logic;
        pt_cs       : inout std_logic;
        pt_miso     : inout std_logic;
        pt_tx       : inout std_logic;
        pt_rx       : in    std_logic;
        pt_sda_in   : in    std_logic;
        pt_scl_in   : in    std_logic;
        pt_dir_spi  : out   std_logic;                 -- SCK/MOSI/CS: 1 = FPGA -> J6
        pt_dir_miso : out   std_logic;                 -- MISO: 1 = FPGA -> J6
        pt_dir_tx   : out   std_logic;                 -- TX: 1 = FPGA -> J6
        pt_sda_drv  : out   std_logic;                 -- 1 = SDA w stanie niskim
        pt_scl_drv  : out   std_logic;                 -- 1 = SCL w stanie niskim
        pvcc_off    : out   std_logic;                 -- 1 = LDO PVCC wylaczone (Q2)
        psel_2v5_n  : out   std_logic;                 -- open-drain jak vsel_*
        psel_3v3_n  : out   std_logic;
        psel_5v_n   : out   std_logic;
        led_n    : out std_logic_vector(4 downto 0)    -- LED-y (aktywne zerem)
    );
end entity;

architecture rtl of seqgen16_top is

    component nios_sys is
        port (
            clk_clk           : in  std_logic;
            reset_reset_n     : in  std_logic;
            seqgen_ch_out     : out std_logic_vector(15 downto 0);
            seqgen_ch_running : out std_logic_vector(15 downto 0);
            seqgen_clk_base   : out std_logic;
            seqgen_sync_out   : out std_logic;
            seqgen_vccb_ctrl  : out std_logic_vector(3 downto 0);
            proto_sck_o       : out std_logic;
            proto_mosi_o      : out std_logic;
            proto_cs_o        : out std_logic;
            proto_miso_o      : out std_logic;
            proto_tx_o        : out std_logic;
            proto_dir_spi     : out std_logic;
            proto_dir_miso    : out std_logic;
            proto_dir_tx      : out std_logic;
            proto_sda_drv     : out std_logic;
            proto_scl_drv     : out std_logic;
            proto_pins_i      : in  std_logic_vector(7 downto 0);
            proto_pvcc_ctrl   : out std_logic_vector(3 downto 0);
            uart_rxd          : in  std_logic;
            uart_txd          : out std_logic
        );
    end component;

    signal rst_n    : std_logic;
    signal running  : std_logic_vector(15 downto 0);
    signal hb       : unsigned(24 downto 0) := (others => '0');
    signal vccb     : std_logic_vector(3 downto 0);
    signal pvcc     : std_logic_vector(3 downto 0);
    signal p_sck, p_mosi, p_cs, p_miso, p_tx : std_logic;
    signal d_spi, d_miso, d_tx                : std_logic;
    signal p_in     : std_logic_vector(7 downto 0);

begin

    u_rst : entity work.reset_ctrl
        generic map (CNT_BITS => 17)                                -- 2,6 ms przy 50 MHz
        port map (clk => clk_50, reset_n_in => reset_n, rst_n_out => rst_n);

    p_hb : process (clk_50)
    begin
        if rising_edge(clk_50) then
            hb <= hb + 1;
        end if;
    end process;

    u_sys : nios_sys
        port map (
            clk_clk           => clk_50,
            reset_reset_n     => rst_n,
            seqgen_ch_out     => ch_out,
            seqgen_ch_running => running,
            seqgen_clk_base   => clk_base,
            seqgen_sync_out   => sync_out,
            seqgen_vccb_ctrl  => vccb,
            proto_sck_o       => p_sck,
            proto_mosi_o      => p_mosi,
            proto_cs_o        => p_cs,
            proto_miso_o      => p_miso,
            proto_tx_o        => p_tx,
            proto_dir_spi     => d_spi,
            proto_dir_miso    => d_miso,
            proto_dir_tx      => d_tx,
            proto_sda_drv     => pt_sda_drv,
            proto_scl_drv     => pt_scl_drv,
            proto_pins_i      => p_in,
            proto_pvcc_ctrl   => pvcc,
            uart_rxd          => uart_rxd,
            uart_txd          => uart_txd
        );

    vccb_off   <= not vccb(0);
    vsel_2v5_n <= '0' when vccb(1) = '1' else 'Z';
    vsel_3v3_n <= '0' when vccb(2) = '1' else 'Z';
    vsel_5v_n  <= '0' when vccb(3) = '1' else 'Z';

    -- tester protokolow: kierunek pinow zgodny z kierunkiem translatorow
    pt_sck      <= p_sck  when d_spi  = '1' else 'Z';
    pt_mosi     <= p_mosi when d_spi  = '1' else 'Z';
    pt_cs       <= p_cs   when d_spi  = '1' else 'Z';
    pt_miso     <= p_miso when d_miso = '1' else 'Z';
    pt_tx       <= p_tx   when d_tx   = '1' else 'Z';
    pt_dir_spi  <= d_spi;
    pt_dir_miso <= d_miso;
    pt_dir_tx   <= d_tx;
    p_in        <= pt_scl_in & pt_sda_in & pt_rx & pt_tx & pt_miso & pt_cs & pt_mosi & pt_sck;
    pvcc_off    <= not pvcc(0);
    psel_2v5_n  <= '0' when pvcc(1) = '1' else 'Z';
    psel_3v3_n  <= '0' when pvcc(2) = '1' else 'Z';
    psel_5v_n   <= '0' when pvcc(3) = '1' else 'Z';

    -- Na plytce pcb/seqgen16 sa tylko LED1 (pin 132) i LED2 (pin 134), aktywne zerem.
    led_n(0) <= not hb(24);                                       -- LED1: heartbeat ~1.5 Hz
    led_n(1) <= '0' when running /= x"0000" else '1';             -- LED2: dowolny kanal pracuje
    -- led_n(2..4) (piny 135/140/141): na plytce niepodlaczone - na stale wylaczone
    -- (stan '1' gasi tez diody D3..D5 na plytce ewaluacyjnej).
    led_n(4 downto 2) <= (others => '1');

end architecture;
