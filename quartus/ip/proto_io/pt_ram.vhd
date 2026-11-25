-- =============================================================================
-- pt_ram.vhd - pamiec dwuportowa (zapis port A, odczyt port B, odczyt rejestrowany),
-- wzorzec rozpoznawany przez Quartus jako blok M9K (kolejki TX / RX w proto_io)
-- =============================================================================
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity pt_ram is
    generic (
        AW : positive := 9;
        DW : positive := 10
    );
    port (
        clk   : in  std_logic;
        we    : in  std_logic;
        waddr : in  unsigned(AW-1 downto 0);
        wdata : in  std_logic_vector(DW-1 downto 0);
        raddr : in  unsigned(AW-1 downto 0);
        q     : out std_logic_vector(DW-1 downto 0)
    );
end entity;

architecture rtl of pt_ram is
    type mem_t is array (0 to 2**AW - 1) of std_logic_vector(DW-1 downto 0);
    signal mem : mem_t;
    attribute ramstyle : string;
    attribute ramstyle of mem : signal is "M9K, no_rw_check";
begin
    process (clk)
    begin
        if rising_edge(clk) then
            if we = '1' then
                mem(to_integer(waddr)) <= wdata;
            end if;
            q <= mem(to_integer(raddr));
        end if;
    end process;
end architecture;
