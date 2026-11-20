-- =============================================================================
-- seqgen16_top.vhd - top level dla MAX 10 10M08 Evaluation Kit
--   Nios V/m + UART RS-232 + 16 blokow generatora sekwencji
-- =============================================================================
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity seqgen16_top is
    port (
        clk_50   : in  std_logic;                      -- 50 MHz
        uart_rxd : in  std_logic;                      -- z PC (konwerter RS-232 / USB-UART 3.3 V)
        uart_txd : out std_logic;                      -- do PC
        ch_out   : out std_logic_vector(15 downto 0);  -- wyjscia kanalow
        clk_base : out std_logic;                      -- zegar bazowy zsynchronizowany z wyjsciami
        sync_out : out std_logic;                      -- zbocze narastajace przy wyzwoleniu sekwencji
        led_n    : out std_logic_vector(4 downto 0)    -- LED-y plytki (aktywne zerem)
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

    signal por_cnt : unsigned(15 downto 0) := (others => '0');
    signal rst_n   : std_logic := '0';
    signal running : std_logic_vector(15 downto 0);
    signal hb      : unsigned(24 downto 0) := (others => '0');

begin

    -- power-on reset (~0.65 ms)
    p_por : process (clk_50)
    begin
        if rising_edge(clk_50) then
            if por_cnt(15) = '0' then
                por_cnt <= por_cnt + 1;
            end if;
            rst_n <= por_cnt(15);
            hb    <= hb + 1;
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

    led_n(0) <= not hb(24);                                       -- heartbeat ~1.5 Hz
    led_n(1) <= '0' when running /= x"0000" else '1';             -- dowolny kanal pracuje
    led_n(2) <= not running(0);
    led_n(3) <= not running(1);
    led_n(4) <= not running(2);

end architecture;
