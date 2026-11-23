-- =============================================================================
-- tb_reset_ctrl.vhd - test bloku reset_ctrl (power-on reset + przycisk reset_n)
--   CNT_BITS = 6 -> 64 takty, zeby symulacja byla krotka; logika jak dla 17.
-- =============================================================================
library ieee;
use ieee.std_logic_1164.all;

entity tb_reset_ctrl is
end entity;

architecture sim of tb_reset_ctrl is
    constant TCLK  : time := 20 ns;
    constant NBITS : positive := 6;
    constant HOLD  : natural := 2 ** NBITS;
    signal clk     : std_logic := '0';
    signal rn_in   : std_logic := '1';
    signal rn_out  : std_logic;
    signal errors  : integer := 0;
begin
    clk <= not clk after TCLK / 2;

    dut : entity work.reset_ctrl
        generic map (CNT_BITS => NBITS)
        port map (clk => clk, reset_n_in => rn_in, rst_n_out => rn_out);

    process
        procedure check(what : string; ok : boolean) is
        begin
            if not ok then
                report "BLAD " & what severity error;
                errors <= errors + 1;
                wait for 0 ns;
            end if;
        end procedure;

        procedure clocks(n : natural) is
        begin
            for i in 1 to n loop
                wait until rising_edge(clk);
            end loop;
            wait for 1 ns;
        end procedure;

        variable n : natural;
    begin
        -- 1. power-on reset: aktywny od startu, zwolniony po HOLD (+ synchronizator)
        wait for 1 ns;
        check("reset aktywny po starcie", rn_out = '0');
        clocks(HOLD - 2);
        check("power-on reset jeszcze aktywny przed HOLD", rn_out = '0');
        n := 0;
        while rn_out = '0' and n < 20 loop
            clocks(1); n := n + 1;
        end loop;
        check("power-on reset zwolniony po ~HOLD taktach", rn_out = '1' and n <= 8);

        -- 2. wcisniecie przycisku: reset w ciagu <= 4 taktow
        clocks(10);
        rn_in <= '0';
        clocks(4);
        check("reset po wcisnieciu (<= 4 takty)", rn_out = '0');

        -- 3. trzymanie: reset caly czas aktywny
        clocks(3 * HOLD);
        check("reset trzymany przy wcisnietym przycisku", rn_out = '0');

        -- 4. zwolnienie z odbiciami styku: kazde odbicie restartuje odliczanie
        rn_in <= '1'; clocks(HOLD / 2);
        rn_in <= '0'; clocks(2);                          -- odbicie
        rn_in <= '1'; clocks(HOLD / 2);
        check("odbicie styku przedluza reset", rn_out = '0');
        clocks(HOLD / 2 + 8);
        check("reset zwolniony po HOLD od ostatniego odbicia", rn_out = '1');

        -- 5. krotki impuls (1 takt) tez daje pelny reset
        clocks(10);
        rn_in <= '0'; wait until rising_edge(clk); wait for 1 ns;
        rn_in <= '1';
        clocks(4);
        check("krotki impuls daje reset", rn_out = '0');
        clocks(HOLD + 8);
        check("po krotkim impulsie reset zwolniony", rn_out = '1');

        if errors = 0 then
            report "=== TEST PASSED (reset_ctrl) ===" severity note;
        else
            report "=== TEST FAILED (reset_ctrl) ===" severity error;
        end if;
        std.env.finish;
    end process;
end architecture;
