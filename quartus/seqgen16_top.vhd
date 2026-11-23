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
--   kanaly zatrzymane, wyjscia = 0, konfiguracja domyslna, BASE_CLK / SYNC = 0.
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
            uart_rxd          : in  std_logic;
            uart_txd          : out std_logic
        );
    end component;

    signal rst_n    : std_logic;
    signal running  : std_logic_vector(15 downto 0);
    signal hb       : unsigned(24 downto 0) := (others => '0');

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
            uart_rxd          => uart_rxd,
            uart_txd          => uart_txd
        );

    -- Na plytce pcb/seqgen16 sa tylko LED1 (pin 132) i LED2 (pin 134), aktywne zerem.
    led_n(0) <= not hb(24);                                       -- LED1: heartbeat ~1.5 Hz
    led_n(1) <= '0' when running /= x"0000" else '1';             -- LED2: dowolny kanal pracuje
    -- led_n(2..4) (piny 135/140/141): na plytce niepodlaczone - na stale wylaczone
    -- (stan '1' gasi tez diody D3..D5 na plytce ewaluacyjnej).
    led_n(4 downto 2) <= (others => '1');

end architecture;
