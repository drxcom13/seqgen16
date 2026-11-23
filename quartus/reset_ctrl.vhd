-- =============================================================================
-- reset_ctrl.vhd - reset systemu: power-on reset + wejscie reset_n (przycisk)
--
--  * reset_n_in jest asynchroniczny -> 2 przerzutniki synchronizujace,
--  * rst_n_out = 0 dopoki reset_n_in = 0 oraz przez 2**CNT_BITS taktow po zwolnieniu
--    (kazde odbicie styku zaczyna odliczanie od nowa = debouncing),
--  * po konfiguracji FPGA (wartosci poczatkowe) dziala jak power-on reset.
--  50 MHz, CNT_BITS = 17 -> 2,6 ms.
-- =============================================================================
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity reset_ctrl is
    generic (
        CNT_BITS : positive := 17
    );
    port (
        clk        : in  std_logic;
        reset_n_in : in  std_logic;     -- asynchroniczne, aktywne stanem niskim
        rst_n_out  : out std_logic      -- synchroniczne, aktywne stanem niskim
    );
end entity;

architecture rtl of reset_ctrl is
    signal meta : std_logic := '0';
    signal sync : std_logic := '0';
    signal cnt  : unsigned(CNT_BITS downto 0) := (others => '0');
    signal rst  : std_logic := '0';

    attribute altera_attribute : string;
    attribute altera_attribute of meta : signal is "-name SYNCHRONIZER_IDENTIFICATION FORCED_IF_ASYNCHRONOUS";
begin
    process (clk)
    begin
        if rising_edge(clk) then
            meta <= reset_n_in;
            sync <= meta;
            if sync = '0' then
                cnt <= (others => '0');
            elsif cnt(CNT_BITS) = '0' then
                cnt <= cnt + 1;
            end if;
            rst <= cnt(CNT_BITS);
        end if;
    end process;
    rst_n_out <= rst;
end architecture;
